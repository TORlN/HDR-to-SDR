import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from conversion import start_conversion

def select_file(input_path_var, output_path_var):
    """
    Opens a file dialog for the user to select a video file and sets the input and output path variables.
    Args:
        input_path_var (tkinter.StringVar): A Tkinter StringVar to store the selected input file path.
        output_path_var (tkinter.StringVar): A Tkinter StringVar to store the generated output file path.
    The function opens a file dialog that filters for video files with extensions .mp4, .mkv, and .mov.
    If a file is selected, it sets the input_path_var to the selected file path and sets the output_path_var
    to the same path with "_sdr.mp4" appended to the filename (replacing the original extension).
    """
    # Open a file dialog to select a video file
    file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4;*.mkv;*.mov")])
    if file_path:
        input_path_var.set(file_path)
        output_path_var.set(os.path.splitext(file_path)[0] + "_sdr.mp4")

def create_main_window(root):
    """
    Creates the main window for the HDR to SDR Converter application.
    Parameters:
    root (tk.Tk): The root window of the Tkinter application.
    This function sets up the main window with the following components:
    - Title of the window.
    - Input file selection with a browse button.
    - Output file name entry.
    - Gamma adjustment slider.
    - Progress bar to show conversion progress.
    - Checkbox to open the file after conversion.
    - Start conversion button.
    The function also sets the geometry of the window.
    """
    # Set the title of the main window
    root.title("HDR to SDR Converter")

    # Initialize Tkinter variables to store input and output paths, gamma value, progress, and checkbox state
    input_path_var = tk.StringVar()
    output_path_var = tk.StringVar()
    gamma_var = tk.DoubleVar(value=1.0)
    progress_var = tk.DoubleVar()
    open_after_conversion = tk.BooleanVar()

    # Create and pack the widgets for input file selection, output file name, gamma adjustment, and progress bar
    tk.Label(root, text="Select Input File:").pack(pady=(10, 0))
    tk.Entry(root, textvariable=input_path_var, width=50).pack(pady=5)
    browse_button = tk.Button(root, text="Browse...", command=lambda: select_file(input_path_var, output_path_var))
    browse_button.pack()

    # Create and pack the widgets for output file name, gamma adjustment, and progress bar
    tk.Label(root, text="Output File Name:").pack(pady=(10, 0))
    tk.Entry(root, textvariable=output_path_var, width=50).pack(pady=5)

    # Create and pack the widgets for gamma adjustment and progress bar
    tk.Label(root, text="Adjust Gamma:").pack(pady=(10, 0))
    gamma_slider = tk.Scale(root, variable=gamma_var, from_=0.5, to=3.0, resolution=0.1, orient='horizontal', length=300)
    gamma_slider.pack(pady=5)

    # Create and pack the widgets for progress bar and open after conversion checkbox
    progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
    progress_bar.pack(pady=10, fill='x', padx=20)

    # Create and pack the widgets for open after conversion checkbox
    open_after_conversion_check = tk.Checkbutton(root, text="Open file after conversion", variable=open_after_conversion)
    open_after_conversion_check.pack(pady=5)
    
    # Create and pack the start conversion button
    start_button = tk.Button(root, text="Start Conversion", command=lambda: start_conversion(input_path_var, output_path_var, gamma_var, progress_var, open_after_conversion, browse_button, start_button, gamma_slider, root))
    start_button.pack(pady=20)
    
    # Set the geometry of the main window
    root.geometry("400x450")