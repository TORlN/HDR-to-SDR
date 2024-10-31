import ffmpeg
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import subprocess
import re
import threading
import webbrowser
import multiprocessing

def get_video_properties(input_file):
    """Use ffprobe to get relevant properties of the input file."""
    try:
        probe = ffmpeg.probe(input_file)
        video_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
        
        # Extract relevant properties
        properties = {
            "width": int(video_stream['width']),
            "height": int(video_stream['height']),
            "bit_rate": int(video_stream.get('bit_rate', 5000000)),  # Default to 5 Mbps if not available
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

def convert_hdr_to_sdr(input_file, output_file):
    properties = get_video_properties(input_file)
    
    if properties is None:
        return  # Stop if unable to retrieve properties
    
    # Get the selected gamma value from the slider
    gamma_value = gamma_var.get()
    
    # Check if the output file already exists and prompt for confirmation
    if os.path.exists(output_file):
        overwrite = messagebox.askyesno("File Exists", f"{output_file} already exists. Do you want to overwrite it?")
        if not overwrite:
            return  # Stop the process if user selects "No"

    # Set up multi-core processing
    num_cores = multiprocessing.cpu_count()
    
    # Disable buttons, gamma slider, and update "Start Conversion" button text
    browse_button.config(state="disabled")
    start_button.config(state="disabled", text="Converting...")
    gamma_slider.config(state="disabled")
    
    # Construct FFmpeg command with multi-threaded CPU processing
    cmd = [
        'ffmpeg', '-i', input_file,
        '-vf', f'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap=reinhard,eq=gamma={gamma_value}',
        '-c:v', properties['codec_name'],
        '-b:v', str(properties['bit_rate']),
        '-s', f"{properties['width']}x{properties['height']}",
        '-r', str(properties['frame_rate']),
        '-threads', str(num_cores),  # Use all available CPU cores
        '-preset', 'faster',         # Optimize speed/quality balance
        '-acodec', properties['audio_codec'],
        '-b:a', str(properties['audio_bit_rate']),
        output_file,
        '-y'  # Overwrite output file if it exists
    ]
    
    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True,
        creationflags=subprocess.CREATE_NO_WINDOW  # Prevent terminal window from opening (Windows-only)
    )
    
    progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')

    def update_progress():
        error_message = ""
        for line in process.stderr:
            error_message += line
            # Extract current time from FFmpeg's output
            match = progress_pattern.search(line)
            if match:
                current_time = match.group(1)
                hours, minutes, seconds = map(float, current_time.split(':'))
                elapsed = hours * 3600 + minutes * 60 + seconds
                progress_var.set((elapsed / properties['duration']) * 100)
                progress_bar.update_idletasks()

        process.wait()  # Wait for the process to finish
        if process.returncode == 0:
            messagebox.showinfo("Success", f"Conversion complete! Output saved to: {output_file}")
            if open_after_conversion.get():  # Check if the user wants to open the file
                webbrowser.open(output_file)  # Open file with the default media player
        else:
            messagebox.showerror("Error", f"Conversion failed with code {process.returncode}\n{error_message}")

        # Re-enable buttons, gamma slider, and reset "Start Conversion" button text
        browse_button.config(state="normal")
        start_button.config(state="normal", text="Start Conversion")
        gamma_slider.config(state="normal")

    # Run the progress update in a separate thread to avoid freezing the GUI
    threading.Thread(target=update_progress).start()

def select_file():
    file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4;*.mkv;*.mov")])
    if file_path:
        input_path_var.set(file_path)
        output_path_var.set(os.path.splitext(file_path)[0] + "_sdr.mp4")

def start_conversion():
    input_file = input_path_var.get()
    output_file = output_path_var.get()
    
    if not input_file or not output_file:
        messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
        return
    
    convert_hdr_to_sdr(input_file, output_file)

# Create the main window
root = tk.Tk()
root.title("HDR to SDR Converter")

# Input file selection
input_path_var = tk.StringVar()
output_path_var = tk.StringVar()

tk.Label(root, text="Select Input File:").pack(pady=(10, 0))
tk.Entry(root, textvariable=input_path_var, width=50).pack(pady=5)
browse_button = tk.Button(root, text="Browse...", command=select_file)
browse_button.pack()

# Output file name entry
tk.Label(root, text="Output File Name:").pack(pady=(10, 0))
tk.Entry(root, textvariable=output_path_var, width=50).pack(pady=5)

# Gamma adjustment slider
gamma_var = tk.DoubleVar(value=1.0)  # Default gamma value set to 1.0
tk.Label(root, text="Adjust Gamma:").pack(pady=(10, 0))
gamma_slider = tk.Scale(root, variable=gamma_var, from_=0.5, to=3.0, resolution=0.1, orient='horizontal', length=300)
gamma_slider.pack(pady=5)

# Progress bar
progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
progress_bar.pack(pady=10, fill='x', padx=20)

# Checkbox for opening file after conversion
open_after_conversion = tk.BooleanVar()
open_after_conversion_check = tk.Checkbutton(root, text="Open file after conversion", variable=open_after_conversion)
open_after_conversion_check.pack(pady=5)

# Start conversion button
start_button = tk.Button(root, text="Start Conversion", command=start_conversion)
start_button.pack(pady=20)

root.geometry("400x450")
root.mainloop()
