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
                         open_after_conversion, cancel_button):
        if not self.verify_paths(input_path, output_path):
            return

        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        self.cancelled = False

        properties = get_video_properties(input_path)
        if properties is None:
            messagebox.showwarning("Warning", "Failed to retrieve video properties.")
            return

        self.disable_ui(interactable_elements)
        cancel_button.config(command=lambda: self.cancel_conversion(
            gui_instance, interactable_elements, cancel_button))
        cancel_button.grid()

        cmd = self.construct_ffmpeg_command(
            input_path, output_path, gamma, properties, use_gpu, selected_filter_index
        )
        self.process = self.start_ffmpeg_process(cmd)

        thread = threading.Thread(target=self.monitor_progress, args=(
            progress_var, properties['duration'], gui_instance, interactable_elements,
            cancel_button, output_path, open_after_conversion, gamma))
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

    def construct_ffmpeg_command(self, input_path, output_path, gamma, properties, use_gpu, selected_filter_index):
        cmd = [
            FFMPEG_EXECUTABLE,
            '-loglevel', 'info',
            # Removed the '-threads' parameter
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

        # Map all audio streams
        cmd += ['-map', '0:v:0']  # Map first video stream
        cmd += ['-map', '0:a?']   # Map all audio streams if they exist
        cmd += ['-map', '0:s?']   # Map all subtitle streams if they exist

        # Use appropriate filter option
        if selected_filter_index == 1:
            maxfall = get_maxfall(input_path)
            filter_str = FFMPEG_FILTER[selected_filter_index].format(
                gamma=gamma, width=properties["width"], height=properties["height"], npl=maxfall
            )
            cmd += ['-filter_complex', filter_str]
        else:
            filter_str = FFMPEG_FILTER[selected_filter_index].format(
                gamma=gamma, width=properties["width"], height=properties["height"]
            )
            cmd += ['-filter:v', filter_str]

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
            '-c:a', 'copy',      # Copy all audio streams as-is
            '-c:s', 'copy',      # Copy all subtitle streams as-is
            '-map_metadata', '0', # Copy all metadata
            '-movflags', '+faststart',  # Optimize for streaming playback
            os.path.normpath(output_path),
            '-y'
        ]

        logging.debug(f"Constructed ffmpeg command: {' '.join(cmd)}")
        return cmd

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
            stdout=subprocess.PIPE,
            universal_newlines=True,
            startupinfo=startupinfo,
            creationflags=creationflags,
            encoding='utf-8',
            errors='replace'
        )
        logging.debug(f"Started FFmpeg process with command: {' '.join(cmd)}")
        return process

    def monitor_progress(self, progress_var, duration, gui_instance, interactable_elements,
                         cancel_button, output_path, open_after_conversion, gamma):
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
            if match:
                elapsed_time = self.parse_time(match.group(1))
                progress = (elapsed_time / duration) * 100
                gui_instance.root.after(0, lambda p=progress: progress_var.set(p))
                gui_instance.root.after(0, gui_instance.root.update_idletasks)

            if 'cuda' in decoded_line.lower() or 'nvcuda.dll' in decoded_line.lower():
                gpu_error_detected = True

        if self.process is not None:
            self.process.wait()
            if self.process.returncode != 0 and gpu_error_detected and not self.cancelled:
                logging.warning("GPU acceleration failed. Retrying with CPU encoding.")
                # Untick GPU checkbox
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
                    cancel_button=cancel_button
                )
            else:
                self.handle_completion(gui_instance, interactable_elements, cancel_button,
                                    output_path, open_after_conversion, error_messages)

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

    def extract_frame(self, video_path, time=None):
        properties = get_video_properties(video_path)
        if not properties or properties['duration'] == 0:
            raise ValueError("Invalid video properties or duration.")

        if time is None:
            time = properties['duration'] / 3

        output_frame_path = os.path.join(os.path.dirname(video_path), 'frame_preview.jpg')

        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            startupinfo = None
            creationflags = 0

        cmd = [
            FFMPEG_EXECUTABLE,
            '-ss', str(time),
            '-i', os.path.normpath(video_path),
            '-frames:v', '1',
            '-q:v', '2',
            os.path.normpath(output_frame_path),
            '-y'
        ]

        subprocess.run(cmd, check=True, startupinfo=startupinfo, creationflags=creationflags)
        return output_frame_path

    def get_frame_preview(self, video_path):
        properties = get_video_properties(video_path)
        if not properties or properties['duration'] == 0:
            raise ValueError("Invalid video properties or duration.")

        frame = self.extract_frame(video_path)
        return frame

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