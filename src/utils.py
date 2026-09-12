from PIL import Image, UnidentifiedImageError
import subprocess
import os
import io
import logging
import logging.handlers
import re
import sys
import json
import shutil
import threading

from platform_utils import _startupinfo, log_dir

# Constants and initialization
TONEMAP = ["Reinhard", "Mobius", "Hable", "BT.2390", "Spline"]
# npl=100 is the SDR reference white (100 nits) -- correct target for SDR displays.
#
# Final zscale omits p=bt709 on purpose: it leaves primaries tagged bt2020,
# and lut3d does the actual gamut correction (src/luts/rec2020_to_rec709.cube,
# tools/generate_lut.py); setparams then retags to bt709 (metadata only,
# confirmed via ffprobe both ways).
#
# interp=tetrahedral: the gamut correction clamps hard at the BT.709
# boundary; trilinear (lut3d's default) rounds off that kink, tetrahedral
# measurably doesn't (confirmed via real-ffmpeg pixel comparison).
FFMPEG_CONVERT_FILTER = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv,'
    'lut3d=file={lut_path}:interp=tetrahedral,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709,'
    'eq=gamma={gamma}'
)

# Preview chain = export chain + a downscale, derived so the two can't drift apart.
FFMPEG_FILTER = (
    FFMPEG_CONVERT_FILTER
    + ',scale={width}:{height}:force_original_aspect_ratio=decrease'
)

# Zscale-only gamut correction (no LUT) for the CPU preview path when
# lut_export_var is off (see _effective_lut_enabled in preview.py). Real
# export always applies the LUT regardless -- never uses this.
FFMPEG_FILTER_LEGACY_NO_LUT = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv:p=bt709,'
    'eq=gamma={gamma},scale={width}:{height}:force_original_aspect_ratio=decrease'
)

# Tonemappers with no zscale/CPU implementation (confirmed via `ffmpeg -h
# filter=tonemap`) -- require the GPU/libplacebo path. Lowercase to match libplacebo.
GPU_ONLY_TONEMAPPERS = {'bt.2390', 'spline'}


def is_gpu_only_tonemapper(tonemapper: str) -> bool:
    """True for tonemapping algorithms only implemented on the GPU (libplacebo)
    path -- FFMPEG_CONVERT_FILTER (CPU) has no equivalent. Case-insensitive."""
    return tonemapper.lower() in GPU_ONLY_TONEMAPPERS


# Shared "All Video Files" filter for both Browse (gui.py) and batch-add
# (batch.py) dialogs, so the extension list can't drift apart between them.
VIDEO_FILE_FILTER = ("All Video Files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v")


def parse_drop_paths(data: str) -> list:  # type: ignore[type-arg]
    """Split a tkdnd drop payload into individual file paths.

    Pure string parsing, no licensing logic -- shared by gui.py's Community
    Edition fallback and Pro's real _BatchMixin (pro/batch.py) so there's one
    implementation instead of two kept in sync by hand.
    """
    tokens = re.findall(r'\{[^}]*\}|\S+', data or '')
    return [t.strip('{}') for t in tokens if t.strip('{}')]

# Flags creating the Vulkan device libplacebo runs on; prepended before -i
# when the GPU tonemap path is active (CPU decode fallback).
VULKAN_DEVICE_ARGS = ['-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk']

# NVIDIA fast path: CUDA device for NVDEC decode, linked to Vulkan so
# libplacebo consumes CUDA frames via hwmap with no CPU round-trip.
VULKAN_CUDA_DEVICE_ARGS = [
    '-init_hw_device', 'cuda=cu:0',
    '-init_hw_device', 'vulkan=vk@cu',
    '-hwaccel', 'cuda',
    '-hwaccel_output_format', 'cuda',
    '-filter_hw_device', 'vk',
]

FFMPEG_EXECUTABLE = None
FFPROBE_EXECUTABLE = None

# Initialize logging
def _log_file_path() -> str:
    """Where the app's log file lives -- see platform_utils.log_dir() for
    the per-OS directory. A windowed/onedir build has no console for
    stderr to reach, so without this file warnings (e.g. a failed update
    check) were invisible."""
    return os.path.join(log_dir(), 'app.log')


def setup_logging():
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_path = _log_file_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))
        handlers.append(file_handler)
    except OSError:
        # Importing this module must never crash the app -- fall back to
        # console-only logging if the log directory can't be created/written.
        pass
    logging.basicConfig(
        level=logging.WARNING, format='%(levelname)s - %(message)s',
        handlers=handlers, force=True,
    )

