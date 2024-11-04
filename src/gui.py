import os
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import sv_ttk
from conversion import conversion_manager  # Import the conversion_manager instance
from utils import extract_frame_with_conversion, extract_frame
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES
import logging

DEFAULT_MIN_SIZE = (550, 150)

class HDRConverterGUI:
    """
    A class encapsulating the GUI components and functionality for the HDR to SDR Converter application.
    """

    def __init__(self, root):
        """Initialize the GUI and set up all components."""
        self.root = root
        self.root.title("HDR to SDR Converter")
        sv_ttk.set_theme("dark")
        self.root.minsize(*DEFAULT_MIN_SIZE)
        self.root.resizable(False, False)

        # Variables
        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.gamma_var = tk.DoubleVar(value=1.0)
        self.progress_var = tk.DoubleVar(value=0)
        self.open_after_conversion_var = tk.BooleanVar()
        self.display_image_var = tk.BooleanVar(value=True)

        # Create widgets and configure layout
        self.create_widgets()
        self.configure_grid()

        # Bind events
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
        self.drop_target_registered = True  # Set to True after registering

        # Bind the window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Removed self.process since it's managed by conversion_manager
        self.cancelled = False  # Flag to track cancellation

    def on_close(self):
        """Handle the window close event by cancelling ongoing conversions and cleaning up."""
        if conversion_manager.process and conversion_manager.process.poll() is None:
            if messagebox.askokcancel("Quit", "A conversion is in progress. Do you want to cancel and exit?"):
                conversion_manager.cancel_conversion(
                    self, self.interactable_elements, self.cancel_button
                )
                self.root.destroy()
        else:
            self.root.destroy()

    def create_widgets(self):
        """Create and arrange the widgets in the main window."""
        # Control Frame
        self.control_frame = ttk.Frame(self.root, padding="10")
        self.control_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N))

        # Input File Widgets
        ttk.Label(self.control_frame, text="Input File:").grid(row=0, column=0, sticky=tk.W)
        self.input_entry = ttk.Entry(self.control_frame, textvariable=self.input_path_var, width=40)
        self.input_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 10))
        self.browse_button = ttk.Button(
            self.control_frame,
            text="Browse",
            command=self.select_file
        )
        self.browse_button.grid(row=0, column=2, sticky=tk.W, padx=(5, 0))

        # Output File Widgets
        ttk.Label(self.control_frame, text="Output File:").grid(row=1, column=0, sticky=tk.W)
        self.output_entry = ttk.Entry(self.control_frame, textvariable=self.output_path_var, width=40)
        self.output_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(10, 10))

        # Gamma Adjustment Widgets
        ttk.Label(self.control_frame, text="Gamma:").grid(row=2, column=0, sticky=tk.W)
        self.gamma_slider = ttk.Scale(
            self.control_frame,
            variable=self.gamma_var,
            from_=0.1,
            to=3.0,
            orient=tk.HORIZONTAL,
            length=200,
            command=self.update_frame_preview
        )
        self.gamma_slider.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=(10, 10))
        self.gamma_entry = ttk.Entry(self.control_frame, textvariable=self.gamma_var, width=5)
        self.gamma_entry.grid(row=2, column=2, sticky=tk.W, padx=(5, 0))
        self.gamma_entry.bind('<Return>', self.update_frame_preview)

        # Display Image Checkbox
        self.display_image_checkbutton = ttk.Checkbutton(
            self.control_frame,
            text="Display Frame Preview",
            variable=self.display_image_var,
            command=self.update_frame_preview
        )
        self.display_image_checkbutton.grid(row=3, column=0, columnspan=3, pady=(0, 0), sticky=tk.W)

        # Image Frame for Displaying Images
        self.image_frame = ttk.Frame(self.root, padding="10")
        self.image_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.image_frame.grid_remove()

        # Image Titles
        self.original_title_label = ttk.Label(self.image_frame, text="Original (HDR):")
        self.converted_title_label = ttk.Label(self.image_frame, text="Converted (SDR):")
        # Initially hide the title labels based on display_image_var
        if not self.display_image_var.get():
            self.original_title_label.grid_remove()
            self.converted_title_label.grid_remove()
        else:
            self.original_title_label.grid(row=0, column=0, sticky=tk.W, padx=(10, 10))
            self.converted_title_label.grid(row=0, column=1, columnspan=2, sticky=tk.W, padx=(10, 10))

        # Image Labels
        self.original_image_label = ttk.Label(self.image_frame)
        self.original_image_label.grid(row=1, column=0, columnspan=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(10, 10))
        self.converted_image_label = ttk.Label(self.image_frame)
        self.converted_image_label.grid(row=1, column=1, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(10, 10))

        # Error Label
        self.error_label = ttk.Label(self.control_frame, text='', foreground='red')
        self.error_label.grid(row=4, column=0, columnspan=3, sticky=tk.W)

        # Button Frame
        self.button_frame = ttk.Frame(self.image_frame)
        self.button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
        self.button_frame.grid_remove()

        # Action Frame
        self.action_frame = ttk.Frame(self.root)
        self.action_frame.grid(row=2, column=0, pady=(10, 0), sticky=tk.N)
        self.action_frame.grid_remove()

        # Open After Conversion Checkbox
        self.open_after_conversion_checkbutton = ttk.Checkbutton(
            self.action_frame,
            text="Open output file after conversion",
            variable=self.open_after_conversion_var
        )
        self.open_after_conversion_checkbutton.grid(row=1, column=0, padx=(5, 5), sticky=tk.N)

        # Convert Button
        self.convert_button = ttk.Button(
            self.action_frame,
            text="Convert",
            command=self.convert_video
        )
        self.convert_button.grid(row=1, column=1, padx=(5, 5), pady=(0, 10), sticky=tk.N)

        # Cancel Button
        self.cancel_button = ttk.Button(
            self.action_frame,
            text="Cancel",
            command=self.cancel_conversion
        )
        self.cancel_button.grid(row=1, column=2, padx=(5, 5), pady=(0, 10), sticky=tk.N)
        self.cancel_button.grid_remove()

        # Progress Bar
        self.progress_bar = ttk.Progressbar(self.image_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E))

        # List of interactable elements
        self.interactable_elements = [
            self.browse_button, self.convert_button, self.gamma_slider,
            self.open_after_conversion_checkbutton, self.display_image_checkbutton,
            self.input_entry, self.output_entry, self.gamma_entry
        ]

    def configure_grid(self):
        """Configure the grid layout for the main window and frames."""
        # Control Frame Grid Configuration
        self.control_frame.columnconfigure(0, weight=0)
        self.control_frame.columnconfigure(1, weight=1)
        self.control_frame.columnconfigure(2, weight=0)
        for i in range(5):
            self.control_frame.rowconfigure(i, weight=0)

        # Image Frame Grid Configuration
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.columnconfigure(1, weight=1)
        self.image_frame.columnconfigure(2, weight=1)
        self.image_frame.rowconfigure(0, weight=0)
        self.image_frame.rowconfigure(1, weight=1)
        self.image_frame.rowconfigure(2, weight=0)
        self.image_frame.rowconfigure(3, weight=0)

        # Root Grid Configuration
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    def select_file(self):
        """Open a file dialog for the user to select a video file."""
        file_path = filedialog.askopenfilename(
            filetypes=[("MP4 files", "*.mp4"), ("MKV files", "*.mkv"), ("MOV files", "*.mov")]
        )
        if file_path:
            self.input_path_var.set(file_path)
            self.output_path_var.set(os.path.splitext(file_path)[0] + "_sdr.mp4")
            self.button_frame.grid()
            self.image_frame.grid()
            self.action_frame.grid()
            self.update_frame_preview()

    def update_frame_preview(self, event=None):
        """Update the frame preview images based on the selected video and gamma value."""
        if self.display_image_var.get() and self.input_path_var.get():
            try:
                video_path = self.input_path_var.get()
                if not video_path:
                    raise ValueError("No video path provided.")

                # Extract and display the frames
                self.display_frames(video_path)
                self.error_label.config(text="")
                # Show title labels
                self.original_title_label.grid()
                self.converted_title_label.grid()
                self.adjust_window_size()
                self.arrange_widgets(image_frame=True)
            except Exception as e:
                self.handle_preview_error(e)
        else:
            self.clear_preview()
            # Hide title labels
            self.original_title_label.grid_remove()
            self.converted_title_label.grid_remove()
            self.arrange_widgets(image_frame=False)

    def display_frames(self, video_path):
        """Extract and display the original and converted frames from the video."""
        original_image = extract_frame(video_path)
        original_image_resized = original_image.resize((960, 540), Image.Resampling.LANCZOS)
        original_photo = ImageTk.PhotoImage(original_image_resized)

        converted_image = extract_frame_with_conversion(video_path, self.gamma_var.get())
        converted_image_resized = converted_image.resize((960, 540), Image.Resampling.LANCZOS)
        converted_photo = ImageTk.PhotoImage(converted_image_resized)

        self.original_image_label.config(image=original_photo)
        self.original_image_label.image = original_photo
        self.converted_image_label.config(image=converted_photo)
        self.converted_image_label.image = converted_photo

    def adjust_window_size(self):
        """Adjust the window size to fit the displayed images."""
        self.root.geometry("")  # Reset window size to fit images
        self.root.update_idletasks()
        new_width = self.root.winfo_width()
        new_height = self.root.winfo_height()
        self.root.minsize(new_width, new_height)

    def arrange_widgets(self, image_frame):
        """Arrange the widgets in the appropriate frames."""
        if image_frame:
            self.button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
            self.progress_bar.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E))
        else:
            self.button_frame.grid(row=5, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
            self.progress_bar.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E))
        self.open_after_conversion_checkbutton.grid(row=1, column=0, padx=(5, 5), sticky=tk.N)
        self.convert_button.grid(row=1, column=1, padx=(5, 5), pady=(0, 10), sticky=tk.N)
        self.cancel_button.grid_remove()  # Ensure cancel button is hidden

    def handle_preview_error(self, error):
        """Handle errors that occur during frame preview update."""
        self.error_label.config(text=f"Error displaying image: {error}")
        self.clear_preview()
        self.original_title_label.grid_remove()
        self.converted_title_label.grid_remove()

    def clear_preview(self):
        """Clear the frame preview images and reset the window size."""
        self.original_image_label.config(image='')
        self.converted_image_label.config(image='')
        self.root.minsize(*DEFAULT_MIN_SIZE)

    def handle_file_drop(self, event):
        """Handle file drop events and update the input and output path variables."""
        try:
            if not self.drop_target_registered:
                return  # Ignore drop if not registered
            file_path = event.data.strip('{}')
            if file_path:
                self.input_path_var.set(file_path)
                self.output_path_var.set(os.path.splitext(file_path)[0] + "_sdr.mp4")
                self.button_frame.grid()
                self.image_frame.grid()
                self.action_frame.grid()
                self.update_frame_preview()
        except Exception as e:
            logging.error(f"Error handling file drop: {e}")
            messagebox.showerror("Error", f"Error handling file drop: {e}")

    def convert_video(self):
        """Convert the video from HDR to SDR."""
        try:
            input_path = self.input_path_var.get()
            output_path = self.output_path_var.get()
            gamma = self.gamma_var.get()

            if not input_path or not output_path:
                messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
                return

            if os.path.exists(output_path):
                answer = messagebox.askyesno("File Exists", f"The file '{output_path}' already exists. Do you want to overwrite it?")
                if not answer:
                    return

            # Unregister drop target before starting conversion
            if self.drop_target_registered:
                self.unregister_drop_target()

            # Show cancel button and disable interactable elements
            self.cancel_button.grid()
            
            # Start the conversion using the conversion_manager instance
            conversion_manager.start_conversion(
                input_path, output_path, gamma, self.progress_var,
                self.interactable_elements, self, self.open_after_conversion_var.get(),
                self.cancel_button
            )
        except Exception as e:
            messagebox.showerror("Conversion Error", f"An error occurred during conversion: {e}")

    def cancel_conversion(self):
        """Cancel the ongoing video conversion process."""
        # Use the conversion_manager to cancel the conversion
        conversion_manager.cancel_conversion(
            self, self.interactable_elements, self.cancel_button
        )
        # The drop target is re-registered within the ConversionManager's cancel_conversion method

    def register_drop_target(self):
        """Register the drag and drop target."""
        if not self.drop_target_registered:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
            self.drop_target_registered = True

    def unregister_drop_target(self):
        """Unregister the drag and drop target."""
        if self.drop_target_registered:
            self.root.drop_target_unregister()
            self.drop_target_registered = False

    def disable_ui(self, elements):
        """Disable the specified UI elements."""
        for element in elements:
            element.config(state='disabled')