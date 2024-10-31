import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from conversion import start_conversion

def select_file(input_path_var, output_path_var):
    file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4;*.mkv;*.mov")])
    if file_path:
        input_path_var.set(file_path)
        output_path_var.set(os.path.splitext(file_path)[0] + "_sdr.mp4")

def create_main_window(root):
    root.title("HDR to SDR Converter")

    input_path_var = tk.StringVar()
    output_path_var = tk.StringVar()
    gamma_var = tk.DoubleVar(value=1.0)
    progress_var = tk.DoubleVar()
    open_after_conversion = tk.BooleanVar()

    tk.Label(root, text="Select Input File:").pack(pady=(10, 0))
    tk.Entry(root, textvariable=input_path_var, width=50).pack(pady=5)
    browse_button = tk.Button(root, text="Browse...", command=lambda: select_file(input_path_var, output_path_var))
    browse_button.pack()

    tk.Label(root, text="Output File Name:").pack(pady=(10, 0))
    tk.Entry(root, textvariable=output_path_var, width=50).pack(pady=5)

    tk.Label(root, text="Adjust Gamma:").pack(pady=(10, 0))
    gamma_slider = tk.Scale(root, variable=gamma_var, from_=0.5, to=3.0, resolution=0.1, orient='horizontal', length=300)
    gamma_slider.pack(pady=5)

    progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
    progress_bar.pack(pady=10, fill='x', padx=20)

    open_after_conversion_check = tk.Checkbutton(root, text="Open file after conversion", variable=open_after_conversion)
    open_after_conversion_check.pack(pady=5)

    start_button = tk.Button(root, text="Start Conversion", command=lambda: start_conversion(input_path_var, output_path_var, gamma_var, progress_var, open_after_conversion, browse_button, start_button, gamma_slider))
    start_button.pack(pady=20)

    root.geometry("400x450")