# Initialize FFmpeg paths
def get_executable_path(filename):
    """Helper function to get the correct path for bundled executables"""
    try:
        if getattr(sys, 'frozen', False):
            # PyInstaller always sets _MEIPASS alongside frozen; the fallback is
            # belt-and-braces so a missing one degrades instead of crashing at import.
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
        # Derive a platform-agnostic base name, then add the correct suffix for disk
        # access.  Callers may pass either 'ffmpeg' or 'ffmpeg.exe' — both work.
        base = filename[:-4] if filename.endswith('.exe') else filename
        disk_name = base + '.exe' if sys.platform == 'win32' else base

        executable = os.path.normpath(os.path.join(base_path, disk_name))
        logging.debug(f"Looking for {disk_name} at: {executable}")

        if not os.path.exists(executable):
            if getattr(sys, 'frozen', False):
                raise FileNotFoundError(f"{base} not found in bundled application")
            system_exec = shutil.which(disk_name)
            if system_exec:
                executable = system_exec
                logging.debug(f"Found {disk_name} in system PATH: {executable}")
            else:
                raise FileNotFoundError(f"{base} not found in bundle or system PATH")

        return executable

    except Exception as e:
        logging.error(f"Error finding {filename}: {str(e)}")
        raise


def get_resource_path(relative_path: str) -> str:
    """Resolve the absolute path to a bundled data file (not an executable --
    no .exe suffix handling, no system-PATH fallback; a bundled asset that
    isn't found next to the app is a broken install, not something to search
    for elsewhere). Mirrors get_executable_path's base_path resolution."""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(os.path.join(base_path, relative_path))
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required bundled file not found: {relative_path} (looked at {path})")
    return path


def _escape_path_for_filter(path: str) -> str:
    """Escape an absolute Windows path for embedding in an ffmpeg -vf
    filtergraph value (lut3d=file=..., libplacebo's lut=...).

    ffmpeg's parser needs filtergraph-special characters escaped with a
    backslash. Convert Windows separators first, then escape colons,
    apostrophes, commas, semicolons, and brackets. Targets literal '\\', not
    os.sep, since CI also runs this on Linux."""
    forward = path.replace('\\', '/')
    escaped = []
    for char in forward:
        if char == ':':
            escaped.append('\\\\:')
        elif char in "'[,;]":
            escaped.append('\\' + char)
        else:
            escaped.append(char)
    return ''.join(escaped)


_LUT_FILTER_PATH = None


def get_lut_filter_path() -> str:
    """Resolve and cache the bundled Rec.2020->Rec.709 LUT's path, pre-escaped
    for embedding in FFMPEG_FILTER / build_libplacebo_filter's lut3d=/lut=.

    Raises FileNotFoundError if the bundled .cube file is missing -- a
    broken install should fail hard, not degrade silently."""
    global _LUT_FILTER_PATH
    if _LUT_FILTER_PATH is not None:
        return _LUT_FILTER_PATH
    raw_path = get_resource_path(os.path.join('luts', 'rec2020_to_rec709.cube'))
    _LUT_FILTER_PATH = _escape_path_for_filter(raw_path)
    return _LUT_FILTER_PATH

def verify_ffmpeg_files():
    """Locate ffmpeg/ffprobe and publish their paths as module globals."""
    global FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
    # Resolve both before publishing either -- no half-initialized pair.
    found = {name: get_executable_path(name) for name in ('ffmpeg', 'ffprobe')}
    FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE = found['ffmpeg'], found['ffprobe']
    return found

def initialize_ffmpeg():
    """Initialize FFmpeg executables and configure the environment."""
    try:
        verify_ffmpeg_files()
    except Exception as e:
        # Surfacing the error is the caller's job (GUI shows it on startup).
        logging.error(f"Error setting up ffmpeg: {str(e)}", exc_info=True)
        raise

setup_logging()
try:
    initialize_ffmpeg()
except Exception:
    # Import must never crash/block on a dialog -- FFMPEG_EXECUTABLE stays
    # None and the GUI reports it on startup (see HDRConverterGUI.__init__).
    logging.error("ffmpeg could not be initialized at import time", exc_info=True)


