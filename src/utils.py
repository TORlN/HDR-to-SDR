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
import tempfile
import threading

# Constants and initialization
TONEMAP = ["Reinhard", "Mobius", "Hable", "BT.2390", "Spline"]
# npl=100 is the SDR reference white (100 nits). Lower values push the average
# frame toward full white and crush highlight detail; higher values darken the
# output. 100 is the correct target for standard SDR displays.
#
# The final zscale step deliberately omits p=bt709: dropping it leaves the
# frame's transfer/matrix/range correct for bt709 but the primaries tag
# still inherited from the source (bt2020) -- confirmed via ffprobe against
# a real encode. lut3d then performs the actual gamut correction on those
# gamma-encoded values (see src/luts/rec2020_to_rec709.cube,
# tools/generate_lut.py), and setparams retags color_primaries/color_trc/
# colorspace to bt709 (a metadata-only fix, no further pixel changes --
# confirmed the retag is necessary, not redundant, by comparing ffprobe
# output with and without it).
#
# interp=tetrahedral: the gamut correction has a hard per-channel clamp at
# the BT.709 boundary (a real kink, not a smooth curve -- matches zscale's
# own p=bt709 clamping behavior). lut3d's default trilinear interpolation
# rounds off that kink; tetrahedral interpolation (the standard, more
# accurate mode used by professional color tools) measurably reduces the
# resulting error -- confirmed via real-ffmpeg pixel comparison against
# zscale's own conversion on real HDR10 content.
FFMPEG_FILTER = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv,'
    'lut3d=file={lut_path}:interp=tetrahedral,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709,'
    'eq=gamma={gamma},scale={width}:{height}:force_original_aspect_ratio=decrease'
)

FFMPEG_CONVERT_FILTER = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv,'
    'lut3d=file={lut_path}:interp=tetrahedral,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709,'
    'eq=gamma={gamma}'
)

# TEMPORARY -- today's zscale-only gamut correction (no LUT). Used only by
# the preview pane's dev-verification toggle (see
# docs/superpowers/specs/2026-07-22-lut-color-pipeline-design.md) when LUT
# preview is switched off, so the two can be compared side by side before
# the old path is trusted and deleted. Removed in Task 9 of the LUT
# implementation plan, together with the toggle -- never used by real export.
FFMPEG_FILTER_LEGACY_NO_LUT = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv:p=bt709,'
    'eq=gamma={gamma},scale={width}:{height}:force_original_aspect_ratio=decrease'
)

# Tonemappers with no zscale/CPU implementation -- confirmed via
# `ffmpeg -h filter=tonemap` against the real bundled build (only
# none/linear/gamma/clip/reinhard/hable/mobius exist there). These two exist
# only via libplacebo, so they require the GPU tonemap path both for preview
# and for final conversion. Lowercase, matching libplacebo's own spelling.
GPU_ONLY_TONEMAPPERS = {'bt.2390', 'spline'}


def is_gpu_only_tonemapper(tonemapper: str) -> bool:
    """True for tonemapping algorithms only implemented on the GPU (libplacebo)
    path -- FFMPEG_CONVERT_FILTER (CPU) has no equivalent. Case-insensitive."""
    return tonemapper.lower() in GPU_ONLY_TONEMAPPERS


# Shared "All Video Files" file-dialog filter entry, used by both the
# single-file Browse dialog (gui.py) and the multi-select batch-add dialog
# (batch.py) so the supported-extension list can't silently drift apart
# between the two.
VIDEO_FILE_FILTER = ("All Video Files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v")

# Flags that create the Vulkan device libplacebo runs on. Prepended to the ffmpeg
# command (before -i) when the GPU tonemap path is active (CPU decode fallback).
VULKAN_DEVICE_ARGS = ['-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk']

