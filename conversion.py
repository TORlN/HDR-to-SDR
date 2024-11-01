import os
import subprocess
import threading
import webbrowser
import multiprocessing
import re
from tkinter import messagebox
from utils import get_video_properties

def start_conversion(input_path_var, output_path_var, gamma_var, progress_var, open_after_conversion, browse_button, start_button, gamma_slider, root):
    input_file = input_path_var.get()
    output_file = output_path_var.get()

    if not input_file or not output_file:
        messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
        return

    properties = get_video_properties(input_file)
    if properties is None:
        return

    gamma_value = gamma_var.get()

    if os.path.exists(output_file):
        overwrite = messagebox.askyesno("File Exists", f"{output_file} already exists. Do you want to overwrite it?")
        if not overwrite:
            return

    num_cores = multiprocessing.cpu_count()

    browse_button.config(state="disabled")
    start_button.config(state="disabled", text="Converting...")
    gamma_slider.config(state="disabled")

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

    print(f"Running command: {' '.join(cmd)}")  # Debugging information

    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')

    def update_progress():
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
            messagebox.showinfo("Success", f"Conversion complete! Output saved to: {output_file}")
            if open_after_conversion.get():
                webbrowser.open(output_file)
        else:
            messagebox.showerror("Error", f"Conversion failed with code {process.returncode}\n{error_message}")
        browse_button.config(state="normal")
        start_button.config(state="normal", text="Start Conversion")
        gamma_slider.config(state="normal")

    threading.Thread(target=update_progress).start()