def run_ffmpeg_command(cmd):
    """Run an FFmpeg command with proper path handling"""
    startupinfo, creationflags = _startupinfo()

    cmd[0] = FFMPEG_EXECUTABLE

    # Normalize path-like args to the native separator, but not the arg right
    # after -vf: that's a filtergraph string that may hold a pre-escaped LUT
    # path (see _escape_path_for_filter) which normpath would corrupt.
    cmd = [
        str(arg) if (i > 0 and cmd[i - 1] == '-vf') or os.path.sep not in str(arg)
        else os.path.normpath(str(arg))
        for i, arg in enumerate(cmd)
    ]
    
    logging.debug(f"Running ffmpeg command: {' '.join(cmd)}")
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=creationflags
        )
        
        out, err = process.communicate()
        
        if process.returncode != 0:
            error_msg = err.decode('utf-8', errors='replace')
            logging.error(f"FFmpeg error: {error_msg}")
            if "no path between colorspaces" in error_msg:
                raise RuntimeError("There was an error importing this video. Colorspace mismatch.")
            raise RuntimeError(f"FFmpeg error: {error_msg}")
        
        return out
        
    except Exception as e:
        logging.error(f"Error running FFmpeg command: {str(e)}")
        raise RuntimeError(f"Error running FFmpeg command: {str(e)}")

# HDR metadata (MaxCLL) is static per file and costs ~0.5-1.2s to probe.
# Cache the dict so all callers share one ffprobe hit.
_HDR_METADATA_CACHE: dict[str, dict] = {}
_HDR_METADATA_CACHE_LOCK = threading.Lock()

# Video properties (streams, duration, codec) are also static per file; caching
# eliminates the extra ffprobe spawned inside each extract_frame / extract_frame_with_conversion call.
_VIDEO_PROPS_CACHE: dict[str, dict] = {}
_VIDEO_PROPS_CACHE_LOCK = threading.Lock()


def clear_hdr_metadata_cache():
    """Drop cached HDR metadata and video properties (call when loading a new/replaced file)."""
    with _HDR_METADATA_CACHE_LOCK:
        _HDR_METADATA_CACHE.clear()
    with _VIDEO_PROPS_CACHE_LOCK:
        _VIDEO_PROPS_CACHE.clear()


def _probe_hdr_metadata(video_path):
    """Probe MaxCLL from the first frame (uncached).

    Returns:
        dict with key 'maxcll' (float|None).
    """
    cmd = [
        FFPROBE_EXECUTABLE,
        '-v', 'quiet',
        '-select_streams', 'v:0',
        '-show_frames',
        '-read_intervals', '%+1',
        '-print_format', 'json',
        video_path
    ]

    startupinfo, creationflags = _startupinfo()
    result: dict = {'maxcll': None}

    try:
        out = subprocess.check_output(
            cmd,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=creationflags
        )
        data = json.loads(out.decode('utf-8'))
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError) as e:
        # Degrade to "no HDR metadata" (e.g. truncated data) instead of raising.
        logging.error(f"Error probing HDR metadata for {video_path}: {e}")
        return result
    for frame in data.get('frames', []):
        for sd in frame.get('side_data_list', []):
            if sd.get('side_data_type') == 'Content light level metadata':
                mc = sd.get('max_content')  # 'if mc:' would drop a legitimate 0
                if mc is not None:
                    result['maxcll'] = float(mc)
    return result


def _get_hdr_metadata(video_path):
    """Thread-safe cached wrapper around _probe_hdr_metadata."""
    if video_path in _HDR_METADATA_CACHE:
        return _HDR_METADATA_CACHE[video_path]
    with _HDR_METADATA_CACHE_LOCK:
        if video_path in _HDR_METADATA_CACHE:
            return _HDR_METADATA_CACHE[video_path]
        meta = _probe_hdr_metadata(video_path)
        _HDR_METADATA_CACHE[video_path] = meta
        return meta


def get_maxcll(video_path):
    """Return MaxCLL (peak pixel luminance) for display; None if not embedded."""
    return _get_hdr_metadata(video_path)['maxcll']