# NVIDIA fast path: CUDA device for NVDEC hardware decode, linked to a Vulkan
# device so libplacebo can consume CUDA frames via hwmap without a CPU round-trip.
# Also sets -hwaccel cuda so ffmpeg routes decode through NVDEC.
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
    """Where the app's log file lives -- %LOCALAPPDATA%\\HDR to SDR\\app.log on
    Windows. A windowed/onedir build has no console for stderr to reach, so
    without this file warnings (e.g. a failed update check) were invisible."""
    base = os.getenv('LOCALAPPDATA') or tempfile.gettempdir()
    return os.path.join(base, 'HDR to SDR', 'app.log')


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
            # PyInstaller sets _MEIPASS; Nuitka does not — use __file__ instead.
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
    """Escape an absolute Windows path for embedding as a value inside an
    ffmpeg -vf filtergraph string (e.g. lut3d=file=..., libplacebo's lut=...).

    Confirmed empirically against the bundled ffmpeg build: a raw path
    breaks the parser at the drive-letter colon ("No option name near
    '/Users/...'"), and neither a bare colon nor a single '\\:' fixes it --
    the parser needs the colon escaped as '\\\\:' (two literal backslashes
    then the colon), with all '\\' elsewhere converted to '/'.

    Always targets literal '\\' rather than os.sep: the input is always a
    Windows path (this app only ships on Windows), but the test suite also
    runs on Linux CI, where os.sep is '/' and would leave the backslashes
    untouched.
    """
    forward = path.replace('\\', '/')
    return forward.replace(':', '\\\\:', 1)


_LUT_FILTER_PATH = None


def get_lut_filter_path() -> str:
    """Resolve and cache the bundled Rec.2020->Rec.709 LUT's path, pre-escaped
    for direct embedding in FFMPEG_FILTER / FFMPEG_CONVERT_FILTER /
    build_libplacebo_filter's lut3d=file=... / lut=... values.

    Raises FileNotFoundError if the bundled .cube file is missing --  a
    missing bundled asset means the install itself is broken, and this must
    surface as a hard, obvious failure rather than silently degrading."""
    global _LUT_FILTER_PATH
    if _LUT_FILTER_PATH is not None:
        return _LUT_FILTER_PATH
    raw_path = get_resource_path(os.path.join('luts', 'rec2020_to_rec709.cube'))
    _LUT_FILTER_PATH = _escape_path_for_filter(raw_path)
    return _LUT_FILTER_PATH

def verify_ffmpeg_files():
    """Verify that ffmpeg files exist and are accessible"""
    global FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
    try:
        if getattr(sys, 'frozen', False):
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            logging.debug(f"Verifying FFmpeg files in bundled environment: {base_path}")
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            logging.debug(f"Verifying FFmpeg files in normal environment: {base_path}")
        
        files_to_check = ['ffmpeg', 'ffprobe']
        found_files = {}

        for name in files_to_check:
            try:
                path = get_executable_path(name)
                found_files[name] = path
                logging.info(f"Found {name} at: {path}")
            except FileNotFoundError as e:
                logging.error(f"Could not find {name}: {str(e)}")
                raise

        FFMPEG_EXECUTABLE = found_files['ffmpeg']
        FFPROBE_EXECUTABLE = found_files['ffprobe']

        return found_files

    except Exception as e:
        logging.error(f"Error verifying FFmpeg files: {str(e)}")
        raise

def initialize_ffmpeg():
    """Initialize FFmpeg executables and configure the environment."""
    try:
        verify_ffmpeg_files()
    except Exception as e:
        # Surfacing the error is the caller's job (the GUI shows it on startup);
        # a utility module must not pop a dialog.
        logging.error(f"Error setting up ffmpeg: {str(e)}", exc_info=True)
        raise

# Call initialization functions
setup_logging()
try:
    initialize_ffmpeg()
except Exception:
    # Importing this module must never crash or block on a dialog when ffmpeg is
    # missing; FFMPEG_EXECUTABLE/FFPROBE_EXECUTABLE stay None and the GUI reports
    # it to the user on startup (see HDRConverterGUI.__init__).
    logging.error("ffmpeg could not be initialized at import time", exc_info=True)


