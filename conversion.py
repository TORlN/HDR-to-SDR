import os
import subprocess
import threading
import webbrowser
import multiprocessing
import re
from tkinter import messagebox
from utils import get_video_properties

cancelled = False  # Flag to track if the cancel button was hit
process = None  # Global process variable

def cancel_conversion():
    global cancelled, process
    cancelled = True
    if process:
        process.terminate()

def start_conversion(input_path, output_path, gamma, progress_var, interactable_elements, root, open_after_conversion, cancel_button):
    """
    Starts the conversion process from HDR to SDR.
    Args:
        input_path (str): The path to the input video file.
        output_path (str): The path to save the converted video file.
        gamma (float): The gamma correction value.
        progress_var (tk.DoubleVar): A Tkinter DoubleVar to update the progress bar.
        interactable_elements (list): List of interactable elements to be disabled during conversion.
        root (tk.Tk): The root Tkinter window.
        open_after_conversion (bool): Whether to open the output file after conversion.
        cancel_button (ttk.Button): The cancel button widget.
    """
    global process, cancelled
    cancelled = False  # Reset the cancelled flag at the start of conversion

    # Check if input and output files are selected
    if not input_path or not output_path:
        messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
        return
    
    # Check if the input file exists
    properties = get_video_properties(input_path)
    if properties is None:
        return
    
    # Set the number of cores to use for conversion
    num_cores = multiprocessing.cpu_count()

    # Disable all interactable elements during conversion
    for element in interactable_elements:
        element.config(state="disabled")

    # Attach the cancel_conversion function to the cancel button
    cancel_button.config(command=cancel_conversion)

    # Construct the ffmpeg command to convert the video
    cmd = [
        'ffmpeg', '-i', input_path,
        '-vf', f'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap=reinhard,eq=gamma={gamma},scale={properties["width"]}:{properties["height"]}',
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
    
    # Print the command for debugging
    print(f"Running command: {' '.join(cmd)}")

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    # Start the conversion process
    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True,
        startupinfo=startupinfo
    )
    
    # Define a regex pattern to extract the progress from the ffmpeg output
    progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
    
    def update_progress():
        """
        Monitors the progress of the conversion task, updates the progress bar,
        and handles the completion status.
        """
        error_message = ""
        for line in process.stderr:
            print(line)  # Debugging information
            error_message += line
            match = progress_pattern.search(line)
            if match:
                current_time = match.group(1)
                hours, minutes, seconds = map(float, current_time.split(':'))
                elapsed = hours * 3600 + minutes * 60 + seconds
                progress_var.set((elapsed / properties['duration']) * 100)
                root.update_idletasks()  # Update the progress bar in the main thread
        
        process.wait()
        if process.returncode == 0:
            messagebox.showinfo("Success", f"Conversion complete! Output saved to: {output_path}")
            if open_after_conversion:
                webbrowser.open(output_path)
        elif not cancelled:
            messagebox.showerror("Error", f"Conversion failed with code {process.returncode}\n{error_message}")
        else:
            messagebox.showwarning("Cancelled", "Conversion was cancelled.")
            
        # Re-enable all interactable elements after conversion
        for element in interactable_elements:
            element.config(state="normal")
        cancel_button.grid_remove()  # Hide cancel button
        
    # Start the progress update thread
    threading.Thread(target=update_progress).start()
    
    return process  # Return the process object