def build_libplacebo_filter(gamma, tonemapper, width: 'int | str' = 'iw',
                            height: 'int | str' = 'ih',
                            cuda_input: bool = False,
                            lut_enabled: bool = True,
                            bit_depth: int = 8) -> str:
    """Build the GPU tonemapping filter chain (HDR->SDR) using libplacebo.

    Always peak_detect=1. cuda_input=False uploads from system RAM
    (format=p010,hwupload); cuda_input=True arrives via NVDEC in CUDA memory
    and hwmap=derive_device=vulkan transfers it with no CPU round-trip.
    For gamma=1.0 + CUDA interop the frame never touches the CPU; otherwise
    we download for the eq filter (ffmpeg has no GPU-native gamma outside
    libplacebo).

    lut_enabled: applies the same BT.2020->BT.709 3D LUT the CPU path uses.
        libplacebo's native lut=/lut_type= can't reproduce lut3d's
        semantics -- measured against all 4 lut_type x primaries
        combinations, none matched (see gpu-lut-libplacebo-native-broken
        notes; root cause is renderer.c's custom-LUT hook running before
        tonemap/gamut-conversion, with no post-tonemap hook available). So
        this downloads and runs the identical CPU lut3d filter instead
        (verified pixel-identical). Real cost: ~2.1-2.4x slower GPU exports
        at 4K, since it disables the CUDA zero-copy fast path.
        lut_enabled=False restores that fast path.
    bit_depth: requested encoder depth. The 10-bit GPU path retains precision
        through a 16-bit RGB LUT intermediate before returning to p010le.
    """
    tm = tonemapper.lower()
    prefix = ('hwmap=derive_device=vulkan,'
              if cuda_input else 'format=p010,hwupload,')
    # LUT on: keep source primaries (auto) and let lut3d below do gamut correction.
    primaries = 'auto' if lut_enabled else 'bt709'
    # lut3d only accepts RGB formats; nv12 triggers a hidden auto_scale to
    # rgb24 (confirmed via -loglevel verbose). Downloading as rgba when the LUT
    # runs skips that hidden conversion -- measured ~5% faster over 300
    # synthetic 4K frames. Ten-bit output needs rgba64le for that stage, then
    # converts explicitly to p010le after all CPU color work. Without the LUT,
    # the encoder receives the frame directly in its matching YUV format.
    ten_bit_output = bit_depth == 10
    download_fmt = ('rgba64le' if ten_bit_output else 'rgba') if lut_enabled else (
        'p010le' if ten_bit_output else 'nv12')
    libplacebo = (
        f'libplacebo=w={width}:h={height}:tonemapping={tm}:'
        f'colorspace=bt709:color_primaries={primaries}:color_trc=bt709:range=tv:'
        f'peak_detect=1:format={download_fmt}'
    )
    gamma_is_identity = abs(gamma - 1.0) < 1e-9
    if lut_enabled:
        # lut3d is CPU-only, so the frame must come down regardless of cuda_input.
        lut_stage = (f'lut3d=file={get_lut_filter_path()}:interp=tetrahedral,'
                     f'setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709')
        if gamma_is_identity:
            suffix = f',hwdownload,format={download_fmt},{lut_stage}'
        else:
            suffix = f',hwdownload,format={download_fmt},{lut_stage},eq=gamma={gamma}'
        if ten_bit_output:
            suffix += ',format=p010le'
    elif cuda_input and gamma_is_identity:
        # Fully-GPU: remap Vulkan->CUDA after libplacebo, NVENC encodes directly.
        suffix = ',hwmap=reverse=1:derive_device=cuda'
    elif gamma_is_identity:
        suffix = f',hwdownload,format={download_fmt}'  # skip the no-op eq=gamma=1
    else:
        suffix = f',hwdownload,format={download_fmt},eq=gamma={gamma}'
    return f'{prefix}{libplacebo}{suffix}'

# None = not yet probed.
_libplacebo_available = None
_cuda_interop_available = None


def reset_libplacebo_probe():
    """Forget the cached probe result (used by tests)."""
    global _libplacebo_available
    _libplacebo_available = None


def reset_cuda_interop_probe():
    """Forget the cached CUDA interop probe result (used by tests)."""
    global _cuda_interop_available
    _cuda_interop_available = None

# Ceiling for the startup GPU probe below. Runs between main.pyw's
# root.withdraw()/deiconify(), so an unbounded wait is a hang with no window.
# Measured ~1s on a healthy machine; 20s margins a cold driver/shader
# compile while still bounding a wedged one. Erring generous on purpose --
# a false negative silently costs that customer GPU tonemapping.
_GPU_PROBE_TIMEOUT = 20


def vulkan_libplacebo_available():
    """Return True if this ffmpeg can tonemap on the GPU via Vulkan + libplacebo.

    Probes once and caches the result via a real filter chain on a tiny
    synthetic frame; any failure falls back to the CPU tonemap path."""
    global _libplacebo_available
    if _libplacebo_available is not None:
        return _libplacebo_available

    if not FFMPEG_EXECUTABLE:
        _libplacebo_available = False
        return False

    startupinfo, creationflags = _startupinfo()

    cmd = [
        FFMPEG_EXECUTABLE, '-loglevel', 'error',
        '-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk',
        '-f', 'lavfi', '-i', 'color=c=black:s=64x64,format=p010',
        '-vf', 'hwupload,libplacebo=tonemapping=clip:format=nv12,hwdownload,format=nv12',
        '-frames:v', '1', '-f', 'null', '-',
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=startupinfo, creationflags=creationflags,
            timeout=_GPU_PROBE_TIMEOUT,
        )
        _libplacebo_available = (result.returncode == 0)
    except subprocess.TimeoutExpired:
        # Separate clause: TimeoutExpired subclasses SubprocessError, not OSError.
        logging.warning(
            f"libplacebo probe exceeded {_GPU_PROBE_TIMEOUT}s; assuming "
            f"unavailable and falling back to CPU tonemapping")
        _libplacebo_available = False
    except (FileNotFoundError, OSError) as e:
        logging.debug(f"libplacebo probe failed to run: {e}")
        _libplacebo_available = False

    logging.warning(f"Vulkan/libplacebo available: {_libplacebo_available}")
    return _libplacebo_available


