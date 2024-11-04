import os
import subprocess
import threading
import webbrowser
import multiprocessing
import re
import logging
from tkinter import messagebox
from utils import get_video_properties, FFMPEG_FILTER
from tkinterdnd2 import DND_FILES

class ConversionManager:
    """
    Manages the video conversion process from HDR to SDR, including starting,
    monitoring progress, and cancelling the conversion.
    """
    def __init__(self):
        self.process = None       # Subprocess for the ffmpeg conversion
        self.cancelled = False    # Flag to indicate if the conversion was cancelled
        self.drop_target_registered = False  # Track drop target registration status

    def start_conversion(self, input_path, output_path, gamma, progress_var,
                         interactable_elements, root, open_after_conversion,
                         cancel_button):
        """
        Start the conversion process.

        Args:
            input_path (str): Path to the input video file.
            output_path (str): Path to save the converted video file.
            gamma (float): Gamma correction value.
            progress_var (tk.DoubleVar): Variable to update the progress bar.
            interactable_elements (list): UI elements to disable during conversion.
            root (tk.Tk): The root Tkinter window.
            open_after_conversion (bool): Whether to open the output file after conversion.
            cancel_button (ttk.Button): The cancel button widget.
        """
        self.cancelled = False  # Reset the cancelled flag at the start

        if not self.verify_paths(input_path, output_path):
            return

        properties = get_video_properties(input_path)
        if properties is None:
            return

        self.disable_ui(interactable_elements)
        cancel_button.config(command=lambda: self.cancel_conversion(
            root, interactable_elements, cancel_button))
        cancel_button.grid()  # Show cancel button

        cmd = self.construct_ffmpeg_command(input_path, output_path, gamma, properties)
        self.process = self.start_ffmpeg_process(cmd)

        threading.Thread(target=self.monitor_progress, args=(
            progress_var, properties['duration'], root, interactable_elements,
            cancel_button, output_path, open_after_conversion)).start()

        # Safely unregister drop target
        if self.drop_target_registered:
            try:
                root.drop_target_unregister()
                self.drop_target_registered = False
            except Exception as e:
                logging.error(f"Error unregistering drop target: {e}")

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

    def construct_ffmpeg_command(self, input_path, output_path, gamma, properties):
        """Construct the ffmpeg command for video conversion."""
        num_cores = multiprocessing.cpu_count()
        cmd = [
            'ffmpeg', '-loglevel', 'info',  # Set log level to info to enable progress messages
            '-i', input_path,
            '-vf', FFMPEG_FILTER.format(
                gamma=gamma, width=properties["width"], height=properties["height"]),
            '-c:v', properties['codec_name'],
            '-b:v', str(properties['bit_rate']),
            '-r', str(properties['frame_rate']),
            '-aspect', f'{properties["width"]}/{properties["height"]}',
            '-threads', str(num_cores),
            '-preset', 'faster',
            '-acodec', properties['audio_codec'],
            '-b:a', str(properties['audio_bit_rate']),
            output_path,
            '-y'
        ]
        logging.debug(f"Running command: {' '.join(cmd)}")
        return cmd

    def start_ffmpeg_process(self, cmd):
        """Start the ffmpeg subprocess with the given command."""
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        process = subprocess.Popen(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
            universal_newlines=True, startupinfo=startupinfo
        )
        return process

    def monitor_progress(self, progress_var, duration, root, interactable_elements,
                         cancel_button, output_path, open_after_conversion):
        """
        Monitor the conversion progress and update the UI accordingly.
        """
        progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
        error_messages = []

        for line in self.process.stderr:
            if self.process is None:
                return  # Exit if the process is None (cancelled)
            logging.debug(line.strip())
            error_messages.append(line)
            match = progress_pattern.search(line)
            if match:
                elapsed_time = self.parse_time(match.group(1))
                progress = (elapsed_time / duration) * 100
                progress_var.set(progress)
                root.update_idletasks()  # Update the progress bar

        if self.process is not None:
            self.process.wait()
            self.handle_completion(root, interactable_elements, cancel_button,
                                   output_path, open_after_conversion, error_messages)

    def parse_time(self, time_str):
        """Convert ffmpeg time string to seconds."""
        hours, minutes, seconds = map(float, time_str.split(':'))
        return hours * 3600 + minutes * 60 + seconds

    def handle_completion(self, root, interactable_elements, cancel_button,
                          output_path, open_after_conversion, error_messages):
        """
        Handle the completion of the conversion process, whether successful or not.
        """
        if self.process and self.process.returncode == 0:
            messagebox.showinfo(
                "Success", f"Conversion complete! Output saved to: {output_path}")
            if open_after_conversion:
                webbrowser.open(output_path)
        elif not self.cancelled:
            error_message = ''.join(error_messages)
            messagebox.showerror(
                "Error", f"Conversion failed with code {self.process.returncode}\n{error_message}")
        else:
            messagebox.showwarning("Cancelled", "Conversion was cancelled.")

        self.enable_ui(interactable_elements)
        cancel_button.grid_remove()  # Hide the cancel button

        # Safely re-register drop target
        if not self.drop_target_registered:
            try:
                root.drop_target_register(DND_FILES)
                self.drop_target_registered = True
            except Exception as e:
                logging.error(f"Error registering drop target: {e}")

    def cancel_conversion(self, root, interactable_elements, cancel_button):
        """
        Cancel the ongoing conversion process and reset the UI.
        """
        self.cancelled = True
        if self.process:
            self.process.terminate()
            self.process = None
            messagebox.showinfo("Cancelled", "Video conversion has been cancelled.")
            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()

            # Safely re-register drop target
            if not self.drop_target_registered:
                try:
                    root.drop_target_register(DND_FILES)
                    self.drop_target_registered = True
                except Exception as e:
                    logging.error(f"Error registering drop target: {e}")

# Instantiate the ConversionManager
conversion_manager = ConversionManager()