import ffmpeg
from tkinter import messagebox

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
    # Get video properties using ffmpeg
    try:
        probe = ffmpeg.probe(input_file)
        video_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
        
        # Extract relevant video properties
        properties = {
            "width": int(video_stream['width']),
            "height": int(video_stream['height']),
            "bit_rate": int(video_stream.get('bit_rate', 5000000)), # Default bit rate is 5 Mbps
            "codec_name": video_stream['codec_name'],
            "frame_rate": eval(video_stream['r_frame_rate']),
            "audio_codec": audio_stream['codec_name'] if audio_stream else 'aac', # Default audio codec is AAC
            "audio_bit_rate": int(audio_stream['bit_rate']) if audio_stream and 'bit_rate' in audio_stream else 128000, # Default audio bit rate is 128 kbps
            "duration": float(video_stream['duration'])
        }
        return properties
    except Exception as e:
        messagebox.showerror("Error", f"Failed to get video properties: {e}")
        return None