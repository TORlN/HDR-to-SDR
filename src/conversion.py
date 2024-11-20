import os
import subprocess
import threading
import webbrowser
import multiprocessing
import re
import logging
from tkinter import messagebox
from utils import get_video_properties, FFMPEG_FILTER, FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
from tkinterdnd2 import DND_FILES
import sys  # Added import
import platform  # Add this import at the top

class ConversionManager:
    """
    Manages the video conversion process from HDR to SDR, including starting,
    monitoring progress, and cancelling the conversion.
    """
    def __init__(self):
        self.process = None       # Subprocess for the ffmpeg conversion
        self.cancelled = False    # Flag to indicate if the conversion was cancelled
        # Removed drop_target_registered attribute

    def start_conversion(self, input_path, output_path, gamma, use_gpu, progress_var,
                         interactable_elements, gui_instance, open_after_conversion,
                         cancel_button):
        """
        Start the conversion process.

        Args:
            input_path (str): Path to the input video file.
            output_path (str): Path to save the converted video file.
            gamma (float): Gamma correction value.
            use_gpu (bool): Flag to enable GPU acceleration.
            progress_var (tk.DoubleVar): Variable to update the progress bar.
            interactable_elements (list): UI elements to disable during conversion.
            gui_instance (HDRConverterGUI): The GUI instance.
            open_after_conversion (bool): Whether to open the output file after conversion.
            cancel_button (ttk.Button): The cancel button widget.
        """
        if not self.verify_paths(input_path, output_path):
            return

        # Ensure paths are absolute
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)

        self.cancelled = False  # Reset the cancelled flag at the start

        properties = get_video_properties(input_path)
        if properties is None:
            messagebox.showwarning("Warning", "Failed to retrieve video properties.")
            return

        self.disable_ui(interactable_elements)
        cancel_button.config(command=lambda: self.cancel_conversion(
            gui_instance, interactable_elements, cancel_button))
        cancel_button.grid()  # Show cancel button

        cmd = self.construct_ffmpeg_command(input_path, output_path, gamma, properties, use_gpu)
        self.process = self.start_ffmpeg_process(cmd)

        # Pass the actual GUI instance to monitor_progress along with gamma
        thread = threading.Thread(target=self.monitor_progress, args=(
            progress_var, properties['duration'], gui_instance, interactable_elements,
            cancel_button, output_path, open_after_conversion, gamma))  # Added gamma here
        thread.daemon = True  # Ensure thread does not prevent program exit
        thread.start()

    def verify_paths(self, input_path, output_path):
        """Verify that the input and output paths are valid."""
        if not input_path or not output_path:
            messagebox.showwarning(
                "Warning", "Please select both an input file and specify an output file.")
            return False
        return True

    def disable_ui(self, elements):
        """Disable interactive UI elements during conversion."""
        for element in elements:
            element.config(state="disabled")

    def enable_ui(self, elements):
        """Enable interactive UI elements after conversion."""
        for element in elements:
            element.config(state="normal")

    def construct_ffmpeg_command(self, input_path, output_path, gamma, properties, use_gpu):
        """Construct the ffmpeg command for video conversion."""
        
        cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
        ]

        current_platform = platform.system().lower()
        
        if use_gpu:
            if current_platform == "windows":
                cmd += [
                    '-hwaccel', 'cuda',
                ]
            elif current_platform == "linux":
                cmd += [
                    '-hwaccel', 'cuda',
                ]
            else:
                # Unsupported platform for GPU acceleration
                messagebox.showwarning("Warning", "GPU acceleration is not supported on this platform.")
                use_gpu = False

        cmd += [
            '-i', os.path.normpath(input_path),
        ]

        if use_gpu:
            cmd += [
                '-c:v', 'h264_nvenc',
            ]
        else:
            cmd += [
                '-filter:v', FFMPEG_FILTER.format(
                    gamma=gamma, width=properties["width"], height=properties["height"]
                ),
                '-c:v', properties['codec_name'],
            ]

        cmd += [
            '-b:v', str(properties['bit_rate']),
            '-r', str(properties['frame_rate']),
            '-preset', 'fast',
            '-pix_fmt', 'yuv420p',  # Enforce 8-bit encoding
            '-strict', '-2',
            # Copy audio and subtitle streams without re-encoding
            '-c:a', 'copy',
            '-c:s', 'copy',
            os.path.normpath(output_path),
            '-y'
        ]

        logging.debug(f"Constructed ffmpeg command: {' '.join(cmd)}")
        return cmd

    def start_ffmpeg_process(self, cmd):
        """Start the ffmpeg subprocess with the given command."""
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            startupinfo=startupinfo
        )
        logging.debug(f"Started FFmpeg process with command: {' '.join(cmd)}")
        return process

    def monitor_progress(self, progress_var, duration, gui_instance, interactable_elements,
                         cancel_button, output_path, open_after_conversion, gamma):  # Added gamma parameter
        """
        Monitor the conversion progress and update the UI accordingly.
        """
        progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
        error_messages = []
        gpu_error_detected = False  # Flag to track GPU errors

        for line in self.process.stderr:
            if self.process is None:
                return  # Exit if the process is None (cancelled)
            decoded_line = line.strip()
            logging.debug(decoded_line)
            error_messages.append(decoded_line)
            match = progress_pattern.search(decoded_line)
            if match:
                elapsed_time = self.parse_time(match.group(1))
                progress = (elapsed_time / duration) * 100
                # Schedule the progress_var.set to run in the main thread
                gui_instance.root.after(0, lambda p=progress: progress_var.set(p))
                # Schedule the update_idletasks to run in the main thread
                gui_instance.root.after(0, gui_instance.root.update_idletasks)
            
            # Detect GPU-related errors
            if 'cuda' in decoded_line.lower() or 'nvcuda.dll' in decoded_line.lower():
                gpu_error_detected = True

        if self.process is not None:
            self.process.wait()
            if self.process.returncode != 0 and gpu_error_detected and not self.cancelled:
                # GPU error detected, retry with CPU
                logging.warning("GPU acceleration failed. Retrying with CPU encoding.")
                messagebox.showwarning("GPU Acceleration Failed",
                                       "GPU acceleration failed. Switching to CPU encoding.")
                # Retry the conversion with CPU
                gui_instance.start_conversion(
                    input_path=gui_instance.input_path_var.get(),
                    output_path=gui_instance.output_path_var.get(),
                    gamma=gamma,  # Use the passed gamma value
                    use_gpu=False,
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
        """Convert ffmpeg time string to seconds."""
        hours, minutes, seconds = map(float, time_str.split(':'))
        return hours * 3600 + minutes * 60 + seconds

    def handle_completion(self, gui_instance, interactable_elements, cancel_button,
                          output_path, open_after_conversion, error_messages):
        """
        Handle the completion of the conversion process, whether successful or not.
        """
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
            # Removed the else block to prevent double message boxes on cancellation

            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()  # Hide the cancel button

            # Re-register drop target
            if hasattr(gui_instance, 'register_drop_target'):
                gui_instance.register_drop_target()

        # Schedule the handle_completion to run in the main thread
        gui_instance.root.after(0, _handle)

    def cancel_conversion(self, gui_instance, interactable_elements, cancel_button):
        """
        Cancel the ongoing conversion process and reset the UI.
        """
        self.cancelled = True
        if self.process:
            self.process.terminate()
            self.process = None
            # Schedule the messagebox and UI updates to run in the main thread
            gui_instance.root.after(0, lambda: messagebox.showinfo(
                "Cancelled", "Video conversion has been cancelled."))
            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()

            # Re-register drop target
            if hasattr(gui_instance, 'register_drop_target'):
                gui_instance.register_drop_target()

    def extract_frame(self, video_path, time=None):
        """
        Extract a frame from the video at the specified time.
        If time is None, extract a frame at 1/3rd of the video duration.
        """
        properties = get_video_properties(video_path)
        if not properties or properties['duration'] == 0:
            raise ValueError("Invalid video properties or duration.")

        if time is None:
            time = properties['duration'] / 3

        output_frame_path = os.path.join(os.path.dirname(video_path), 'frame_preview.jpg')

        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        else:
            startupinfo = None

        cmd = [
            FFMPEG_EXECUTABLE,
            '-ss', str(time),
            '-i', os.path.normpath(video_path),
            '-frames:v', '1',
            '-q:v', '2',
            os.path.normpath(output_frame_path),
            '-y'
        ]

        subprocess.run(cmd, check=True, startupinfo=startupinfo)  # Add startupinfo here
        return output_frame_path

    def get_frame_preview(self, video_path):
        """Get a frame preview 1/3rd into the video."""
        properties = get_video_properties(video_path)
        if not properties or properties['duration'] == 0:
            raise ValueError("Invalid video properties or duration.")

        frame = self.extract_frame(video_path)
        return frame

    def is_gpu_available(self):
        """
        Check if GPU acceleration is available by:
        1. Verifying that an NVIDIA GPU is present using 'nvidia-smi'.
        2. Confirming that FFmpeg has the 'h264_nvenc' encoder.
        
        Returns:
            bool: True if both checks pass, False otherwise.
        """
        try:
            # Check for NVIDIA GPU using nvidia-smi
            result = subprocess.run(['nvidia-smi'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                logging.warning("nvidia-smi not found or no NVIDIA GPU detected.")
                return False
            logging.debug("NVIDIA GPU detected.")

            # Check if 'h264_nvenc' is available in FFmpeg encoders
            cmd = [FFMPEG_EXECUTABLE, '-encoders']
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
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

# Instantiate the ConversionManager
conversion_manager = ConversionManager()