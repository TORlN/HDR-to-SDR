import os
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import sv_ttk
from conversion import conversion_manager  # Import the conversion_manager instance
from utils import extract_frame_with_conversion, extract_frame, TONEMAP, get_video_properties  # Add get_video_properties
from PIL import Image, ImageTk, ImageOps  # Add this import
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
        self.original_image = None  # Cache for the original frame
        self.converted_image_base = None  # Cache for the converted SDR frame
        self.gpu_accel_var = tk.BooleanVar(value=False)
        self.filter_options = ['Static', 'Dynamic']
        self.filter_var = tk.StringVar(value=self.filter_options[1])  # Set default to 'Dynamic'
        self.tonemap_var = tk.StringVar(value='Mobius')  # Set default to 'Mobius'
        self.tooltip = None  # Add this line for tooltip tracking
        self.current_frame_index = 1  # Default to 1 (1/6 of the video)
        self.total_frames = 5
        self.last_time_position = None

        # Create widgets and configure layout
        self.create_widgets()
        self.configure_grid()

        # Bind events
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
        self.drop_target_registered = True  # Set to True after registering

        # Bind the window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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

        # GPU Acceleration Checkbox
        self.gpu_accel_checkbutton = ttk.Checkbutton(
            self.control_frame,
            text="Enable GPU Acceleration",
            variable=self.gpu_accel_var,
            command=self.check_gpu_acceleration
        )
        self.gpu_accel_checkbutton.grid(row=3, column=0, sticky=tk.W, pady=(5, 0))

        # Add Filter Combobox with padding and event binding
        filter_frame = ttk.Frame(self.control_frame)
        filter_frame.grid(row=3, column=1, sticky=tk.W, padx=(5, 10), pady=(5, 0))
        
        self.filter_combobox = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=self.filter_options,
            state='readonly',
            width=15
        )
        self.filter_combobox.grid(row=0, column=0, padx=(0, 5))
        self.filter_combobox.bind('<<ComboboxSelected>>', self.update_frame_preview)

        # Add info button with tooltip
        info_button = ttk.Label(
            filter_frame,
            text="ⓘ",
            cursor="hand2"
        )
        info_button.grid(row=0, column=1)
        
        # Tooltip text
        tooltip_text = ("Static: Basic HDR to SDR conversion with fixed parameters\n"
                       "Dynamic: Adaptive conversion that analyzes video brightness")

        # Bind hover events
        info_button.bind('<Enter>', lambda e: self.show_tooltip(e, tooltip_text))
        info_button.bind('<Leave>', self.hide_tooltip)

        # Move tonemapper to a new frame next to display image checkbox
        display_frame = ttk.Frame(self.control_frame)
        display_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))

        # Display Image Checkbox in the new frame
        self.display_image_checkbutton = ttk.Checkbutton(
            display_frame,
            text="Display Frame Preview",
            variable=self.display_image_var,
            command=self.update_frame_preview
        )
        self.display_image_checkbutton.grid(row=0, column=0, sticky=tk.W)

        # Add Tonemapper Combobox in the new frame
        self.tonemap_combobox = ttk.Combobox(
            display_frame,
            textvariable=self.tonemap_var,
            values=TONEMAP,
            state='readonly',
            width=15
        )
        self.tonemap_combobox.grid(row=0, column=1, padx=(18, 0), sticky=tk.W)
        self.tonemap_combobox.bind('<<ComboboxSelected>>', self.update_frame_preview)

        # Add Tonemapper Info Button with tooltip
        info_button_tonemap = ttk.Label(
            display_frame,
            text="ⓘ",
            cursor="hand2"
        )
        info_button_tonemap.grid(row=0, column=2, padx=(5, 0))
        
        # Tooltip text for tonemapper
        tooltip_text_tonemap = (
            "Reinhard: Basic HDR to SDR conversion\n"
            "Mobius: Natural-looking conversion\n"
            "Hable: Game-like conversion (Cyberpunk 2077)"
        )
        
        # Bind hover events for tonemapper tooltip
        info_button_tonemap.bind('<Enter>', lambda e: self.show_tooltip(e, tooltip_text_tonemap))
        info_button_tonemap.bind('<Leave>', self.hide_tooltip)

        # Update tooltip text to include tonemapper info
        tooltip_text = ("Static: Basic HDR to SDR conversion with fixed parameters\n"
                       "Dynamic: Adaptive conversion that analyzes video brightness\n\n"
                       "Tonemappers:\n"
                       "Reinhard: Basic HDR to SDR conversion\n"
                       "Mobius: Natural-looking conversion\n"
                       "Hable: Game-like conversion (Cyberpunk 2077)")

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
        self.converted_image_label.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(10, 0))

        self.button_container = ttk.Frame(self.image_frame)  # New container frame
        self.button_container.grid(row=1, column=2, sticky=(tk.N), padx=(5, 10))
        self.button_container.grid_remove()  # Initially hide the button container
        
        self.frame_buttons = []  # List to store frame buttons
        style = ttk.Style()
        style.configure('Selected.TButton', relief='sunken')  # Style for selected button

        for i in range(1, 6):
            btn = ttk.Button(self.button_container, text=str(i), 
                           command=lambda idx=i: self.on_frame_button_click(idx))
            btn.grid(row=i-1, column=0, pady=5)
            self.frame_buttons.append(btn)

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
            self.input_entry, self.output_entry, self.gamma_entry, self.gpu_accel_checkbutton
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
            filetypes=[
                ("All Video Files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                ("MP4 files", "*.mp4"),
                ("MKV files", "*.mkv"),
                ("MOV files", "*.mov"),
                ("AVI files", "*.avi"),
                ("WebM files", "*.webm"),
                ("M4V files", "*.m4v"),
                ("All files", "*.*")
            ]
        )
        if file_path:
            self.input_path_var.set(file_path)
            # Keep the same extension for output file
            base, ext = os.path.splitext(file_path)
            self.output_path_var.set(f"{base}_sdr{ext}")
            # Reset the cached images
            self.original_image = None
            self.converted_image_base = None
            self.button_frame.grid()
            self.image_frame.grid()
            self.action_frame.grid()
            self.update_frame_preview()
            self.highlight_frame_button(1)  # Highlight button 1 when image is loaded

    def adjust_gamma(self, image, gamma):
        """Adjust gamma of a PIL.Image."""
        inv_gamma = 1.0 / gamma
        lut = [pow(i / 255.0, inv_gamma) * 255 for i in range(256)]
        # Extend LUT for all channels
        lut = lut * len(image.getbands())
        lut = [int(round(v)) for v in lut]  # Ensure values are integers
        return image.point(lut)

    def display_frames(self, video_path):
        """Extract and display frames using cached images and adjusted gamma."""
        if self.original_image is None:
            # Extract the original HDR frame
            self.original_image = extract_frame(video_path)
        # Re-extract the converted SDR frame with the selected filter
        selected_filter_index = self.filter_options.index(self.filter_var.get())
        tonemapper = self.tonemap_var.get().lower()  # Convert tonemapper to lowercase
        self.converted_image_base = extract_frame_with_conversion(
            video_path, gamma=1.0, filter_index=selected_filter_index,
            tonemapper=tonemapper  # Pass current tonemapper
        )
        original_image_resized = self.original_image.resize((960, 540), Image.LANCZOS)
        original_photo = ImageTk.PhotoImage(original_image_resized)
        self.original_image_label.config(image=original_photo)
        self.original_image_label.image = original_photo

        # Apply gamma adjustment to the cached converted SDR image
        gamma = self.gamma_var.get()
        adjusted_converted_image = self.adjust_gamma(self.converted_image_base, gamma)
        converted_image_resized = adjusted_converted_image.resize((960, 540), Image.LANCZOS)
        converted_photo = ImageTk.PhotoImage(converted_image_resized)
        self.converted_image_label.config(image=converted_photo)
        self.converted_image_label.image = converted_photo

        self.adjust_window_size()  # Ensure window size is adjusted after displaying images

    def update_frame_preview(self, event=None):
        """Update the frame preview without blocking the UI."""
        filter_value = self.filter_var.get()
        
        if self.display_image_var.get() and self.input_path_var.get():
            try:
                video_path = self.input_path_var.get()
                self.display_frames(video_path)
                self.error_label.config(text="")
                self.original_title_label.grid()
                self.converted_title_label.grid()
                self.button_container.grid()  # Show frame buttons
                self.adjust_window_size()
                self.arrange_widgets(image_frame=True)
            except Exception as e:
                self.handle_preview_error(e)
        else:
            self.clear_preview()
            self.original_title_label.grid_remove()
            self.converted_title_label.grid_remove()
            self.button_container.grid_remove()  # Hide frame buttons
            self.arrange_widgets(image_frame=False)
        self.filter_combobox.selection_clear()
        self.tonemap_combobox.selection_clear()

    def clear_preview(self):
        """Clear the frame preview images and reset cached images."""
        self.original_image_label.config(image='')
        self.converted_image_label.config(image='')
        self.original_image = None
        self.converted_image_base = None
        self.root.minsize(*DEFAULT_MIN_SIZE)

    def adjust_window_size(self):
        """Adjust the window size to fit the displayed images."""
        self.root.geometry("")  # Reset window size to fit images
        self.root.update_idletasks()
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        new_width = self.root.winfo_width()
        new_height = self.root.winfo_height()

        if new_width > screen_width or new_height > screen_height:
            # Reduce image size to fit within screen bounds
            max_width = screen_width - 100  # Leave some margin
            max_height = screen_height - 100  # Leave some margin
            self.resize_images(max_width, max_height)
            self.root.geometry("")  # Reset window size again after resizing images
            self.root.update_idletasks()
            new_width = self.root.winfo_width()
            new_height = self.root.winfo_height()

        self.root.minsize(new_width, new_height)

    def resize_images(self, max_width, max_height):
        """Resize images to fit within the specified maximum width and height."""
        if self.original_image:
            original_image_resized = self.original_image.resize((max_width // 2, max_height // 2), Image.LANCZOS)
            original_photo = ImageTk.PhotoImage(original_image_resized)
            self.original_image_label.config(image=original_photo)
            self.original_image_label.image = original_photo

        if self.converted_image_base:
            gamma = self.gamma_var.get()
            adjusted_converted_image = self.adjust_gamma(self.converted_image_base, gamma)
            converted_image_resized = adjusted_converted_image.resize((max_width // 2, max_height // 2), Image.LANCZOS)
            converted_photo = ImageTk.PhotoImage(converted_image_resized)
            self.converted_image_label.config(image=converted_photo)
            self.converted_image_label.image = converted_photo

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

    def handle_file_drop(self, event):
        """Handle file drop events and update the input and output path variables."""
        try:
            if not self.drop_target_registered:
                return  # Ignore drop if not registered
            file_path = event.data.strip('{}')
            if file_path:
                self.input_path_var.set(file_path)
                # Keep the same extension for output file
                base, ext = os.path.splitext(file_path)
                self.output_path_var.set(f"{base}_sdr{ext}")
                # Reset the cached images
                self.original_image = None
                self.converted_image_base = None
                self.button_frame.grid()
                self.image_frame.grid()
                self.action_frame.grid()
                self.update_frame_preview()
                self.highlight_frame_button(1)  # Highlight button 1 when image is loaded
        except Exception as e:
            logging.error(f"Error handling file drop: {e}")
            messagebox.showerror("Error", f"Error handling file drop: {e}")

    def convert_video(self):
        """Convert the video from HDR to SDR."""
        try:
            input_path = os.path.normpath(self.input_path_var.get())
            output_path = os.path.normpath(self.output_path_var.get())
            gamma = self.gamma_var.get()
            use_gpu = self.gpu_accel_var.get()  # Get GPU acceleration state
            selected_filter_index = self.filter_options.index(self.filter_var.get())
            tonemapper = self.tonemap_var.get().lower()  # Convert tonemapper to lowercase

            if not input_path or not output_path:
                messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
                return

            if not os.path.isfile(input_path):
                messagebox.showerror("Error", f"Input file not found: {input_path}")
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
            
            logging.info(f"Starting conversion - Input: {input_path}, Output: {output_path}, Gamma: {gamma}")
            
            # Start the conversion using the conversion_manager instance
            conversion_manager.start_conversion(
                input_path, output_path, gamma, use_gpu, selected_filter_index,
                self.progress_var, self.interactable_elements, self,
                self.open_after_conversion_var.get(), self.cancel_button,
                tonemapper=tonemapper  # Pass tonemapper to the conversion
            )
        except Exception as e:
            logging.error(f"Conversion error: {str(e)}", exc_info=True)
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

    def check_gpu_acceleration(self):
        """Check if GPU acceleration is available when the checkbox is toggled."""
        if self.gpu_accel_var.get():
            try:
                logging.debug("Checking GPU acceleration availability.")
                available = conversion_manager.is_gpu_available()
                logging.debug(f"GPU available: {available}")
                if not available:
                    self.gpu_accel_var.set(False)
                    messagebox.showwarning("GPU Acceleration",
                                           "GPU acceleration is not available on this system. GPU acceleration is only supported for NVIDIA gpu's with access to h264_nvenc. Switching to CPU mode.")
            except Exception as e:
                self.gpu_accel_var.set(False)
                logging.error(f"Error checking GPU acceleration: {e}")
                messagebox.showerror("Error", f"An error occurred while checking GPU acceleration:\n{e}")

    def show_tooltip(self, event, text):
        """Show tooltip window at mouse position"""
        x, y, _, _ = event.widget.bbox("insert")
        x += event.widget.winfo_rootx() + 25
        y += event.widget.winfo_rooty() + 20

        self.hide_tooltip()

        self.tooltip = tk.Toplevel(self.root)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        label = ttk.Label(self.tooltip, text=text, justify=tk.LEFT, relief=tk.SOLID, borderwidth=1, padding=(5, 5))
        label.pack()

    def hide_tooltip(self, event=None):
        """Hide and destroy tooltip window"""
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

    def on_frame_button_click(self, index):
        """Handle frame button clicks to update the displayed frames."""
        self.current_frame_index = index
        self.original_image = None  # Reset cached images
        self.converted_image_base = None
        self.highlight_frame_button(index)  # Update button highlight
        self.update_frame_preview()

    def highlight_frame_button(self, index):
        """Highlight the selected frame button and reset others."""
        for i, btn in enumerate(self.frame_buttons, start=1):
            if i == index:
                btn.configure(style='Selected.TButton')  # Apply selected style
            else:
                btn.configure(style='TButton')  # Reset to default style

    def display_frames(self, video_path):
        """Extract and display frames using the current frame index."""
        properties = get_video_properties(video_path)
        duration = properties['duration']
        time_position = (self.current_frame_index / (self.total_frames + 1)) * duration

        if self.original_image is None or self.last_time_position != time_position:
            # Extract original frame at specified time position
            self.original_image = extract_frame(video_path, time_position=time_position)
            self.last_time_position = time_position

        # Re-extract the converted SDR frame with the selected filter
        selected_filter_index = self.filter_options.index(self.filter_var.get())
        tonemapper = self.tonemap_var.get().lower()  # Convert tonemapper to lowercase
        self.converted_image_base = extract_frame_with_conversion(
            video_path, gamma=1.0, filter_index=selected_filter_index,
            tonemapper=tonemapper, time_position=time_position
        )

        # Resize and display original image
        original_image_resized = self.original_image.resize((960, 540), Image.LANCZOS)
        original_photo = ImageTk.PhotoImage(original_image_resized)
        self.original_image_label.config(image=original_photo)
        self.original_image_label.image = original_photo

        # Apply gamma adjustment to the cached converted SDR image
        gamma = self.gamma_var.get()
        adjusted_converted_image = self.adjust_gamma(self.converted_image_base, gamma)
        converted_image_resized = adjusted_converted_image.resize((960, 540), Image.LANCZOS)
        converted_photo = ImageTk.PhotoImage(converted_image_resized)
        self.converted_image_label.config(image=converted_photo)
        self.converted_image_label.image = converted_photo

        self.adjust_window_size()  # Ensure window size is adjusted after displaying images

    def update_frame_preview(self, event=None):
        """Update the frame preview without blocking the UI."""
        filter_value = self.filter_var.get()
        
        if self.display_image_var.get() and self.input_path_var.get():
            try:
                video_path = self.input_path_var.get()
                self.display_frames(video_path)
                self.error_label.config(text="")
                self.original_title_label.grid()
                self.converted_title_label.grid()
                self.button_container.grid()  # Show frame buttons
                self.adjust_window_size()
                self.arrange_widgets(image_frame=True)
            except Exception as e:
                self.handle_preview_error(e)
        else:
            self.clear_preview()
            self.original_title_label.grid_remove()
            self.converted_title_label.grid_remove()
            self.button_container.grid_remove()  # Hide frame buttons
            self.arrange_widgets(image_frame=False)
        self.filter_combobox.selection_clear()
        self.tonemap_combobox.selection_clear()