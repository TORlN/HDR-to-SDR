import ffmpeg
from tkinter import messagebox
from PIL import Image, ImageTk, UnidentifiedImageError
import subprocess
import os
import numpy as np
import io
import logging
import sys  # Added import

# Logging configuration
LOGGING_ENABLED = False # Changed to True to enable logging

if LOGGING_ENABLED:
    logging.basicConfig(level=logging.DEBUG, filename='debug.log', filemode='w',
                        format='%(name)s - %(levelname)s - %(message)s')
else:
    logging.basicConfig(level=logging.WARNING)
    # Prevent any file handlers from being added when logging is disabled
    logging.getLogger().handlers = []

FFMPEG_FILTER = 'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap=reinhard,eq=gamma={gamma},scale={width}:{height}'

if getattr(sys, 'frozen', False):
    # If the application is frozen by PyInstaller, use the bundled ffmpeg.exe in root
    FFMPEG_EXECUTABLE = os.path.join(sys._MEIPASS, "ffmpeg.exe")
    logging.debug(f"FFMPEG_EXECUTABLE set to: {FFMPEG_EXECUTABLE}")
    
    # Verify ffmpeg.exe exists
    if not os.path.exists(FFMPEG_EXECUTABLE):
        logging.error(f"ffmpeg.exe not found at {FFMPEG_EXECUTABLE}")
    else:
        logging.debug("ffmpeg.exe successfully found in the bundled application.")
    
    # Optional: List all files in the bundled directory for verification
    logging.debug("Bundled application files:")
    for root_dir, dirs, files in os.walk(sys._MEIPASS):
        for file in files:
            logging.debug(os.path.join(root_dir, file))
else:
    # During development, assume ffmpeg is in the system's PATH
    FFMPEG_EXECUTABLE = 'ffmpeg'
    logging.debug(f"FFMPEG_EXECUTABLE set to system PATH: {FFMPEG_EXECUTABLE}")

def run_ffmpeg_command(cmd):
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    else:
        startupinfo = None
    
    # Replace the ffmpeg command with the bundled executable path
    cmd[0] = FFMPEG_EXECUTABLE
    logging.debug(f"Running ffmpeg command: {' '.join(cmd)}")
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
    except FileNotFoundError:
        logging.error("ffmpeg executable not found. Please ensure ffmpeg is bundled correctly with the application.")
        raise RuntimeError("ffmpeg executable not found. Please reinstall the application.")
    
    out, err = process.communicate()
    
    if process.returncode != 0:
        logging.error(f"ffmpeg error: {err.decode('utf-8')}")
        raise RuntimeError(f"ffmpeg error: {err.decode('utf-8')}")
    
    return out

def extract_frame_with_conversion(video_path, gamma):
    """
    Extracts a frame from the video and applies gamma correction.
    Args:
        video_path (str): The path to the video file.
        gamma (float): The gamma correction value.
    Returns:
        PIL.Image: The extracted frame as a PIL image.
    """
    cmd = [
        'ffmpeg', '-i', video_path,
        '-vf', FFMPEG_FILTER.format(gamma=gamma, width='iw', height='ih'),
        '-vframes', '1', '-f', 'image2pipe', '-'
    ]
    
    out = run_ffmpeg_command(cmd)
    return Image.open(io.BytesIO(out))

def extract_frame(video_path):
    """
    Extracts a frame from the video without applying any modifications.
    Args:
        video_path (str): The path to the video file.
    Returns:
        PIL.Image: The extracted frame as a PIL image.
    """
    cmd = [
        'ffmpeg', '-i', video_path,
        '-vframes', '1', '-f', 'image2pipe', '-'
    ]
    
    out = run_ffmpeg_command(cmd)
    return Image.open(io.BytesIO(out))

def get_video_properties(input_file):
    """
    Retrieve properties of a video file using ffmpeg.
    Args:
        input_file (str): Path to the input video file.
    Returns:
        dict: A dictionary containing the following video properties:
            - width (int): Width of the video in pixels.
            - height (int): Height of the video in pixels.
            - bit_rate (int): Bit rate of the video in bits per second.
            - codec_name (str): Name of the video codec.
            - frame_rate (float): Frame rate of the video.
            - audio_codec (str): Name of the audio codec (default is 'aac' if no audio stream is found).
            - audio_bit_rate (int): Bit rate of the audio in bits per second (default is 128000 if no bit rate is found).
            - duration (float): Duration of the video in seconds.
    Raises:
        Exception: If there is an error in retrieving video properties, an error message is shown and None is returned.
    """
    try:
        probe = ffmpeg.probe(input_file)
        video_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
        
        properties = {
            "width": int(video_stream['width']),
            "height": int(video_stream['height']),
            "bit_rate": int(video_stream.get('bit_rate', 5000000)),  # Default bit rate is 5 Mbps
            "codec_name": video_stream['codec_name'],
            "frame_rate": eval(video_stream['avg_frame_rate']),  # Use avg_frame_rate for accurate frame rate
            "audio_codec": audio_stream['codec_name'] if audio_stream else 'aac',  # Default audio codec is AAC
            "audio_bit_rate": int(audio_stream['bit_rate']) if audio_stream and 'bit_rate' in audio_stream else 128000,  # Default audio bit rate is 128 kbps
            "duration": float(video_stream['duration'])
        }
        return properties
    except Exception as e:
        logging.error(f"Failed to get video properties: {e}")
        messagebox.showerror("Error", f"Failed to get video properties: {e}")
        return None