def vulkan_cuda_interop_available() -> bool:
    """Return True if CUDA→Vulkan interop works for hardware-decoded frames.

    Probes once and caches, validating the full NVIDIA fast-path chain:
    CUDA frames (NVDEC) mapped to Vulkan via hwmap, then libplacebo."""
    global _cuda_interop_available
    if _cuda_interop_available is not None:
        return _cuda_interop_available

    if not FFMPEG_EXECUTABLE:
        _cuda_interop_available = False
        return False

    startupinfo, creationflags = _startupinfo()

    cmd = [
        FFMPEG_EXECUTABLE, '-loglevel', 'error',
        '-init_hw_device', 'cuda=cu:0',
        '-init_hw_device', 'vulkan=vk@cu',
        '-filter_hw_device', 'vk',
        '-f', 'lavfi', '-i', 'color=c=black:s=64x64,format=p010',
        '-vf', ('hwupload_cuda,'
                'hwmap=derive_device=vulkan,'
                'libplacebo=tonemapping=clip:format=nv12,'
                'hwdownload,format=nv12'),
        '-frames:v', '1', '-f', 'null', '-',
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            startupinfo=startupinfo, creationflags=creationflags,
        )
        _cuda_interop_available = (result.returncode == 0)
        if not _cuda_interop_available and result.stderr:
            logging.warning(f"CUDA interop probe stderr: {result.stderr.decode('utf-8', errors='replace').strip()}")
    except (FileNotFoundError, OSError) as e:
        logging.warning(f"CUDA→Vulkan interop probe raised: {e}")
        _cuda_interop_available = False

    logging.warning(f"CUDA/Vulkan interop available: {_cuda_interop_available}")
    return _cuda_interop_available


_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _split_png_frames(data: bytes) -> 'list[Image.Image]':
    """Split a byte stream of back-to-back PNG files into PIL Image objects."""
    frames: list[Image.Image] = []
    pos = 0
    while pos < len(data):
        if data[pos:pos + 8] != _PNG_SIGNATURE:
            pos += 1
            continue
        next_sig = data.find(_PNG_SIGNATURE, pos + 8)
        chunk = data[pos:] if next_sig == -1 else data[pos:next_sig]
        frames.append(Image.open(io.BytesIO(chunk)))
        if next_sig == -1:
            break
        pos = next_sig
    return frames


def _batch_ffmpeg_filter_complex(n: int, per_input_filter: str) -> str:
    """Build a filter_complex that applies per_input_filter to each of N inputs and concats."""
    if n == 1:
        return f'[0:v]trim=end_frame=1,setpts=PTS-STARTPTS,{per_input_filter}[out]'
    parts = ';'.join(
        f'[{i}:v]trim=end_frame=1,setpts=PTS-STARTPTS,{per_input_filter}[v{i}]'
        for i in range(n)
    )
    concat_in = ''.join(f'[v{i}]' for i in range(n))
    return f'{parts};{concat_in}concat=n={n}:v=1:a=0[out]'


def extract_frames_batch(
    video_path: str,
    time_positions: 'list[float]',
    width: int,
    height: int,
) -> 'list[Image.Image]':
    """Extract multiple original frames in a single ffmpeg process.

    Uses N -ss/-i pairs with filter_complex concat so the file is opened N
    times internally but only one process is spawned, capping the burst at 1
    process instead of N.
    """
    if not time_positions:
        return []
    if not FFMPEG_EXECUTABLE:
        return []
    n = len(time_positions)
    startupinfo, creationflags = _startupinfo()
    scale = f'scale={width}:{height}:force_original_aspect_ratio=decrease'
    cmd = [FFMPEG_EXECUTABLE]
    for t in time_positions:
        cmd += ['-ss', str(t), '-i', os.path.normpath(video_path)]
    cmd += [
        '-filter_complex', _batch_ffmpeg_filter_complex(n, scale),
        '-map', '[out]',
        '-f', 'image2pipe', '-vcodec', 'png', '-',
    ]
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=startupinfo, creationflags=creationflags,
    )
    out, err = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f'FFmpeg batch frame extraction failed: {err.decode("utf-8", errors="replace")}'
        )
    return _split_png_frames(out)


