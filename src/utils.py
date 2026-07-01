import ffmpeg
from PIL import Image, UnidentifiedImageError
import subprocess
import os
import io
import logging
import re
import sys
import json
import shutil
import threading

# Constants and initialization
LOGGING_ENABLED = False
TONEMAP = ["Reinhard", "Mobius", "Hable"]
# npl=100 is the SDR reference white (100 nits). Lower values push the average
# frame toward full white and crush highlight detail; higher values darken the
# output. 100 is the correct target for standard SDR displays.
FFMPEG_FILTER = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv:p=bt709,'
    'eq=gamma={gamma},scale={width}:{height}:force_original_aspect_ratio=decrease'
)

FFMPEG_CONVERT_FILTER = (
    'zscale=t=linear:npl=100,tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv:p=bt709,'
    'eq=gamma={gamma}'
)

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
def setup_logging():
    """Configure logging with fallback locations for Wine compatibility"""
    if not LOGGING_ENABLED:
        logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')
        return False

    try:
        base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
        log_paths = [
            os.path.join(base_dir, 'debug.log'),
            os.path.join(os.getcwd(), 'debug.log'),
            os.path.expanduser('~/debug.log'),
            'debug.log'
        ]
        
        for log_path in log_paths:
            try:
                logging.basicConfig(
                    level=logging.DEBUG if LOGGING_ENABLED else logging.WARNING,
                    filename=log_path,
                    filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                )
                console = logging.StreamHandler()
                console.setLevel(logging.DEBUG if LOGGING_ENABLED else logging.WARNING)
                formatter = logging.Formatter('%(levelname)s - %(message)s')
                console.setFormatter(formatter)
                logging.getLogger('').addHandler(console)
                
                logging.info(f"Logging initialized. Log file: {log_path}")
                logging.info(f"Platform: {sys.platform}")
                logging.info(f"Executable path: {sys.executable if getattr(sys, 'frozen', False) else __file__}")
                return True
            except (IOError, PermissionError) as e:
                print(f"Failed to set up logging at {log_path}: {e}")
                continue
        
        logging.basicConfig(level=logging.DEBUG if LOGGING_ENABLED else logging.WARNING, format='%(levelname)s - %(message)s')
        logging.warning("Failed to create log file. Logging to console only.")
        return False
    
    except Exception as e:
        print(f"Error setting up logging: {e}")
        return False

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
    global FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
    try:
        found_files = verify_ffmpeg_files()
        FFMPEG_EXECUTABLE = found_files['ffmpeg']
        FFPROBE_EXECUTABLE = found_files['ffprobe']

        # Configure ffmpeg-python. These are private, undeclared module
        # attributes, so set/read them via setattr/getattr to keep static type
        # checkers from flagging them as unknown attributes of the module.
        setattr(ffmpeg, '_ffmpeg_binary', FFMPEG_EXECUTABLE)
        setattr(ffmpeg, '_ffprobe_binary', FFPROBE_EXECUTABLE)

        # Set environment variables
        os.environ['FFMPEG_BINARY'] = FFMPEG_EXECUTABLE
        os.environ['FFPROBE_BINARY'] = FFPROBE_EXECUTABLE

        # Add diagnostic logging
        logging.debug(f"Configured ffmpeg binary: {getattr(ffmpeg, '_ffmpeg_binary', None)}")
        logging.debug(f"Configured ffprobe binary: {getattr(ffmpeg, '_ffprobe_binary', None)}")

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
    
    # Normalize all paths in command
    cmd = [os.path.normpath(str(arg)) if os.path.sep in str(arg) else str(arg) for arg in cmd]
    
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

    out = subprocess.check_output(
        cmd,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        startupinfo=startupinfo,
        creationflags=creationflags
    )
    data = json.loads(out.decode('utf-8'))
    result: dict = {'maxcll': None, 'maxfall': None, 'mastering_peak': None}
    for frame in data.get('frames', []):
        for sd in frame.get('side_data_list', []):
            sdt = sd.get('side_data_type')
            if sdt == 'Content light level metadata':
                mc = sd.get('max_content')
                if mc:
                    result['maxcll'] = float(mc)
                mf = sd.get('max_average')
                if mf:
                    result['maxfall'] = float(mf)
            elif sdt == 'Mastering display metadata':
                lum = sd.get('max_luminance')
                if lum and '/' in str(lum):
                    num, den = str(lum).split('/')
                    if float(den) != 0:
                        result['mastering_peak'] = float(num) / float(den)
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


def get_maxfall(video_path):
    """Backward-compat alias for get_maxcll."""
    return get_maxcll(video_path)


def _compute_maxfall(video_path):
    """Backward-compat shim — returns the raw probe dict (tests patch this)."""
    return _probe_hdr_metadata(video_path)

def build_libplacebo_filter(gamma, tonemapper, width='iw', height='ih',
                            cuda_input: bool = False) -> str:
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
    """
    tm = tonemapper.lower()
    prefix = ('hwmap=derive_device=vulkan,'
              if cuda_input else 'format=p010,hwupload,')
    libplacebo = (
        f'libplacebo=w={width}:h={height}:tonemapping={tm}:'
        f'colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:'
        f'peak_detect=1:format=nv12'
    )
    gamma_is_identity = abs(gamma - 1.0) < 1e-9
    if cuda_input and gamma_is_identity:
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
) -> 'list[Image.Image]':
    """Tonemap-convert multiple frames in a single ffmpeg process.

    Applies the same CPU tonemap filter chain as extract_frame_with_conversion
    but to all N frames in one pass, reducing process count from N to 1.
    """
    if not time_positions:
        return []
    if not FFMPEG_EXECUTABLE:
        return []
    n = len(time_positions)
    startupinfo, creationflags = _startupinfo()
    tone_filter = FFMPEG_FILTER.format(
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
                                  height: 'int | str' = 'ih'):
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

    filter_str = FFMPEG_FILTER.format(
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
            
        frame_rate = video_stream.get('avg_frame_rate', '0/1')
        if '/' in frame_rate:
            num, den = map(int, frame_rate.split('/'))
            frame_rate = num / den if den != 0 else 0
        
        if 'format' not in data:
            return None
        duration = float(data['format'].get('duration', 0))

        props = {
            "width": int(video_stream.get('width', 0)),
            "height": int(video_stream.get('height', 0)),
            "bit_rate": _int_or_zero(video_stream.get('bit_rate')),
            "codec_name": video_stream.get('codec_name', ''),
            "frame_rate": float(frame_rate),
            "duration": duration,
            "audio_codec": audio_stream.get('codec_name', '') if audio_stream else '',
            "audio_bit_rate": _int_or_zero(audio_stream.get('bit_rate')) if audio_stream else 0,
            "subtitle_streams": subtitle_streams,
            "color_primaries": video_stream.get('color_primaries', ''),
            "color_transfer": video_stream.get('color_transfer', ''),
            "bit_depth": _parse_bit_depth(video_stream),
        }
        with _VIDEO_PROPS_CACHE_LOCK:
            _VIDEO_PROPS_CACHE[input_file] = props
        return props
        
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as e:
        print(f"Error getting video properties: {str(e)}")
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