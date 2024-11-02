import os
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import sv_ttk
from conversion import start_conversion
from utils import extract_frame_with_conversion, extract_frame
from PIL import Image, ImageTk

DEFAULT_MIN_SIZE = (550, 150)

process = None  # Add this global variable to track the conversion process

def select_file(input_path_var, output_path_var, gamma_var, original_image_label, converted_image_label, display_image_var, error_label, original_title_label, converted_title_label, button_frame, image_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton, action_frame):
    """
    Opens a file dialog for the user to select a video file and sets the input and output path variables.
    Args:
        input_path_var (tkinter.StringVar): A Tkinter StringVar to store the selected input file path.
        output_path_var (tk.StringVar): A Tkinter StringVar to store the generated output file path.
        gamma_var (tk.DoubleVar): A Tkinter DoubleVar to store the gamma correction value.
        original_image_label (tk.Label): A Tkinter Label to display the original frame.
        converted_image_label (tk.Label): A Tkinter Label to display the converted frame.
        display_image_var (tk.BooleanVar): A Tkinter BooleanVar to store the state of the display image checkbox.
        error_label (tk.Label): A Tkinter Label to display error messages.
        button_frame (ttk.Frame): The frame containing the convert button and open file checkbox.
        image_frame (ttk.Frame): The frame containing the image previews.
        progress_bar (ttk.Progressbar): The progress bar widget.
        control_frame (ttk.Frame): The control frame containing the input and output widgets.
        action_frame (ttk.Frame): The frame containing the convert button, cancel button, and open file after conversion checkbox.
    """
    file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4;*.mkv;*.mov")])
    if file_path:
        input_path_var.set(file_path)
        output_path_var.set(os.path.splitext(file_path)[0] + "_sdr.mp4")
        button_frame.grid()
        image_frame.grid()
        action_frame.grid()  # Show the action frame
        update_frame_preview(input_path_var, gamma_var, original_image_label, converted_image_label, display_image_var, error_label, original_title_label, converted_title_label, button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton)

def update_frame_preview(input_path_var, gamma_var, original_image_label, converted_image_label, display_image_var, error_label, original_title_label, converted_title_label, button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton):
    if display_image_var.get() and input_path_var.get():
        try:
            video_path = input_path_var.get()
            if not video_path:
                raise ValueError("No video path provided.")

            # Extract the original frame from the video
            original_image = extract_frame(video_path)
            original_image_resized = original_image.resize((960, 540), Image.Resampling.LANCZOS)  # Resize to 540p
            original_photo = ImageTk.PhotoImage(original_image_resized)

            # Extract the converted frame from the video
            converted_image = extract_frame_with_conversion(video_path, gamma_var.get())
            converted_image_resized = converted_image.resize((960, 540), Image.Resampling.LANCZOS)  # Resize to 540p
            converted_photo = ImageTk.PhotoImage(converted_image_resized)

            # Update the image labels
            original_image_label.config(image=original_photo)
            original_image_label.image = original_photo
            converted_image_label.config(image=converted_photo)
            converted_image_label.image = converted_photo
            error_label.config(text="")
            original_title_label.grid()
            converted_title_label.grid()
            original_image_label.master.master.geometry("")  # Reset window size to fit images
            original_image_label.master.master.update_idletasks()
            new_width = original_image_label.master.master.winfo_width()
            new_height = original_image_label.master.master.winfo_height()
            original_image_label.master.master.minsize(new_width, new_height)

            # Move buttons and progress bar to the image frame
            button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
            progress_bar.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E))
        except Exception as e:
            error_label.config(text=f"Error displaying image: {e}")
            original_image_label.config(image='')
            converted_image_label.config(image='')
            original_title_label.grid_remove()
            converted_title_label.grid_remove()
            original_image_label.master.master.minsize(*DEFAULT_MIN_SIZE)  # Revert to default minimum size
    else:
        original_image_label.config(image='')
        converted_image_label.config(image='')
        original_title_label.grid_remove()
        converted_title_label.grid_remove()
        original_image_label.master.master.minsize(*DEFAULT_MIN_SIZE)  # Revert to default minimum size

        # Move buttons and progress bar to the control frame
        button_frame.grid(row=5, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
        progress_bar.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E))
        convert_button.grid(row=1, column=1, columnspan=1, sticky=tk.N)  # Center convert button
        open_after_conversion_checkbutton.grid(row=0, column=0, columnspan=3, pady=(0, 5), sticky=tk.N)  # Center open after conversion checkbox