def extract_frames_with_conversion_batch(
    video_path: str,
    time_positions: 'list[float]',
    gamma: float,
    tonemapper: str,
    width: int,
    height: int,
    lut_enabled: bool = True,
) -> 'list[Image.Image]':
    """Tonemap-convert multiple frames in a single ffmpeg process.

    Applies the same CPU tonemap filter chain as extract_frame_with_conversion
    but to all N frames in one pass, reducing process count from N to 1.

    lut_enabled: TEMPORARY, dev-verification only (see FFMPEG_FILTER_LEGACY_NO_LUT).
    """
    if not time_positions:
        return []
    if not FFMPEG_EXECUTABLE:
        return []
    n = len(time_positions)
    startupinfo, creationflags = _startupinfo()
    if lut_enabled:
        tone_filter = FFMPEG_FILTER.format(
            gamma=gamma, width=width, height=height, tonemapper=tonemapper.lower(),
            lut_path=get_lut_filter_path(),
        )
    else:
        tone_filter = FFMPEG_FILTER_LEGACY_NO_LUT.format(
            gamma=gamma, width=width, height=height, tonemapper=tonemapper.lower()
        )
    cmd = [FFMPEG_EXECUTABLE]
    for t in time_positions:
        cmd += ['-ss', str(t), '-i', os.path.normpath(video_path)]
    cmd += [
        '-filter_complex', _batch_ffmpeg_filter_complex(n, tone_filter),
        '-map', '[out]',
        '-f', 'image2pipe', '-vcodec', 'png', '-',
    ]
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=startupinfo, creationflags=creationflags,
    )
    out, err = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f'FFmpeg batch conversion failed: {err.decode("utf-8", errors="replace")}'
        )
    return _split_png_frames(out)


def extract_frame_with_conversion(video_path, gamma, tonemapper='reinhard',
                                  time_position=None, width: 'int | str' = 'iw',
                                  height: 'int | str' = 'ih', lut_enabled: bool = True):
    """Extract a frame and apply tonemapping conversion; returns a PIL Image.

    width/height: default ('iw'/'ih') keeps source resolution; pass concrete
    sizes to have ffmpeg scale the preview down for snappier decoding.
    lut_enabled: TEMPORARY, dev-verification only -- see FFMPEG_FILTER_LEGACY_NO_LUT."""
    properties = get_video_properties(video_path)
    if not properties or properties['duration'] == 0:
        raise ValueError("Invalid video properties or duration.")

    if time_position is None:
        target_time = properties['duration'] / 3
    else:
        target_time = time_position

    if lut_enabled:
        filter_str = FFMPEG_FILTER.format(
            gamma=gamma, width=width, height=height, tonemapper=tonemapper.lower(),
            lut_path=get_lut_filter_path(),
        )
    else:
        filter_str = FFMPEG_FILTER_LEGACY_NO_LUT.format(
            gamma=gamma, width=width, height=height, tonemapper=tonemapper.lower()
        )
    cmd = [
        FFMPEG_EXECUTABLE, '-ss', str(target_time), '-i', video_path,
        '-vf', filter_str,
        '-vframes', '1', '-f', 'image2pipe', '-'
    ]

    out = run_ffmpeg_command(cmd)
    try:
        return Image.open(io.BytesIO(out))
    except UnidentifiedImageError as e:
        logging.error(f"Failed to extract and convert frame: {e}")
        raise RuntimeError("Failed to extract and convert frame.")


def extract_frame_with_gpu_conversion(video_path, gamma, tonemapper='bt.2390',
                                      time_position=None, width: 'int | str' = 'iw',
                                      height: 'int | str' = 'ih', lut_enabled: bool = True):
    """GPU (libplacebo) counterpart to extract_frame_with_conversion, for
    tonemappers with no CPU implementation (see GPU_ONLY_TONEMAPPERS). Uses
    the plain-Vulkan (CPU-decode) path -- CUDA interop isn't worth it for a
    single preview frame.

    lut_enabled: TEMPORARY, dev-verification only -- see build_libplacebo_filter.
    """
    properties = get_video_properties(video_path)
    if not properties or properties['duration'] == 0:
        raise ValueError("Invalid video properties or duration.")

    target_time = properties['duration'] / 3 if time_position is None else time_position

    filter_str = build_libplacebo_filter(
        gamma, tonemapper, width=width, height=height, lut_enabled=lut_enabled)
    cmd = [FFMPEG_EXECUTABLE] + VULKAN_DEVICE_ARGS + [
        '-ss', str(target_time), '-i', video_path,
        '-vf', filter_str,
        '-vframes', '1', '-f', 'image2pipe', '-'
    ]

    out = run_ffmpeg_command(cmd)
    try:
        return Image.open(io.BytesIO(out))
    except UnidentifiedImageError as e:
        logging.error(f"Failed to extract and convert frame (GPU): {e}")
        raise RuntimeError("Failed to extract and convert frame.")


