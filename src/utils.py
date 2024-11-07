import ffmpeg
from tkinter import messagebox
from PIL import Image, ImageTk, UnidentifiedImageError
import subprocess
import os
import numpy as np
import io
import logging
import sys
import json

# Constants and initialization
LOGGING_ENABLED = False
FFMPEG_FILTER = 'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap=reinhard,eq=gamma={gamma},scale={width}:{height}'
FFMPEG_EXECUTABLE = None
FFPROBE_EXECUTABLE = None

# Initialize logging
def setup_logging():
    """Configure logging with fallback locations for Wine compatibility"""
    if not LOGGING_ENABLED:
        # Set up minimal console logging for warnings and errors only
        logging.basicConfig(
            level=logging.WARNING,
            format='%(levelname)s - %(message)s'
        )
        return False

    try:
        # Get the executable's directory or current directory
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        
        log_paths = [
            os.path.join(base_dir, 'debug.log'),  # Try executable directory
            os.path.join(os.getcwd(), 'debug.log'),  # Try current working directory
            os.path.expanduser('~/debug.log'),  # Try user's home directory
            'debug.log'  # Try current directory as last resort
        ]
        
        for log_path in log_paths:
            try:
                logging.basicConfig(
                    level=logging.DEBUG if LOGGING_ENABLED else logging.WARNING,
                    filename=log_path,
                    filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                )
                # Add console handler for immediate feedback
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
        
        # If all paths fail, set up console-only logging
        logging.basicConfig(
            level=logging.DEBUG if LOGGING_ENABLED else logging.WARNING,
            format='%(levelname)s - %(message)s'
        )
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
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
        
        # Handle platform-specific executable names
        if sys.platform != 'win32' and filename.endswith('.exe'):
            filename = filename[:-4]  # Remove .exe for non-Windows platforms
        
        # Check bundled executable first
        executable = os.path.normpath(os.path.join(base_path, filename))
        logging.debug(f"Looking for {filename} at: {executable}")
        
        if not os.path.exists(executable):
            # Try system PATH as fallback
            import shutil
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
            base_path = sys._MEIPASS
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

        # Configure ffmpeg-python
        ffmpeg._ffmpeg_binary = FFMPEG_EXECUTABLE
        ffmpeg._ffprobe_binary = FFPROBE_EXECUTABLE
        
        # Set environment variables
        os.environ['FFMPEG_BINARY'] = FFMPEG_EXECUTABLE
        os.environ['FFPROBE_BINARY'] = FFPROBE_EXECUTABLE

        # Add diagnostic logging
        logging.debug(f"Configured ffmpeg binary: {ffmpeg._ffmpeg_binary}")
        logging.debug(f"Configured ffprobe binary: {ffmpeg._ffprobe_binary}")

    except Exception as e:
        logging.error(f"Error setting up ffmpeg: {str(e)}", exc_info=True)
        messagebox.showerror("Error", f"Failed to initialize ffmpeg: {str(e)}")
        raise

# Call initialization functions
setup_logging()
initialize_ffmpeg()

# Rest of your existing functions...
def run_ffmpeg_command(cmd):
    """Run an FFmpeg command with proper path handling"""
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    else:
        startupinfo = None
    
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
            startupinfo=startupinfo
        )
        
        out, err = process.communicate()
        
        if process.returncode != 0:
            error_msg = err.decode('utf-8', errors='replace')
            logging.error(f"FFmpeg error: {error_msg}")
            raise RuntimeError(f"FFmpeg error: {error_msg}")
        
        return out
        
    except Exception as e:
        logging.error(f"Error running FFmpeg command: {str(e)}")
        raise RuntimeError(f"Error running FFmpeg command: {str(e)}")

def extract_frame_with_conversion(video_path, gamma):
    """
    Extracts a frame from the video 1/3rd of the way through and applies gamma correction.
    Args:
        video_path (str): The path to the video file.
        gamma (float): The gamma correction value.
    Returns:
        PIL.Image: The extracted and gamma-corrected frame as a PIL image.
    """
    properties = get_video_properties(video_path)
    if not properties or properties['duration'] == 0:
        raise ValueError("Invalid video properties or duration.")

    target_time = properties['duration'] / 3  # Changed to 1/3rd of the duration

    cmd = [
        FFMPEG_EXECUTABLE, '-ss', str(target_time), '-i', video_path,
        '-vf', FFMPEG_FILTER.format(gamma=gamma, width='iw', height='ih'),
        '-vframes', '1', '-f', 'image2pipe', '-'
    ]

    out = run_ffmpeg_command(cmd)
    try:
        return Image.open(io.BytesIO(out))
    except UnidentifiedImageError as e:
        logging.error(f"Failed to extract and convert frame: {e}")
        raise RuntimeError("Failed to extract and convert frame.")

def extract_frame(video_path):
    """
    Extracts a frame from the video 1/3rd of the way through.
    Args:
        video_path (str): The path to the video file.
    Returns:
        PIL.Image: The extracted frame as a PIL image.
    """
    properties = get_video_properties(video_path)
    if not properties or properties['duration'] == 0:
        raise ValueError("Invalid video properties or duration.")

    target_time = properties['duration'] / 3  # Changed to 1/3rd of the duration
    
    cmd = [
        FFMPEG_EXECUTABLE, '-ss', str(target_time), '-i', video_path,
        '-vframes', '1', '-f', 'image2pipe', '-'
    ]
    
    out = run_ffmpeg_command(cmd)
    return Image.open(io.BytesIO(out))

def get_video_properties(input_file):
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    else:
        startupinfo = None

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
            startupinfo=startupinfo  # Add startupinfo here
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
            if stream['codec_type'] == 'video' and not video_stream:
                video_stream = stream
            elif stream['codec_type'] == 'audio' and not audio_stream:
                audio_stream = stream
            elif stream['codec_type'] == 'subtitle':
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
            "subtitle_streams": subtitle_streams
        }
        
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as e:
        print(f"Error getting video properties: {str(e)}")
        return None