def create_main_window(root):
    """
    Creates the main window for the HDR to SDR Converter application.
    Parameters:
    root (tk.Tk): The root window of the Tkinter application.
    """
    root.title("HDR to SDR Converter")
    sv_ttk.set_theme("dark")
    root.minsize(*DEFAULT_MIN_SIZE)  # Set minimum window size
    root.resizable(False, False)  # Disable window resizing

    input_path_var = tk.StringVar()
    output_path_var = tk.StringVar()
    gamma_var = tk.DoubleVar(value=1.0)
    progress_var = tk.DoubleVar(value=0)
    open_after_conversion_var = tk.BooleanVar()
    display_image_var = tk.BooleanVar(value=True)
    
    # Main Frame for Input, Output, and Controls
    control_frame = ttk.Frame(root, padding="10")
    control_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N))
    
    # Input File Widgets
    ttk.Label(control_frame, text="Input File:").grid(row=0, column=0, sticky=tk.W)
    input_entry = ttk.Entry(control_frame, textvariable=input_path_var, width=40)
    input_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 10))  # Adjusted padding
    browse_button = ttk.Button(
        control_frame, 
        text="Browse", 
        command=lambda: select_file(
            input_path_var, output_path_var, gamma_var,
            original_image_label, converted_image_label, display_image_var, error_label, original_title_label, converted_title_label,
            button_frame, image_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton, action_frame
        )
    )
    browse_button.grid(row=0, column=2, sticky=tk.W, padx=(5, 0))  # Added padding
    
    # Output File Widgets
    ttk.Label(control_frame, text="Output File:").grid(row=1, column=0, sticky=tk.W)
    output_entry = ttk.Entry(control_frame, textvariable=output_path_var, width=40)  # Set width to 40
    output_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(10, 10))  # Adjusted padding
    
    # Gamma Adjustment Widgets
    ttk.Label(control_frame, text="Gamma:").grid(row=2, column=0, sticky=tk.W)  # Renamed to "Gamma"
    gamma_slider = ttk.Scale(
        control_frame, 
        variable=gamma_var, 
        from_=0.1, 
        to=3.0, 
        orient=tk.HORIZONTAL, 
        length=200,
        command=lambda value: update_frame_preview(
            input_path_var, gamma_var, original_image_label,
            converted_image_label, display_image_var, error_label, original_title_label, converted_title_label,
            button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton
        )
    )
    gamma_slider.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=(10, 10))  # Adjusted padding
    gamma_entry = ttk.Entry(control_frame, textvariable=gamma_var, width=5)
    gamma_entry.grid(row=2, column=2, sticky=tk.W, padx=(5, 0))  # Added padding to align with browse button
    gamma_entry.bind(
        '<Return>', 
        lambda event: update_frame_preview(
            input_path_var, gamma_var, original_image_label, 
            converted_image_label, display_image_var, error_label, original_title_label, converted_title_label,
            button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton
        )
    )
    
    # Display Image Checkbox
    display_image_checkbutton = ttk.Checkbutton(
        control_frame,
        text="Display Frame Preview",
        variable=display_image_var,
        command=lambda: update_frame_preview(
            input_path_var, gamma_var, original_image_label,
            converted_image_label, display_image_var, error_label, original_title_label, converted_title_label,
            button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton
        )
    )
    display_image_checkbutton.grid(row=3, column=0, columnspan=3, sticky=tk.W)
    
    # Image Frame for Displaying Images
    image_frame = ttk.Frame(root, padding="10")
    image_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
    image_frame.grid_remove()
    
    # Image Titles
    original_title_label = ttk.Label(image_frame, text="Original (HDR):")
    original_title_label.grid(row=0, column=0, sticky=tk.W, padx=(10, 10))
    converted_title_label = ttk.Label(image_frame, text="Converted (SDR):")
    converted_title_label.grid(row=0, column=1, columnspan=2, sticky=tk.W, padx=(10, 10))
    original_title_label.grid_remove()
    converted_title_label.grid_remove()
    
    # Image Labels
    original_image_label = ttk.Label(image_frame)
    original_image_label.grid(row=1, column=0, columnspan=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(10, 10))
    converted_image_label = ttk.Label(image_frame)
    converted_image_label.grid(row=1, column=1, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(10, 10))
    
    # Error Label
    error_label = ttk.Label(control_frame, text='', foreground='red')
    error_label.grid(row=4, column=0, columnspan=3, sticky=tk.W)

    # Conversion Button and Progress Bar
    button_frame = ttk.Frame(image_frame)
    button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
    button_frame.grid_remove()
    
    # New Frame for Convert Button and Open After Conversion Checkbox
    action_frame = ttk.Frame(root)
    action_frame.grid(row=2, column=0, pady=(10, 0), sticky=tk.N)
    action_frame.grid_remove()  # Hide the action frame initially
    
    # Open After Conversion Checkbox
    open_after_conversion_checkbutton = ttk.Checkbutton(
        action_frame,
        text="Open output file after conversion",
        variable=open_after_conversion_var
    )
    open_after_conversion_checkbutton.grid(row=0, column=0, pady=(0, 5), sticky=tk.W)  # Move to the left
    
    convert_button = ttk.Button(
        action_frame, 
        text="Convert",
        command=lambda: convert_video(
            input_path_var.get(), output_path_var.get(), gamma_var.get(),
            progress_var, open_after_conversion_var.get(), interactable_elements, root, cancel_button
        )
    )
    convert_button.grid(row=0, column=1, padx=(5, 5), sticky=tk.N)  # Center convert button
    
    cancel_button = ttk.Button(
        action_frame, 
        text="Cancel",
        command=lambda: cancel_conversion(process, interactable_elements, cancel_button)
    )
    cancel_button.grid(row=0, column=2, padx=(5, 5), sticky=tk.N)  # Adjusted padding for centering
    cancel_button.grid_remove()  # Hide cancel button initially
    
    progress_bar = ttk.Progressbar(image_frame, variable=progress_var, maximum=100)
    progress_bar.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E))
    
    interactable_elements = [
        browse_button, convert_button, gamma_slider, open_after_conversion_checkbutton, display_image_checkbutton, input_entry, output_entry, gamma_entry
    ]
    
    convert_button.config(
        command=lambda: convert_video(
            input_path_var.get(), output_path_var.get(), gamma_var.get(),
            progress_var, open_after_conversion_var.get(), interactable_elements, root, cancel_button
        )
    )
    
    # Configure column and row weights
    control_frame.columnconfigure(0, weight=1)
    control_frame.columnconfigure(1, weight=1)
    control_frame.columnconfigure(2, weight=1)
    control_frame.rowconfigure(0, weight=0)
    control_frame.rowconfigure(1, weight=0)
    control_frame.rowconfigure(2, weight=0)
    control_frame.rowconfigure(3, weight=0)
    control_frame.rowconfigure(4, weight=0)
    control_frame.rowconfigure(5, weight=0)
    
    image_frame.columnconfigure(0, weight=1)
    image_frame.columnconfigure(1, weight=1)
    image_frame.columnconfigure(2, weight=1)
    image_frame.rowconfigure(0, weight=0)
    image_frame.rowconfigure(1, weight=1)
    image_frame.rowconfigure(2, weight=0)
    image_frame.rowconfigure(3, weight=0)
    
    # Adjust root grid configuration to remove excess space
    root.grid_rowconfigure(0, weight=0)
    root.grid_rowconfigure(1, weight=1)
    root.grid_columnconfigure(0, weight=1)
    
    # Initial Frame Preview Update
    if input_path_var.get():
        update_frame_preview(
            input_path_var, gamma_var, original_image_label,
            converted_image_label, display_image_var, error_label,
            original_title_label, converted_title_label,
            button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton
        )