def extract_frames_with_gpu_conversion_batch(
    video_path: str,
    time_positions: 'list[float]',
    gamma: float,
    tonemapper: str,
    width: int,
    height: int,
    lut_enabled: bool = True,
) -> 'list[Image.Image]':
    """GPU counterpart to extract_frames_with_conversion_batch.

    Loops extract_frame_with_gpu_conversion once per position rather than
    building a shared multi-input Vulkan filter graph -- that's materially
    more complex and not worth it for this narrower, heavier-weight path.
    """
    if not time_positions:
        return []
    return [
        extract_frame_with_gpu_conversion(
            video_path, gamma, tonemapper=tonemapper,
            time_position=t, width=width, height=height, lut_enabled=lut_enabled)
        for t in time_positions
    ]

def extract_frame(video_path, time_position=None, width: 'int | None' = None,
                  height: 'int | None' = None):
    """Extract a frame from the video; returns a PIL Image.

    width/height: when both given, ffmpeg scales the frame on the way out,
    so the preview decodes far less data."""
    properties = get_video_properties(video_path)
    if not properties or properties['duration'] == 0:
        raise ValueError("Invalid video properties or duration.")

    # Calculate target time
    if time_position is None:
        target_time = properties['duration'] / 3  # Changed from /6 to /3
    else:
        target_time = time_position

    cmd = [FFMPEG_EXECUTABLE, '-ss', str(target_time), '-i', video_path]
    if width and height:
        cmd += ['-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease']
    cmd += ['-vframes', '1', '-f', 'image2pipe', '-']

    out = run_ffmpeg_command(cmd)
    try:
        return Image.open(io.BytesIO(out))
    except UnidentifiedImageError as e:
        logging.error(f"Failed to extract frame: {e}")
        raise RuntimeError("Failed to extract frame.")

def _int_or_zero(v) -> int:
    """Convert a value to int; return 0 for None, empty, or non-numeric strings (e.g. 'N/A')."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float_or_zero(v) -> float:
    """Convert a value to float; return 0.0 for None, empty, or non-numeric strings (e.g. 'N/A')."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_frame_rate_fraction(value) -> float:
    """Parse an ffprobe fractional frame-rate string ('30000/1001', '0/0', or
    the literal 'N/A') into a float. Returns 0.0 when it can't be determined
    -- callers should treat that as 'try the next field' or 'unreadable',
    never pass it straight through to ffmpeg's -r."""
    if not value or '/' not in value:
        return 0.0
    num_str, _, den_str = value.partition('/')
    try:
        num, den = int(num_str), int(den_str)
    except ValueError:
        return 0.0
    return num / den if den else 0.0


def _parse_dovi(video_stream: dict) -> 'tuple[bool, int | None]':
    """Detect Dolby Vision from the video stream's side data.

    ffprobe surfaces DoVi as a ``side_data_list`` entry with
    ``side_data_type == 'DOVI configuration record'`` carrying ``dv_profile``
    (5 = IPTPQc2/no HDR10-compatible base layer, 7 = BD dual-layer,
    8 = single-layer HDR10-compatible). Returns ``(is_dolby_vision,
    dovi_profile)``; a record with an unreadable profile still flags the
    stream as DoVi so the UI badge and audio tier split stay correct.
    """
    for sd in video_stream.get('side_data_list') or []:
        if sd.get('side_data_type') == 'DOVI configuration record':
            try:
                return True, int(sd.get('dv_profile'))
            except (TypeError, ValueError):
                return True, None
    return False, None


def _parse_bit_depth(video_stream: dict) -> int:
    """Determine the source's actual bit depth (8/10/12/16), independent of the
    output color depth we ultimately encode to (always capped to 8-bit free /
    10-bit Pro). Prefers ffprobe's ``bits_per_raw_sample``; falls back to the
    trailing digits in ``pix_fmt`` (e.g. ``yuv420p10le`` -> 10); defaults to 8."""
    raw_sample = _int_or_zero(video_stream.get('bits_per_raw_sample'))
    if raw_sample:
        return raw_sample
    pix_fmt = video_stream.get('pix_fmt') or ''
    match = re.search(r'(\d+)(?:le|be)$', pix_fmt)
    if match:
        return int(match.group(1))
    return 8


