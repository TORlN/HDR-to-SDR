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
import sys  # Added import

class ConversionManager:
    """
    Manages the video conversion process from HDR to SDR, including starting,
    monitoring progress, and cancelling the conversion.
    """
    def __init__(self):
        self.process = None       # Subprocess for the ffmpeg conversion
        self.cancelled = False    # Flag to indicate if the conversion was cancelled
        # Removed drop_target_registered attribute

    def start_conversion(self, input_path, output_path, gamma, progress_var,
                         interactable_elements, gui_instance, open_after_conversion,
                         cancel_button):
        """
        Start the conversion process.

        Args:
            input_path (str): Path to the input video file.
            output_path (str): Path to save the converted video file.
            gamma (float): Gamma correction value.
            progress_var (tk.DoubleVar): Variable to update the progress bar.
            interactable_elements (list): UI elements to disable during conversion.
            gui_instance (HDRConverterGUI): The GUI instance.
            open_after_conversion (bool): Whether to open the output file after conversion.
            cancel_button (ttk.Button): The cancel button widget.
        """
        self.cancelled = False  # Reset the cancelled flag at the start

        if not self.verify_paths(input_path, output_path):
            return

        properties = get_video_properties(input_path)
        if properties is None:
            messagebox.showwarning("Warning", "Failed to retrieve video properties.")
            return

        self.disable_ui(interactable_elements)
        cancel_button.config(command=lambda: self.cancel_conversion(
            gui_instance, interactable_elements, cancel_button))
        cancel_button.grid()  # Show cancel button

        cmd = self.construct_ffmpeg_command(input_path, output_path, gamma, properties)
        self.process = self.start_ffmpeg_process(cmd)

        # Pass the actual GUI instance to monitor_progress
        threading.Thread(target=self.monitor_progress, args=(
            progress_var, properties['duration'], gui_instance, interactable_elements,
            cancel_button, output_path, open_after_conversion)).start()

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
        startupinfo = None
        if sys.platform == 'win32':
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
        return process

    def monitor_progress(self, progress_var, duration, gui_instance, interactable_elements,
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
                # Schedule the update_idletasks to run in the main thread
                gui_instance.root.after(0, gui_instance.root.update_idletasks)

        if self.process is not None:
            self.process.wait()
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
                messagebox.showinfo(
                    "Success", f"Conversion complete! Output saved to: {output_path}")
                if open_after_conversion:
                    webbrowser.open(output_path)
            elif not self.cancelled:
                error_message = ''.join(error_messages)
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

# Instantiate the ConversionManager
conversion_manager = ConversionManager()