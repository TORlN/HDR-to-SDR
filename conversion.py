import os
import subprocess
import threading
import webbrowser
import multiprocessing
import re
from tkinter import messagebox
from utils import get_video_properties

def start_conversion(input_path_var, output_path_var, gamma_var, progress_var, open_after_conversion, browse_button, start_button, gamma_slider, root):
    """
    Starts the conversion process from HDR to SDR using the specified parameters.
    Args:
        input_path_var (tk.StringVar): The variable holding the input file path.
        output_path_var (tk.StringVar): The variable holding the output file path.
        gamma_var (tk.DoubleVar): The variable holding the gamma correction value.
        progress_var (tk.DoubleVar): The variable holding the progress percentage.
        open_after_conversion (tk.BooleanVar): A flag indicating whether to open the output file after conversion.
        browse_button (tk.Button): The browse button widget.
        start_button (tk.Button): The start button widget.
        gamma_slider (tk.Scale): The gamma slider widget.
        root (tk.Tk): The root Tkinter window.
    Returns:
        None
    """
    # Get the input and output file paths
    input_file = input_path_var.get()
    output_file = output_path_var.get()

    # Check if input and output files are selected
    if not input_file or not output_file:
        messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
        return
    
    # Check if the input file exists
    properties = get_video_properties(input_file)
    if properties is None:
        return
    
    # Get the gamma value
    gamma_value = gamma_var.get()

    # Check if the output file already exists
    if os.path.exists(output_file):
        overwrite = messagebox.askyesno("File Exists", f"{output_file} already exists. Do you want to overwrite it?")
        if not overwrite:
            return
    # Set the number of cores to use for conversion
    num_cores = multiprocessing.cpu_count()

    # Disable the browse button, start button, and gamma slider during conversion
    browse_button.config(state="disabled")
    start_button.config(state="disabled", text="Converting...")
    gamma_slider.config(state="disabled")

    # Run the ffmpeg command to convert the video
    cmd = [
        'ffmpeg', '-i', input_file,
        '-vf', f'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap=reinhard,eq=gamma={gamma_value}',
        '-c:v', properties['codec_name'],
        '-b:v', str(properties['bit_rate']),
        '-s', f"{properties['width']}x{properties['height']}",
        '-r', str(properties['frame_rate']),
        '-threads', str(num_cores),
        '-preset', 'faster',
        '-acodec', properties['audio_codec'],
        '-b:a', str(properties['audio_bit_rate']),
        output_file,
        '-y'
    ]
    # Print the command for debugging
    print(f"Running command: {' '.join(cmd)}")  # Debugging information

    # Start the conversion process
    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    
    # Define a regex pattern to extract the progress from the ffmpeg output
    progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
    
    # Define a function to update the progress bar and handle the completion status
    def update_progress():
        """
        Monitors the progress of a subprocess performing a conversion task, updates a progress bar,
        and handles the completion status.
        This function reads the standard error output of a subprocess line by line, extracts the 
        current progress using a regex pattern, and updates a progress bar accordingly. It also 
        handles the final status of the subprocess, displaying a success or error message based on 
        the return code.
        Side Effects:
            - Updates a progress bar in the GUI.
            - Displays message boxes for success or error.
            - Opens the output file in a web browser if the conversion is successful and the user 
              has opted to open the file after conversion.
            - Re-enables GUI controls after the conversion process is complete.
        Raises:
            None
        Note:
            This function assumes the existence of certain global variables and GUI elements such 
            as `process`, `progress_pattern`, `properties`, `root`, `progress_var`, `messagebox`, 
            `output_file`, `open_after_conversion`, `webbrowser`, `browse_button`, `start_button`, 
            and `gamma_slider`.
        """
        # Read the standard error output line by line
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
        # Wait for the process to complete
        process.wait()
        if process.returncode == 0:
            messagebox.showinfo("Success", f"Conversion complete! Output saved to: {output_file}")
            if open_after_conversion.get():
                webbrowser.open(output_file) # Open the output file in the default web browser/application
        else:
            messagebox.showerror("Error", f"Conversion failed with code {process.returncode}\n{error_message}")
            
        # Re-enable the GUI controls
        browse_button.config(state="normal")
        start_button.config(state="normal", text="Start Conversion")
        gamma_slider.config(state="normal")
        
    # Start the progress update thread
    threading.Thread(target=update_progress).start()