def _startupinfo():
    """Return (startupinfo, creationflags) that suppress the console window on Windows."""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si, subprocess.CREATE_NO_WINDOW
    return None, 0


# Rest of your existing functions...
def run_ffmpeg_command(cmd):
    """Run an FFmpeg command with proper path handling"""
    startupinfo, creationflags = _startupinfo()
    
    # Replace the ffmpeg command with the bundled/system executable path
    cmd[0] = FFMPEG_EXECUTABLE

    # Normalize path-like args (e.g. input/output file paths) to the native
    # separator. The -vf value is a filtergraph string, not a file path -- it
    # can contain a deliberately pre-escaped LUT path (see
    # _escape_path_for_filter: doubled backslash before the drive-letter
    # colon, forward slashes elsewhere) that os.path.normpath would corrupt by
    # collapsing the doubled backslash and swapping '/' back to '\\', breaking
    # ffmpeg's filtergraph parser. Skip normalization for the arg immediately
    # following -vf.
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

# HDR metadata (MaxCLL, mastering display peak) is static per file and costs
# ~0.5-1.2s to probe.  Cache the full dict so all callers share one ffprobe hit.
_MAXFALL_CACHE: dict[str, dict] = {}
_MAXFALL_CACHE_LOCK = threading.Lock()

# Video properties (streams, duration, codec) are also static per file; caching
# eliminates the extra ffprobe spawned inside each extract_frame / extract_frame_with_conversion call.
_VIDEO_PROPS_CACHE: dict[str, dict] = {}
_VIDEO_PROPS_CACHE_LOCK = threading.Lock()


def clear_video_properties_cache() -> None:
    """Drop cached video properties (call when loading a new/replaced file)."""
    with _VIDEO_PROPS_CACHE_LOCK:
        _VIDEO_PROPS_CACHE.clear()


def clear_maxfall_cache():
    """Drop cached HDR metadata and video properties (call when loading a new/replaced file)."""
    with _MAXFALL_CACHE_LOCK:
        _MAXFALL_CACHE.clear()
    clear_video_properties_cache()


