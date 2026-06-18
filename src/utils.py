import ffmpeg
from PIL import Image, UnidentifiedImageError
import subprocess
import os
import io
import logging
import sys
import json
import shutil

# Constants and initialization
LOGGING_ENABLED = False
TONEMAP = ["Reinhard", "Mobius", "Hable"]
# Preview filter chains. These keep a trailing scale={width}:{height} because the
# preview pipeline downscales the extracted frame to a thumbnail. Index 0 = Static,
# 1 = Dynamic.
FFMPEG_FILTER = [
    'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap={tonemapper},eq=gamma={gamma},scale={width}:{height}',
    'zscale=t=linear:npl={npl},tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv:p=bt709,eq=gamma={gamma},scale={width}:{height}'
]

# Conversion filter chains (CPU path). Identical to the preview chains minus the
# trailing scale: a full conversion always keeps the source resolution, so
# scale={w}:{h} (to the source's own size) was a per-frame swscale no-op.
FFMPEG_CONVERT_FILTER = [
    'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap={tonemapper},eq=gamma={gamma}',
    'zscale=t=linear:npl={npl},tonemap={tonemapper},zscale=t=bt709:m=bt709:r=tv:p=bt709,eq=gamma={gamma}'
]

# Flags that create the Vulkan device libplacebo runs on. Prepended to the ffmpeg
# command (before -i) when the GPU tonemap path is active.
VULKAN_DEVICE_ARGS = ['-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk']

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
        base_path = getattr(sys, '_MEIPASS') if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        if sys.platform != 'win32' and filename.endswith('.exe'):
            filename = filename[:-4]
        
        executable = os.path.normpath(os.path.join(base_path, filename))
        logging.debug(f"Looking for {filename} at: {executable}")
        
        if not os.path.exists(executable):
            system_exec = shutil.which(filename)
            if system_exec:
                executable = system_exec
                logging.debug(f"Found {filename} in system PATH: {executable}")
            else:
                raise FileNotFoundError(f"{filename} not found in bundle or system PATH")
        
        return executable

    except Exception as e:
        logging.error(f"Error finding {filename}: {str(e)}")
        raise

def verify_ffmpeg_files():
    """Verify that ffmpeg files exist and are accessible"""
    global FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
    try:
        if getattr(sys, 'frozen', False):
            base_path = getattr(sys, '_MEIPASS')
            logging.debug(f"Verifying FFmpeg files in bundled environment: {base_path}")
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            logging.debug(f"Verifying FFmpeg files in normal environment: {base_path}")
        
        files_to_check = ['ffmpeg.exe', 'ffprobe.exe', 'ffplay.exe']
        found_files = {}
        
        for file in files_to_check:
            try:
                path = get_executable_path(file)
                found_files[file] = path
                logging.info(f"Found {file} at: {path}")
            except FileNotFoundError as e:
                logging.error(f"Could not find {file}: {str(e)}")
                raise

        FFMPEG_EXECUTABLE = found_files['ffmpeg.exe']
        FFPROBE_EXECUTABLE = found_files['ffprobe.exe']

        return found_files

    except Exception as e:
        logging.error(f"Error verifying FFmpeg files: {str(e)}")
        raise

def initialize_ffmpeg():
    """Initialize FFmpeg executables and configure the environment."""
    global FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
    try:
        found_files = verify_ffmpeg_files()
        FFMPEG_EXECUTABLE = found_files['ffmpeg.exe']
        FFPROBE_EXECUTABLE = found_files['ffprobe.exe']

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

# Rest of your existing functions...
def run_ffmpeg_command(cmd):
    """Run an FFmpeg command with proper path handling"""
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        startupinfo = None
        creationflags = 0
    
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

# MAXFALL is static mastering-display metadata, identical for every call on a
# given file, yet probing it costs ~0.5-1.2s (ffprobe decodes ~1s of frames).
# Memoize per path so it is paid once per video instead of once per preview.
_MAXFALL_CACHE = {}


def clear_maxfall_cache():
    """Drop memoized MAXFALL values (call when loading a new/replaced file)."""
    _MAXFALL_CACHE.clear()


def get_maxfall(video_path):
    """
    Extract MAXFALL from video metadata using ffprobe (memoized per path).
    Args:
        video_path (str): Path to the video file.
    Returns:
        float: The MAXFALL value.
    """
    if video_path in _MAXFALL_CACHE:
        return _MAXFALL_CACHE[video_path]
    value = _compute_maxfall(video_path)
    _MAXFALL_CACHE[video_path] = value
    return value


def _compute_maxfall(video_path):
    """Probe MAXFALL from mastering-display metadata (uncached)."""
    cmd = [
        FFPROBE_EXECUTABLE,
        '-v', 'quiet',
        '-select_streams', 'v:0',
        '-show_frames',
        '-read_intervals', '%+1',
        '-print_format', 'json',
        video_path
    ]
    
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        startupinfo = None
        creationflags = 0

    out = subprocess.check_output(
        cmd,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        startupinfo=startupinfo,
        creationflags=creationflags
    )
    data = json.loads(out.decode('utf-8'))
    frames = data.get('frames', [])
    for frame in frames:
        side_data_list = frame.get('side_data_list', [])
        for side_data in side_data_list:
            if side_data.get('side_data_type') == 'Mastering display metadata':
                max_fall = side_data.get('max_fall', None)
                if (max_fall):
                    return float(max_fall)
    return 100  # Default value if MAXFALL is not found

def build_libplacebo_filter(filter_index, gamma, tonemapper, width='iw', height='ih'):
    """Build the GPU tonemapping filter chain (HDR->SDR) using libplacebo.

    libplacebo does the same HDR->SDR tonemap as the CPU ``tonemap`` filter, but
    on the GPU (Vulkan) -- offloading the single-threaded tonemap step that
    dominates a CPU conversion. The user's tonemapper (reinhard/mobius/hable)
    maps 1:1 to libplacebo's ``tonemapping`` option.

    Static (``filter_index`` 0) uses a fixed curve; Dynamic (1) enables
    libplacebo's per-scene ``peak_detect`` -- a better stand-in for the old
    npl=MAXFALL approach, and it skips the MAXFALL ffprobe entirely. Frames are
    uploaded as p010, tonemapped to nv12, downloaded, and ``eq=gamma`` is applied
    on the CPU afterwards so gamma matches the CPU path exactly.
    """
    peak = 1 if filter_index == 1 else 0
    tm = tonemapper.lower()
    return (
        f'format=p010,hwupload,'
        f'libplacebo=w={width}:h={height}:tonemapping={tm}:'
        f'colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:'
        f'peak_detect={peak}:format=nv12,'
        f'hwdownload,format=nv12,eq=gamma={gamma}'
    )

# Cached result of the Vulkan/libplacebo capability probe: None = not yet probed.
_libplacebo_available = None

def reset_libplacebo_probe():
    """Forget the cached probe result (used by tests)."""
    global _libplacebo_available
    _libplacebo_available = None

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

    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        startupinfo = None
        creationflags = 0

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

    logging.debug(f"Vulkan/libplacebo available: {_libplacebo_available}")
    return _libplacebo_available

def extract_frame_with_conversion(video_path, gamma, filter_index, tonemapper='reinhard',
                                  time_position=None, width: 'int | str' = 'iw',
                                  height: 'int | str' = 'ih'):
    """
    Extracts a frame from the video and applies tonemapping conversion.
    Args:
        video_path (str): The path to the video file.
        gamma (float): The gamma correction value.
        filter_index (int): The index of the filter to use.
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

    # Calculate target time
    if time_position is None:
        target_time = properties['duration'] / 3  # Changed from /6 to /3
    else:
        target_time = time_position

    tonemapper = tonemapper.lower()  # Ensure tonemapper is lowercase

    if filter_index == 1:
        maxfall = get_maxfall(video_path)
        filter_str = FFMPEG_FILTER[filter_index].format(
            gamma=gamma, width=width, height=height, npl=maxfall, tonemapper=tonemapper
        )
    else:
        filter_str = FFMPEG_FILTER[filter_index].format(
            gamma=gamma, width=width, height=height, tonemapper=tonemapper
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
        cmd += ['-vf', f'scale={width}:{height}']
    cmd += ['-vframes', '1', '-f', 'image2pipe', '-']

    out = run_ffmpeg_command(cmd)
    try:
        return Image.open(io.BytesIO(out))
    except UnidentifiedImageError as e:
        logging.error(f"Failed to extract frame: {e}")
        raise RuntimeError("Failed to extract frame.")

def get_video_properties(input_file):
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        startupinfo = None
        creationflags = 0

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
        
        duration = float(data['format'].get('duration', 0))
            
        return {
            "width": int(video_stream.get('width', 0)),
            "height": int(video_stream.get('height', 0)),
            "bit_rate": int(video_stream.get('bit_rate', 0)),
            "codec_name": video_stream.get('codec_name', ''),
            "frame_rate": float(frame_rate),
            "duration": duration,
            "audio_codec": audio_stream.get('codec_name', '') if audio_stream else '',
            "audio_bit_rate": int(audio_stream.get('bit_rate', 0)) if audio_stream else 0,
            "subtitle_streams": subtitle_streams,
            "color_primaries": video_stream.get('color_primaries', ''),
            "color_transfer": video_stream.get('color_transfer', ''),
        }
        
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as e:
        print(f"Error getting video properties: {str(e)}")
        return None