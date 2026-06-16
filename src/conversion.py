import os
import subprocess
import threading
import webbrowser
import multiprocessing
import re
import logging
from tkinter import messagebox
from utils import get_video_properties, FFMPEG_FILTER, FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE, get_maxfall
from tkinterdnd2 import DND_FILES
import sys
import platform  # Add this import at the top

class ConversionManager:
    def __init__(self):
        self.process = None
        self.cancelled = False
        self.cpu_count = multiprocessing.cpu_count()
        self.filter_options = ['Static', 'Dynamic']  # Add filter options to ConversionManager

    def start_conversion(self, input_path, output_path, gamma, use_gpu, selected_filter_index,
                         progress_var, interactable_elements, gui_instance,
                         open_after_conversion, cancel_button, tonemapper='reinhard'):
        if not self.verify_paths(input_path, output_path):
            return

        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        self.cancelled = False
        self.use_gpu = use_gpu  # Store the use_gpu state

        properties = get_video_properties(input_path)
        if properties is None:
            messagebox.showwarning("Warning", "Failed to retrieve video properties.")
            return

        # A missing/zero duration would make progress tracking divide by zero in
        # the monitor thread (which would then die silently, leaving the UI stuck).
        if not properties.get('duration'):
            messagebox.showwarning(
                "Warning", "Could not determine the video's duration, so it can't be converted.")
            return

        self.disable_ui(interactable_elements)
        cancel_button.config(command=lambda: self.cancel_conversion(
            gui_instance, interactable_elements, cancel_button))
        cancel_button.grid()

        cmd = self.construct_ffmpeg_command(
            input_path, output_path, gamma, properties, use_gpu, selected_filter_index,
            tonemapper=tonemapper
        )
        self.process = self.start_ffmpeg_process(cmd)

        thread = threading.Thread(target=self.monitor_progress, args=(
            progress_var, properties['duration'], gui_instance, interactable_elements,
            cancel_button, output_path, open_after_conversion, gamma, tonemapper))
        thread.daemon = True
        thread.start()

    def verify_paths(self, input_path, output_path):
        if not input_path or not output_path:
            messagebox.showwarning(
                "Warning", "Please select both an input file and specify an output file.")
            return False
        return True

    def disable_ui(self, elements):
        for element in elements:
            element.config(state="disabled")

    def enable_ui(self, elements):
        for element in elements:
            element.config(state="normal")

    def construct_ffmpeg_command(self, input_path, output_path, gamma, properties, use_gpu, 
                               selected_filter_index, tonemapper='reinhard'):
        cmd = [
            FFMPEG_EXECUTABLE,
            '-loglevel', 'info',
        ]
        current_platform = platform.system().lower()

        # GPU acceleration setup
        if use_gpu:
            if current_platform in ["windows", "linux"]:
                cmd += [
                    '-hwaccel', 'cuda',
                    '-hwaccel_device', '0'
                ]
            else:
                messagebox.showwarning("Warning", "GPU acceleration is not supported on this platform.")
                use_gpu = False

        # Input file
        cmd += ['-i', os.path.normpath(input_path)]

        # The filter must be applied before mapping streams
        tonemapper = tonemapper.lower()
        if selected_filter_index == 1:
            maxfall = get_maxfall(input_path)
            filter_str = FFMPEG_FILTER[selected_filter_index].format(
                gamma=gamma, width=properties["width"], height=properties["height"],
                npl=maxfall, tonemapper=tonemapper
            )
            cmd += [
                '-filter_complex', f'[0:v:0]{filter_str}[vout]',
                '-map', '[vout]'  # Map the filtered video output
            ]
        else:
            filter_str = FFMPEG_FILTER[selected_filter_index].format(
                gamma=gamma, width=properties["width"], height=properties["height"],
                tonemapper=tonemapper
            )
            cmd += [
                '-filter_complex', f'[0:v:0]{filter_str}[vout]',
                '-map', '[vout]'  # Map the filtered video output
            ]

        # Map remaining streams. Audio is always mapped; subtitle mapping depends
        # on the output container (see _container_stream_args).
        subtitle_map_args, audio_codec_args, subtitle_codec_args = \
            self._container_stream_args(output_path, properties)
        cmd += ['-map', '0:a?']   # Map all audio streams if they exist
        cmd += subtitle_map_args

        # Encoding settings
        if use_gpu:
            cmd += [
                '-c:v', 'h264_nvenc',
                '-preset', 'p4',
                '-tune', 'hq',
                '-rc', 'vbr',
                '-cq', '20',
                '-b:v', str(properties['bit_rate']),
                '-maxrate', str(int(properties['bit_rate'] * 1)),
                '-bufsize', str(int(properties['bit_rate'] * 2))
            ]
        else:
            cmd += [
                '-c:v', 'libx264',
                '-preset', 'veryfast',  # Faster CPU preset
                '-tune', 'film',
                '-crf', '23',
                '-b:v', str(properties['bit_rate'])
            ]

        # Common settings
        cmd += [
            '-r', str(properties['frame_rate']),
            '-pix_fmt', 'yuv420p',
            '-strict', '-2',
        ]
        cmd += audio_codec_args      # copy, or transcode when container demands
        cmd += subtitle_codec_args   # copy / mov_text / omitted
        cmd += [
            '-map_metadata', '0', # Copy all metadata
            '-movflags', '+faststart',  # Optimize for streaming playback
            os.path.normpath(output_path),
            '-y'
        ]

        logging.debug(f"Constructed ffmpeg command: {' '.join(cmd)}")
        return cmd

    # Audio/subtitle codecs that the MP4-family containers (.mp4/.m4v/.mov) accept
    # via stream copy. Anything else must be transcoded or dropped.
    _MP4_AUDIO_OK = {'aac', 'ac3', 'eac3', 'mp3', 'alac'}
    _TEXT_SUBTITLES = {'subrip', 'srt', 'ass', 'ssa', 'text', 'mov_text', 'webvtt'}
    _MP4_FAMILY = {'mp4', 'm4v', 'mov'}

    def _container_stream_args(self, output_path, properties):
        """Decide subtitle mapping and audio/subtitle codecs for the output container.

        Prefer lossless stream copy. For MP4-family containers, which can't copy
        TrueHD/DTS audio or ASS/PGS subtitles, fall back to transcoding audio to
        AAC and text subtitles to mov_text, and drop image subtitles (e.g. PGS)
        that no MP4 codec can represent. Non-MP4 containers (notably MKV) keep the
        original copy-everything behavior.

        Returns ``(subtitle_map_args, audio_codec_args, subtitle_codec_args)``.
        """
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        if ext not in self._MP4_FAMILY:
            # MKV and friends accept the source streams as-is.
            return (['-map', '0:s?'], ['-c:a', 'copy'], ['-c:s', 'copy'])

        audio_codec = (properties.get('audio_codec') or '').lower()
        if audio_codec and audio_codec not in self._MP4_AUDIO_OK:
            bit_rate = properties.get('audio_bit_rate') or 0
            target_rate = str(min(int(bit_rate), 384000)) if bit_rate else '192k'
            audio_codec_args = ['-c:a', 'aac', '-b:a', target_rate]
        else:
            audio_codec_args = ['-c:a', 'copy']

        # Map only subtitle streams MP4 can hold (text); drop image subs entirely.
        subtitle_map_args = []
        for stream in properties.get('subtitle_streams', []):
            if (stream.get('codec_name') or '').lower() in self._TEXT_SUBTITLES:
                subtitle_map_args += ['-map', f"0:{stream['index']}"]
        subtitle_codec_args = ['-c:s', 'mov_text'] if subtitle_map_args else []

        return (subtitle_map_args, audio_codec_args, subtitle_codec_args)

    def start_ffmpeg_process(self, cmd):
        """Start the FFmpeg process without showing a console window."""
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,  # output goes to a file; don't fill an unread pipe
            universal_newlines=True,
            startupinfo=startupinfo,
            creationflags=creationflags,
            encoding='utf-8',
            errors='replace'
        )
        logging.debug(f"Started FFmpeg process with command: {' '.join(cmd)}")
        return process

    def monitor_progress(self, progress_var, duration, gui_instance, interactable_elements,
                         cancel_button, output_path, open_after_conversion, gamma,
                         tonemapper='reinhard'):
        progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
        error_messages = []
        gpu_error_detected = False

        for line in self.process.stderr:
            if self.process is None:
                return
            decoded_line = line.strip()
            logging.debug(decoded_line)
            error_messages.append(decoded_line)
            match = progress_pattern.search(decoded_line)
            if match and duration:
                elapsed_time = self.parse_time(match.group(1))
                progress = (elapsed_time / duration) * 100
                gui_instance.root.after(0, lambda p=progress: progress_var.set(p))
                gui_instance.root.after(0, gui_instance.root.update_idletasks)

            if 'cuda' in decoded_line.lower() or 'nvcuda.dll' in decoded_line.lower():
                gpu_error_detected = True

        if self.process is not None:
            self.process.wait()
            if self.process.returncode != 0 and self.use_gpu and gpu_error_detected and not self.cancelled:
                logging.warning("GPU acceleration failed. Retrying with CPU encoding.")
                # The retry touches Tk (gpu checkbox, dialog, UI state) and must run
                # on the main thread, not this worker thread.
                gui_instance.root.after(0, lambda: self._retry_with_cpu(
                    gui_instance, interactable_elements, cancel_button, progress_var,
                    open_after_conversion, gamma, tonemapper))
            else:
                self.handle_completion(gui_instance, interactable_elements, cancel_button,
                                    output_path, open_after_conversion, error_messages)

    def _retry_with_cpu(self, gui_instance, interactable_elements, cancel_button,
                        progress_var, open_after_conversion, gamma, tonemapper):
        """Restart the conversion on the CPU after a GPU failure. Runs on the main thread."""
        gui_instance.gpu_accel_var.set(False)
        messagebox.showwarning("GPU Acceleration Failed",
                               "GPU acceleration failed. Switching to CPU encoding.")
        self.start_conversion(
            input_path=gui_instance.input_path_var.get(),
            output_path=gui_instance.output_path_var.get(),
            gamma=gamma,
            use_gpu=False,  # Force CPU encoding
            selected_filter_index=self.filter_options.index(gui_instance.filter_var.get()),
            progress_var=progress_var,
            interactable_elements=interactable_elements,
            gui_instance=gui_instance,
            open_after_conversion=open_after_conversion,
            cancel_button=cancel_button,
            tonemapper=tonemapper,  # preserve the user's tonemapper across the retry
        )

    def parse_time(self, time_str):
        hours, minutes, seconds = map(float, time_str.split(':'))
        return hours * 3600 + minutes * 60 + seconds

    def handle_completion(self, gui_instance, interactable_elements, cancel_button,
                          output_path, open_after_conversion, error_messages):
        def _handle():
            if self.process and self.process.returncode == 0:
                logging.info("Conversion completed successfully.")
                messagebox.showinfo(
                    "Success", f"Conversion complete! Output saved to: {output_path}")
                if open_after_conversion:
                    webbrowser.open(output_path)
            elif not self.cancelled:
                error_message = '\n'.join(error_messages)
                logging.error(f"Conversion failed with code {self.process.returncode}: {error_message}")
                messagebox.showerror(
                    "Error", f"Conversion failed with code {self.process.returncode}\n{error_message}")

            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()

            if hasattr(gui_instance, 'register_drop_target'):
                gui_instance.register_drop_target()

        gui_instance.root.after(0, _handle)

    def cancel_conversion(self, gui_instance, interactable_elements, cancel_button):
        self.cancelled = True
        if self.process:
            self.process.terminate()
            self.process = None
            gui_instance.root.after(0, lambda: messagebox.showinfo(
                "Cancelled", "Video conversion has been cancelled."))
            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()

            if hasattr(gui_instance, 'register_drop_target'):
                gui_instance.register_drop_target()

    def is_gpu_available(self):
        try:
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW
            else:
                startupinfo = None
                creationflags = 0

            result = subprocess.run(['nvidia-smi'], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=creationflags)
            if result.returncode != 0:
                logging.warning("nvidia-smi not found or no NVIDIA GPU detected.")
                return False
            logging.debug("NVIDIA GPU detected.")

            cmd = [FFMPEG_EXECUTABLE, '-encoders']
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                startupinfo=startupinfo,
                creationflags=creationflags
            )
            stdout, _ = process.communicate()
            if process.returncode != 0:
                logging.error("FFmpeg failed to list encoders.")
                return False
            if 'h264_nvenc' not in stdout.lower():
                logging.warning("'h264_nvenc' encoder not found in FFmpeg.")
                return False
            
            logging.debug("'h264_nvenc' encoder is available in FFmpeg.")
            return True

        except FileNotFoundError:
            logging.warning("'nvidia-smi' command not found. NVIDIA GPU may not be installed.")
            return False
        except Exception as e:
            logging.error(f"Error checking GPU availability: {e}")
            return False

conversion_manager = ConversionManager()