def _probe_hdr_metadata(video_path):
    """Probe MaxCLL and mastering display peak luminance from the first frame (uncached).

    Returns:
        dict with keys 'maxcll' (float|None) and 'mastering_peak' (float|None).
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
    result: dict = {'maxcll': None, 'maxfall': None, 'mastering_peak': None}

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
        # A stream that passes the basic ffprobe (get_video_properties) can
        # still fail this second frame-level probe (e.g. truncated/corrupt
        # HDR data) -- degrade to "no HDR metadata" like get_video_properties
        # already does, instead of raising uncaught out of the load path.
        logging.error(f"Error probing HDR metadata for {video_path}: {e}")
        return result
    for frame in data.get('frames', []):
        for sd in frame.get('side_data_list', []):
            sdt = sd.get('side_data_type')
            if sdt == 'Content light level metadata':
                # A legitimately-reported 0 must not be treated the same as
                # an absent key -- 'if mc:' would silently drop it.
                mc = sd.get('max_content')
                if mc is not None:
                    result['maxcll'] = float(mc)
                mf = sd.get('max_average')
                if mf is not None:
                    result['maxfall'] = float(mf)
            elif sdt == 'Mastering display metadata':
                lum = sd.get('max_luminance')
                if lum is not None:
                    if '/' in str(lum):
                        num, den = str(lum).split('/')
                        if float(den) != 0:
                            result['mastering_peak'] = float(num) / float(den)
                    else:
                        # Some containers report a plain number instead of
                        # the fraction form -- just as valid, don't drop it.
                        result['mastering_peak'] = float(lum)
    return result


def _get_hdr_metadata(video_path):
    """Thread-safe cached wrapper around _probe_hdr_metadata."""
    if video_path in _MAXFALL_CACHE:
        return _MAXFALL_CACHE[video_path]
    with _MAXFALL_CACHE_LOCK:
        if video_path in _MAXFALL_CACHE:
            return _MAXFALL_CACHE[video_path]
        meta = _probe_hdr_metadata(video_path)
        _MAXFALL_CACHE[video_path] = meta
        return meta


def get_maxcll(video_path):
    """Return MaxCLL (peak pixel luminance) for display; None if not embedded."""
    return _get_hdr_metadata(video_path)['maxcll']


def build_libplacebo_filter(gamma, tonemapper, width: 'int | str' = 'iw',
                            height: 'int | str' = 'ih',
                            cuda_input: bool = False,
                            lut_enabled: bool = True) -> str:
    """Build the GPU tonemapping filter chain (HDR->SDR) using libplacebo.

    Always uses peak_detect=1 (per-scene peak detection).  When cuda_input is
    False (default / CPU decode path) frames are uploaded from system RAM via
    format=p010,hwupload.  When cuda_input is True (NVIDIA CUDA→Vulkan interop
    path) frames arrive in CUDA memory from NVDEC; hwmap=derive_device=vulkan
    transfers them to Vulkan without touching system RAM.

    For the gamma=1.0 case with CUDA interop the frame never touches the CPU:
    after libplacebo the Vulkan frame is remapped back to CUDA via hwmap and
    fed directly to NVENC.  For gamma≠1.0 we still download to CPU for the eq
    filter since FFmpeg has no GPU-native gamma correction outside libplacebo.

    lut_enabled: applies the same BT.2020->BT.709 3D LUT the CPU path uses.
        libplacebo's own native lut=/lut_type= option cannot reproduce
        lut3d's semantics -- measured the full cross product of all 4
        lut_type values x both color_primaries settings against a
        verified-correct reference and none matched (see the
        gpu-lut-libplacebo-native-broken memory / project notes). Root
        cause, confirmed by reading libplacebo's renderer.c: the custom-LUT
        hook it exposes runs *before* the main tonemap/gamut-conversion
        pass (or replaces it entirely for lut_type=conversion) -- there is
        no exposed hook for "after tonemap, before final gamut convert",
        which is the slot our gamut-only correction needs. So instead this
        applies the identical CPU lut3d filter the CPU path uses, after
        downloading the tonemapped frame -- verified pixel-identical to the
        CPU reference. This is real, measured cost (~2.1-2.4x slower GPU
        exports at 4K): it forces a hwdownload even on the CUDA zero-copy
        interop path, and disables that path's fully-GPU fast route
        entirely. lut_enabled=False restores the exact pre-LUT-feature
        behavior (including the zero-copy fast path) for callers that want
        raw export speed over gamut correction accuracy.
    """
    tm = tonemapper.lower()
    prefix = ('hwmap=derive_device=vulkan,'
              if cuda_input else 'format=p010,hwupload,')
    # When the LUT is on, libplacebo must not do its own gamut mapping too --
    # color_primaries=auto (keep source primaries) leaves gamut correction
    # entirely to our lut3d stage below, avoiding a double conversion.
    primaries = 'auto' if lut_enabled else 'bt709'
    # lut3d (CPU) only accepts RGB-family pixel formats, not nv12 -- feeding
    # it nv12 makes ffmpeg silently auto-insert an nv12->rgb24 swscale
    # conversion before lut3d that appears nowhere in this filter string
    # (confirmed via -loglevel verbose: "auto-inserting filter auto_scale
    # ... fmt:nv12 -> fmt:rgb24"). Downloading directly as rgba when the LUT
    # runs skips that hidden conversion -- measured ~5% faster over 300
    # synthetic 4K frames. Without the LUT, nv12 is still right: it feeds the
    # encoder directly with no RGB-family stage in between.
    download_fmt = 'rgba' if lut_enabled else 'nv12'
    libplacebo = (
        f'libplacebo=w={width}:h={height}:tonemapping={tm}:'
        f'colorspace=bt709:color_primaries={primaries}:color_trc=bt709:range=tv:'
        f'peak_detect=1:format={download_fmt}'
    )
    gamma_is_identity = abs(gamma - 1.0) < 1e-9
    if lut_enabled:
        # lut3d is CPU-only, so the frame must come down to system RAM
        # regardless of cuda_input -- there is no GPU-native path that
        # reproduces this correctly (see docstring above).
        lut_stage = (f'lut3d=file={get_lut_filter_path()}:interp=tetrahedral,'
                     f'setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709')
        if gamma_is_identity:
            suffix = f',hwdownload,format={download_fmt},{lut_stage}'
        else:
            suffix = f',hwdownload,format={download_fmt},{lut_stage},eq=gamma={gamma}'
    elif cuda_input and gamma_is_identity:
        # Fully-GPU path: remap Vulkan→CUDA after libplacebo; NVENC encodes
        # CUDA frames directly with no CPU round-trip.
        suffix = ',hwmap=reverse=1:derive_device=cuda'
    elif gamma_is_identity:
        # Plain Vulkan path: NVENC needs CPU frames, so we still download, but
        # skip the no-op eq=gamma=1 filter to avoid wasted work.
        suffix = ',hwdownload,format=nv12'
    else:
        suffix = f',hwdownload,format=nv12,eq=gamma={gamma}'
    return f'{prefix}{libplacebo}{suffix}'

# Cached result of the Vulkan/libplacebo capability probe: None = not yet probed.
_libplacebo_available = None
# Cached result of the CUDA→Vulkan interop probe.
_cuda_interop_available = None


def reset_libplacebo_probe():
    """Forget the cached probe result (used by tests)."""
    global _libplacebo_available
    _libplacebo_available = None


def reset_cuda_interop_probe():
    """Forget the cached CUDA interop probe result (used by tests)."""
    global _cuda_interop_available
    _cuda_interop_available = None

def vulkan_libplacebo_available():
    """Return True if this ffmpeg can tonemap on the GPU via Vulkan + libplacebo.

    Probes once and caches the result. The probe runs the real filter chain on a
    tiny synthetic frame, so a success genuinely proves the path works on this
    machine; on any failure we fall back to the CPU tonemap path.
    """
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
        )
        _libplacebo_available = (result.returncode == 0)
    except (FileNotFoundError, OSError) as e:
        logging.debug(f"libplacebo probe failed to run: {e}")
        _libplacebo_available = False

    logging.warning(f"Vulkan/libplacebo available: {_libplacebo_available}")
    return _libplacebo_available


def vulkan_cuda_interop_available() -> bool:
    """Return True if CUDA→Vulkan interop works for hardware-decoded frames.

    Probes once and caches. Validates the full chain NVIDIA uses for the fast
    path: CUDA frames (from NVDEC) mapped to Vulkan via hwmap, then processed
    by libplacebo.  A success proves both the driver support and the linked
    device creation work on this machine.
    """
    global _cuda_interop_available
    if _cuda_interop_available is not None:
        return _cuda_interop_available

    if not FFMPEG_EXECUTABLE:
        _cuda_interop_available = False
        return False

    startupinfo, creationflags = _startupinfo()

    # Simulate CUDA frames going through the interop chain: upload a synthetic
    # frame to CUDA memory, hwmap to Vulkan, run libplacebo, then download.
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
    """
    Extracts a frame from the video and applies tonemapping conversion.
    Args:
        video_path (str): The path to the video file.
        gamma (float): The gamma correction value.
        tonemapper (str): The tonemapping algorithm to use.
        time_position (float, optional): The time position to extract the frame from.
        width, height: output scale for the filter chain. Default ('iw'/'ih') keeps
            the source resolution; pass concrete sizes (e.g. 960, 540) to have ffmpeg
            scale the preview down, decoding far less data for a snappier preview.
        lut_enabled: TEMPORARY, dev-verification only -- see FFMPEG_FILTER_LEGACY_NO_LUT.
    Returns:
        PIL.Image: The extracted and converted frame as a PIL image.
    """
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
    """GPU (libplacebo) counterpart to extract_frame_with_conversion.

    Used for tonemappers with no zscale/CPU implementation (see
    GPU_ONLY_TONEMAPPERS) -- preview must render the true algorithm, not an
    approximation, so these route through libplacebo/Vulkan instead of
    zscale. Uses the plain-Vulkan (CPU-decode) path, not CUDA interop:
    interop optimizes full-length encodes, not single preview frames.

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
    """
    Extracts a frame from the video.
    Args:
        video_path (str): The path to the video file.
        time_position (float, optional): The time position to extract the frame from.
        width, height (int, optional): when both given, ffmpeg scales the frame to
            this size on the way out, so the preview decodes far less data.
    Returns:
        PIL.Image: The extracted frame as a PIL image.
    """
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
        subtitle_streams = []
        
        for stream in data.get('streams', []):
            if (stream['codec_type'] == 'video' and not video_stream):
                video_stream = stream
            elif (stream['codec_type'] == 'audio' and not audio_stream):
                audio_stream = stream
            elif (stream['codec_type'] == 'subtitle'):
                subtitle_streams.append(stream)
        
        if not video_stream:
            return None
            
        # avg_frame_rate can be '0/0' (denominator-zero, seen on some VFR
        # sources) or the literal string 'N/A' (no '/' at all) -- either
        # would otherwise become frame_rate=0.0 and reach ffmpeg as '-r 0'
        # (rejected outright), or raise ValueError from float('N/A').
        # r_frame_rate is the nominal rate and is almost always usable, so
        # fall back to it before giving up.
        frame_rate = (_parse_frame_rate_fraction(video_stream.get('avg_frame_rate'))
                      or _parse_frame_rate_fraction(video_stream.get('r_frame_rate')))
        if not frame_rate:
            return None

        if 'format' not in data:
            return None
        duration = float(data['format'].get('duration', 0))

        is_dolby_vision, dovi_profile = _parse_dovi(video_stream)

        # Matroska rarely declares a per-stream bit_rate the way MP4's stsd/esds
        # boxes do -- ffprobe then omits the video stream's bit_rate entirely.
        # Fall back to the container's overall bit_rate (format.bit_rate, which
        # ffprobe already computes as file_size*8/duration) rather than showing
        # nothing. It includes audio/subtitle overhead, so it's an estimate, not
        # the exact video-only figure a real per-stream reading would give.
        bit_rate = _int_or_zero(video_stream.get('bit_rate'))
        bit_rate_estimated = False
        if not bit_rate:
            container_bit_rate = _int_or_zero(data['format'].get('bit_rate'))
            if container_bit_rate:
                bit_rate = container_bit_rate
                bit_rate_estimated = True

        audio_bit_rate = _int_or_zero(audio_stream.get('bit_rate')) if audio_stream else 0

        # Windows Explorer's Properties -> Details tab computes "Data rate"
        # (video-only) and "Total bitrate" (video+audio) from each stream's
        # reconstructed byte count (that stream's own bit_rate * its own
        # duration), divided by the CONTAINER's total duration rounded to the
        # nearest whole second. ffprobe's raw per-stream bit_rate instead
        # divides by the stream's own duration without that rounding -- and
        # that duration can be shorter than the container's when the stream
        # has a start-time offset -- which is why the app's reading ran a few
        # percent higher than what Windows shows for the same file. Re-derive
        # both figures the same way Windows does so they agree exactly.
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
            "subtitle_streams": subtitle_streams,
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


def setup_dpi_awareness() -> None:
    """Enable Per-Monitor DPI awareness so Windows doesn't bitmap-scale the window."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass