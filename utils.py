import ffmpeg
from tkinter import messagebox

def get_video_properties(input_file):
    try:
        probe = ffmpeg.probe(input_file)
        video_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)

        properties = {
            "width": int(video_stream['width']),
            "height": int(video_stream['height']),
            "bit_rate": int(video_stream.get('bit_rate', 5000000)),
            "codec_name": video_stream['codec_name'],
            "frame_rate": eval(video_stream['r_frame_rate']),
            "audio_codec": audio_stream['codec_name'] if audio_stream else 'aac',
            "audio_bit_rate": int(audio_stream['bit_rate']) if audio_stream and 'bit_rate' in audio_stream else 128000,
            "duration": float(video_stream['duration'])
        }

        return properties
    except Exception as e:
        messagebox.showerror("Error", f"Failed to get video properties: {e}")
        return None