def get_video_properties(input_file):
    if input_file in _VIDEO_PROPS_CACHE:
        return _VIDEO_PROPS_CACHE[input_file]

    with _VIDEO_PROPS_CACHE_LOCK:
        return _probe_video_properties(input_file)


def _probe_video_properties(input_file):
    """Runs under _VIDEO_PROPS_CACHE_LOCK; re-checks the cache (another thread
    may have populated it while this one was waiting on the lock) before
    spawning ffprobe, matching _get_hdr_metadata's check-lock-check pattern."""
    if input_file in _VIDEO_PROPS_CACHE:
        return _VIDEO_PROPS_CACHE[input_file]

    startupinfo, creationflags = _startupinfo()

    command = [
        FFPROBE_EXECUTABLE,
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-show_format',
        os.path.normpath(input_file)
    ]

    try:
        result = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=creationflags
        )
        output, _ = result.communicate()
        
        if result.returncode != 0:
            return None
            
        if isinstance(output, bytes):
            output = output.decode('utf-8')
            
        data = json.loads(output)
        
        video_stream = None
        audio_stream = None
        audio_streams = []
        subtitle_streams = []
        attachment_streams = []
        
        for stream in data.get('streams', []):
            if (stream['codec_type'] == 'video' and not video_stream):
                video_stream = stream
            elif stream['codec_type'] == 'audio':
                if not audio_stream:
                    audio_stream = stream
                audio_streams.append(stream)
            elif (stream['codec_type'] == 'subtitle'):
                subtitle_streams.append(stream)
            elif stream['codec_type'] == 'attachment':
                attachment_streams.append(stream)
        
        if not video_stream:
            return None
            
        # avg_frame_rate can be '0/0' (VFR sources) or 'N/A'; fall back to
        # r_frame_rate (the nominal rate) before giving up.
        frame_rate = (_parse_frame_rate_fraction(video_stream.get('avg_frame_rate'))
                      or _parse_frame_rate_fraction(video_stream.get('r_frame_rate')))
        if not frame_rate:
            return None

        if 'format' not in data:
            return None
        duration = float(data['format'].get('duration', 0))

        is_dolby_vision, dovi_profile = _parse_dovi(video_stream)

        # Matroska rarely declares a per-stream bit_rate -- fall back to the
        # container's overall bit_rate (an estimate, includes audio overhead).
        bit_rate = _int_or_zero(video_stream.get('bit_rate'))
        bit_rate_estimated = False
        if not bit_rate:
            container_bit_rate = _int_or_zero(data['format'].get('bit_rate'))
            if container_bit_rate:
                bit_rate = container_bit_rate
                bit_rate_estimated = True

        audio_bit_rate = _int_or_zero(audio_stream.get('bit_rate')) if audio_stream else 0

        # Match Windows Explorer's Properties->Details: reconstruct each
        # stream's byte count and divide by the CONTAINER's duration
        # (rounded), not the stream's own (possibly offset-shortened) one.
        total_bit_rate = bit_rate
        rounded_duration = round(duration) if duration > 0 else 0
        if rounded_duration and bit_rate and not bit_rate_estimated:
            video_duration = _float_or_zero(video_stream.get('duration')) or duration
            video_bits = bit_rate * video_duration
            bit_rate = round(video_bits / rounded_duration)
            total_bit_rate = bit_rate
            if audio_bit_rate:
                audio_duration = (_float_or_zero(audio_stream.get('duration')) if audio_stream else 0.0) or duration
                audio_bits = audio_bit_rate * audio_duration
                total_bit_rate = round((video_bits + audio_bits) / rounded_duration)

        props = {
            "width": int(video_stream.get('width', 0)),
            "height": int(video_stream.get('height', 0)),
            "bit_rate": bit_rate,
            "bit_rate_estimated": bit_rate_estimated,
            "total_bit_rate": total_bit_rate,
            "codec_name": video_stream.get('codec_name', ''),
            "frame_rate": float(frame_rate),
            "duration": duration,
            "audio_codec": audio_stream.get('codec_name', '') if audio_stream else '',
            "audio_bit_rate": audio_bit_rate,
            "audio_streams": audio_streams,
            "subtitle_streams": subtitle_streams,
            "attachment_streams": attachment_streams,
            "color_primaries": video_stream.get('color_primaries', ''),
            "color_transfer": video_stream.get('color_transfer', ''),
            "bit_depth": _parse_bit_depth(video_stream),
            "is_dolby_vision": is_dolby_vision,
            "dovi_profile": dovi_profile,
        }
        _VIDEO_PROPS_CACHE[input_file] = props
        return props
        
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as e:
        logging.error(f"Error getting video properties: {str(e)}")
        return None