def validate_gamma_entry(value, gamma_var, input_path_var, original_image_label, converted_image_label, display_image_var, error_label, original_title_label, converted_title_label, button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton):
    try:
        value = float(value)
        gamma_var.set(value)
        update_frame_preview(input_path_var, gamma_var, original_image_label, converted_image_label, display_image_var, error_label, original_title_label, converted_title_label, button_frame, progress_bar, control_frame, convert_button, open_after_conversion_checkbutton)
        error_label.config(text="")
        return True
    except ValueError:
        error_label.config(text="Please enter a valid float value for gamma.")
        return False

def convert_video(input_path, output_path, gamma, progress_var, open_after_conversion, interactable_elements, root, cancel_button):
    """
    Converts the video from HDR to SDR.
    Args:
        input_path (str): The path to the input video file.
        output_path (str): The path to save the converted video file.
        gamma (float): The gamma correction value.
        progress_var (tk.DoubleVar): A Tkinter DoubleVar to update the progress bar.
        open_after_conversion (bool): Whether to open the output file after conversion.
        interactable_elements (list): List of interactable elements to be disabled during conversion.
        root (tk.Tk): The root Tkinter window.
        cancel_button (ttk.Button): The cancel button widget.
    """
    global process  # Ensure process is global to allow cancellation
    try:
        if os.path.exists(output_path):
            answer = messagebox.askyesno("File Exists", f"The file '{output_path}' already exists. Do you want to overwrite it?")
            if not answer:
                return
        
        cancel_button.grid()  # Show cancel button only after user confirms
        process = start_conversion(input_path, output_path, gamma, progress_var, interactable_elements, root, open_after_conversion, cancel_button)
    except Exception as e:
        messagebox.showerror("Conversion Error", f"An error occurred during conversion: {e}")

def cancel_conversion(process, interactable_elements, cancel_button):
    """
    Cancels the ongoing video conversion process.
    Args:
        process (subprocess.Popen): The process to be terminated.
        interactable_elements (list): List of interactable elements to be re-enabled.
        cancel_button (ttk.Button): The cancel button widget.
    """
    if process:
        process.terminate()
        process = None  # Reset the process variable
        messagebox.showinfo("Cancelled", "Video conversion has been cancelled.")
        for element in interactable_elements:
            element.config(state="normal")
        cancel_button.grid_remove()  # Hide cancel button