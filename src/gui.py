import os
import sys
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox
from tkinter import ttk
from dark_theme import apply_dark_theme
from conversion import conversion_manager
from utils import get_video_properties, get_maxcll, TONEMAP, clear_maxfall_cache
from settings import load_settings, save_settings
from licensing import InvalidKeyError, DeviceLimitError, NetworkError, LicenseError
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from dialogs import _LicenseDialog, _UpdateDialog, activate_license
from preview import (
    DEFAULT_MIN_SIZE, PREVIEW_SIZE, INITIAL_PANE_SIZE,
    _MIN_PANE_W, _RESIZE_DEBOUNCE_MS, _PREVIEW_WIDTH_RESERVE,
    _PREVIEW_HEIGHT_RESERVE, _MIN_SIZE_MARGIN, _PREVIEW_POOL_WORKERS,
    _PreviewFuture, _HDRPreviewMixin,
)
from batch import _BatchMixin

# Register the split-out modules under their src.* names so that
# patch('src.dialogs.X'), patch('src.preview.X'), patch('src.batch.X')
# target the same module objects that the code actually runs in.
# Without this, Python would load a second copy under each dotted name.
import sys as _sys
_sys.modules.update({
    'src.dialogs': _sys.modules['dialogs'],
    'src.preview': _sys.modules['preview'],
    'src.batch':   _sys.modules['batch'],
})
# mock.patch() looks up 'src.batch' via getattr(sys.modules['src'], 'batch')
# before falling back to sys.modules — and that fallback is a no-op when the
# key is already present, so it never links the attribute. Set it directly so
# patch() finds it on the first getattr, both when running under the 'src'
# package (tests) and when 'src' isn't imported at all (production, gui.py
# loaded as a bare top-level module).
if 'src' in _sys.modules:
    _src_pkg = _sys.modules['src']
    _src_pkg.dialogs = _sys.modules['dialogs']
    _src_pkg.preview = _sys.modules['preview']
    _src_pkg.batch = _sys.modules['batch']
    del _src_pkg
del _sys

# Re-export webbrowser so existing patches (patch('src.gui.webbrowser')) still resolve.
# (The name was importable from this module before the dialogs split.)
webbrowser = webbrowser  # noqa: F811


class HDRConverterGUI(_BatchMixin, _HDRPreviewMixin):
    """Main application window for the HDR to SDR Converter."""

    _licensed: bool = True  # class-level default for bare instances that bypass __init__

    # Output containers the user can pick.
    _OUTPUT_FORMATS = ['MP4', 'MKV', 'MOV']
    _INPUT_FORMAT_MAP = {'mp4': 'MP4', 'm4v': 'MP4', 'mov': 'MOV', 'mkv': 'MKV'}

    # Quality slider ranges as (worst, best): left end = smaller file, right = better.
    _CRF_RANGE = (28, 17)
    _CQ_RANGE = (30, 15)

    def __init__(self, root: "TkinterDnD.Tk", licensed: bool = False) -> None:
        """Initialize the GUI and set up all components."""
        self.root = root
        self._licensed = licensed
        self.root.title("HDR to SDR Converter")
        self._set_window_icon()
        self.root.after(0, self._set_window_icon)
        apply_dark_theme(self.root)
        self.root.minsize(*DEFAULT_MIN_SIZE)
        self.root.resizable(True, True)

        _s = load_settings()
        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.gamma_var = tk.DoubleVar(value=_s['gamma'])
        self.progress_var = tk.DoubleVar(value=0)
        self.open_after_conversion_var = tk.BooleanVar(value=_s['open_after_conversion'])
        self.display_image_var = tk.BooleanVar(value=_s['display_preview'])
        self.original_image = None
        self.converted_image_base = None
        self.gpu_accel_var = tk.BooleanVar(value=_s['gpu_accel'])
        self.tonemap_var = tk.StringVar(value=_s['tonemapper'])
        self.quality_var = tk.IntVar(value=_s['quality'])
        self.format_var = tk.StringVar(value=_s['filetype'])
        self.color_depth_var = tk.StringVar(value=_s['color_depth'])
        self.custom_time_var = tk.StringVar()
        self.custom_time_position: float | None = None
        self.batch_items: list[dict] = []  # type: ignore[type-arg]
        self._current_batch_item: dict | None = None  # type: ignore[type-arg]
        self.tooltip = None
        self.current_frame_index = 1
        self.total_frames = 5
        self.last_time_position: float | None = None
        self._preview_generation = 0
        self._preview_pool = ThreadPoolExecutor(
            max_workers=_PREVIEW_POOL_WORKERS, thread_name_prefix='frame-fetch')
        self._preview_thread: _PreviewFuture | None = None
        self._converted_preview_base: Image.Image | None = None
        self._duration_path: str | None = None
        self._duration_value: float | None = None
        self._preview_cache_original: dict = {}
        self._preview_cache_converted: dict = {}
        self._cache_lock = threading.Lock()

        self.create_widgets()
        self.configure_grid()
        self._apply_license_state(licensed)

        self._min_window_size = self._compute_min_window_size()
        self._apply_min_window_size()

        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
        self.drop_target_registered = True

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._resize_job: str | None = None
        self._window_auto_fitted = False
        self.root.bind('<Configure>', self._on_window_configure)

        self.cancelled = False

        self.check_ffmpeg_available()

        self.root.after(3000, self._start_update_check)

    # ── Window icon ────────────────────────────────────────────────────────────

    def _set_window_icon(self) -> None:
        exe_name = os.path.basename(sys.executable).lower()
        is_compiled = getattr(sys, 'frozen', False) or not exe_name.startswith('python')
        if is_compiled:
            meipass: str | None = getattr(sys, '_MEIPASS', None)
            base_dir = meipass if meipass is not None else os.path.dirname(
                os.path.abspath(sys.executable))
            icon_path = os.path.join(base_dir, 'icon.ico')
        else:
            src_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(src_dir, '..', 'logo', 'icon.ico')
        if not os.path.exists(icon_path):
            return
        try:
            self.root.iconbitmap(icon_path)
        except Exception:
            pass

    def check_ffmpeg_available(self) -> bool:
        """Warn the user if ffmpeg/ffprobe could not be located on startup."""
        from utils import FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE
        if not FFMPEG_EXECUTABLE or not FFPROBE_EXECUTABLE:
            messagebox.showerror(
                "FFmpeg Not Found",
                "ffmpeg/ffprobe could not be located. The converter cannot run "
                "without them. Please reinstall the application or install ffmpeg.")
            return False
        return True

    # ── Auto-update ────────────────────────────────────────────────────────────

    def _start_update_check(self) -> None:
        def _worker() -> None:
            from updater import check_for_update, APP_VERSION
            result = check_for_update()
            if result:
                new_ver, url = result
                self.root.after(0, lambda: self._show_update_dialog(APP_VERSION, new_ver, url))
        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_dialog(self, current_ver: str, new_ver: str, url: str) -> None:
        _UpdateDialog(self.root, current_ver, new_ver, url)

    # ── Licensing ──────────────────────────────────────────────────────────────

    def _open_license_dialog(self) -> None:
        dlg = _LicenseDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.activated:
            self._apply_license_state(True)

    def _apply_license_state(self, licensed: bool) -> None:
        """Enable or disable Pro-only widgets and rebuild interactable_elements."""
        self._licensed = licensed
        pro = 'normal' if licensed else 'disabled'

        self.quality_slider.config(state=pro)
        self.custom_time_entry.config(state=pro)
        self.custom_seek_button.config(state=pro)

        if licensed:
            self.format_combobox.config(values=self._OUTPUT_FORMATS)
        else:
            self.format_var.set('MP4')
            self.format_combobox.config(values=['MP4'])
            current = self.output_path_var.get()
            if current:
                self.output_path_var.set(self._output_path_with_format(current, 'MP4'))

        self.add_files_button.config(state=pro)
        self.remove_batch_button.config(state=pro)
        self.clear_batch_button.config(state=pro)

        self._rebuild_interactable_elements()

        if licensed:
            self._pro_banner.grid_remove()
        else:
            self._pro_banner.grid()

    def _rebuild_interactable_elements(self) -> None:
        """Keep interactable_elements in sync with license state."""
        free = [
            self.browse_button, self.convert_button, self.gamma_slider,
            self.open_after_conversion_checkbutton, self.display_image_checkbutton,
            self.input_entry, self.output_entry, self.gamma_entry,
            self.gpu_accel_checkbutton, self.color_depth_combobox,
        ]
        premium = [
            self.quality_slider, self.format_combobox,
            self.custom_time_entry, self.custom_seek_button,
            self.add_files_button, self.clear_batch_button, self.remove_batch_button,
        ]
        self.interactable_elements = free + premium if self._licensed else free

    # ── Window / session ────────────────────────────────────────────────────────

    def on_close(self) -> None:
        """Handle the window close event."""
        if conversion_manager.process and conversion_manager.process.poll() is None:
            if messagebox.askokcancel(
                    "Quit", "A conversion is in progress. Do you want to cancel and exit?"):
                conversion_manager.cancel_conversion(
                    self, self.interactable_elements, self.cancel_button)
                self._save_current_settings()
                if hasattr(self, '_preview_pool'):
                    self._preview_pool.shutdown(wait=False, cancel_futures=True)
                self.root.destroy()
        else:
            self._save_current_settings()
            if hasattr(self, '_preview_pool'):
                self._preview_pool.shutdown(wait=False, cancel_futures=True)
            self.root.destroy()

    def _save_current_settings(self) -> None:
        """Persist current UI settings to disk."""
        try:
            save_settings({
                'gamma': self.gamma_var.get(),
                'tonemapper': self.tonemap_var.get(),
                'gpu_accel': self.gpu_accel_var.get(),
                'open_after_conversion': self.open_after_conversion_var.get(),
                'display_preview': self.display_image_var.get(),
                'quality': self.quality_var.get(),
                'filetype': self.format_var.get(),
                'color_depth': self.color_depth_var.get(),
            })
        except AttributeError:
            pass  # bare/partially-initialized instance (test contexts only)

    # ── Widget construction ────────────────────────────────────────────────────

    def create_widgets(self) -> None:
        """Create and arrange the widgets in the main window."""
        self.control_frame = ttk.Frame(self.root, padding="10")
        self.control_frame.grid(row=0, column=0, sticky=tk.W + tk.E + tk.N)

        ttk.Label(self.control_frame, text="Input File:").grid(row=0, column=0, sticky=tk.W)
        self.input_entry = ttk.Entry(self.control_frame, textvariable=self.input_path_var, width=40)
        self.input_entry.grid(row=0, column=1, sticky=tk.W + tk.E, padx=(10, 10))
        self.browse_button = ttk.Button(
            self.control_frame, text="Browse", command=self.select_file)
        self.browse_button.grid(row=0, column=2, sticky=tk.W + tk.E, padx=(5, 0))

        ttk.Label(self.control_frame, text="Output File:").grid(row=1, column=0, sticky=tk.W)
        self.output_entry = ttk.Entry(
            self.control_frame, textvariable=self.output_path_var, width=40)
        self.output_entry.grid(row=1, column=1, sticky=tk.W + tk.E, padx=(10, 10))
        self.format_combobox = ttk.Combobox(
            self.control_frame, textvariable=self.format_var,
            values=self._OUTPUT_FORMATS, state='readonly', width=6)
        self.format_combobox.grid(row=1, column=2, sticky=tk.W + tk.E, padx=(5, 0))
        self.format_combobox.bind('<<ComboboxSelected>>', self._on_format_change)

        ttk.Label(self.control_frame, text="Gamma:").grid(row=2, column=0, sticky=tk.W)
        self.gamma_slider = ttk.Scale(
            self.control_frame, variable=self.gamma_var, from_=0.1, to=3.0,
            orient=tk.HORIZONTAL, length=200, command=self.on_gamma_change)
        self.gamma_slider.grid(row=2, column=1, sticky=tk.W + tk.E, padx=(10, 10))
        self.gamma_slider.bind('<Button-1>', self._gamma_slider_jump)
        self.gamma_entry = ttk.Entry(self.control_frame, textvariable=self.gamma_var, width=5)
        self.gamma_entry.grid(row=2, column=2, sticky=tk.W + tk.E, padx=(5, 0))
        self.gamma_entry.bind('<Return>', self.on_gamma_change)

        self.gpu_accel_checkbutton = ttk.Checkbutton(
            self.control_frame, text="Enable GPU Acceleration",
            variable=self.gpu_accel_var, command=self.check_gpu_acceleration)
        self.gpu_accel_checkbutton.grid(row=3, column=0, sticky=tk.W, pady=(5, 0))

        self.display_image_checkbutton = ttk.Checkbutton(
            self.control_frame, text="Display Frame Preview",
            variable=self.display_image_var, command=self.update_frame_preview)
        self.display_image_checkbutton.grid(row=4, column=0, sticky=tk.W, pady=(5, 0))

        tonemap_frame = ttk.Frame(self.control_frame)
        tonemap_frame.grid(row=3, column=1, sticky=tk.W, padx=(10, 10), pady=(5, 0))
        self.tonemap_combobox = ttk.Combobox(
            tonemap_frame, textvariable=self.tonemap_var,
            values=TONEMAP, state='readonly', width=15)
        self.tonemap_combobox.grid(row=0, column=0, padx=(0, 5))
        self.tonemap_combobox.bind('<<ComboboxSelected>>', self.update_frame_preview)
        info_button_tonemap = ttk.Label(tonemap_frame, text="ⓘ", cursor="hand2")
        info_button_tonemap.grid(row=0, column=1)
        tooltip_text_tonemap = (
            "Reinhard: Basic HDR to SDR conversion\n"
            "Mobius: Natural-looking conversion\n"
            "Hable: Game-like conversion (Cyberpunk 2077)"
        )
        info_button_tonemap.bind('<Enter>',
                                  lambda e: self.show_tooltip(e, tooltip_text_tonemap))
        info_button_tonemap.bind('<Leave>', self.hide_tooltip)

        quality_frame = ttk.Frame(self.control_frame)
        quality_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W + tk.E, pady=(5, 0))
        ttk.Label(quality_frame, text="Quality:").grid(row=0, column=0, sticky=tk.W)
        self.quality_slider = ttk.Scale(
            quality_frame, from_=self._CRF_RANGE[0], to=self._CRF_RANGE[1],
            orient=tk.HORIZONTAL, length=200, command=self._on_quality_change)
        self.quality_slider.grid(row=0, column=1, sticky=tk.W + tk.E, padx=(10, 8))
        self.quality_slider.set(self.quality_var.get())
        self.quality_slider.bind('<Button-1>', self._quality_slider_jump)
        self.quality_value_label = ttk.Label(quality_frame, textvariable=self.quality_var, width=3)
        self.quality_value_label.grid(row=0, column=2, sticky=tk.W)
        ttk.Label(quality_frame, text="Smaller File  ◀──▶  Better Quality",
                  foreground='gray').grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=(10, 0))
        quality_frame.columnconfigure(1, weight=1)

        color_depth_frame = ttk.Frame(self.control_frame)
        color_depth_frame.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))
        ttk.Label(color_depth_frame, text="Output Color Depth:").grid(
            row=0, column=0, sticky=tk.W)
        self.color_depth_combobox = ttk.Combobox(
            color_depth_frame, textvariable=self.color_depth_var,
            values=['8-bit', '10-bit'], state='readonly', width=8)
        self.color_depth_combobox.grid(row=0, column=1, padx=(10, 0))
        self.color_depth_combobox.bind('<<ComboboxSelected>>', self._on_color_depth_change)

        self.image_frame = ttk.Frame(self.root, padding="10")
        self.image_frame.grid(row=1, column=0, sticky=tk.W + tk.E + tk.N + tk.S)
        self.image_frame.grid_remove()

        self.original_title_label = ttk.Label(self.image_frame, text="Original (HDR):")
        self.converted_title_label = ttk.Label(self.image_frame, text="Converted (SDR):")
        if not self.display_image_var.get():
            self.original_title_label.grid_remove()
            self.converted_title_label.grid_remove()
        else:
            self.original_title_label.grid(row=0, column=0, sticky=tk.W, padx=(10, 10))
            self.converted_title_label.grid(
                row=0, column=1, columnspan=2, sticky=tk.W, padx=(10, 10))

        self.original_image_label = ttk.Label(self.image_frame)
        self.original_image_label.grid(
            row=1, column=0, columnspan=1,
            sticky=tk.W + tk.E + tk.N + tk.S, padx=(10, 10))
        self.converted_image_label = ttk.Label(self.image_frame)
        self.converted_image_label.grid(
            row=1, column=1, sticky=tk.W + tk.E + tk.N + tk.S, padx=(10, 0))

        self.button_container = ttk.Frame(self.image_frame)
        self.button_container.grid(row=1, column=2, sticky=tk.N, padx=(5, 10))
        self.button_container.grid_remove()

        self.frame_buttons: list[ttk.Button] = []
        style = ttk.Style()
        style.configure('Selected.TButton', relief='sunken')
        for i in range(1, 6):
            btn = ttk.Button(self.button_container, text=str(i),
                             command=lambda idx=i: self.on_frame_button_click(idx))
            btn.grid(row=i-1, column=0, pady=5)
            self.frame_buttons.append(btn)

        self.custom_seek_label = ttk.Label(
            self.button_container, text="Jump to time\n(HH:MM:SS)",
            foreground='gray', justify=tk.CENTER)
        self.custom_seek_label.grid(row=self.total_frames, column=0, pady=(10, 0))
        self.custom_time_entry = ttk.Entry(
            self.button_container, textvariable=self.custom_time_var, width=8)
        self.custom_time_entry.grid(row=self.total_frames + 1, column=0, pady=(2, 2))
        self.custom_time_entry.bind('<Return>', self.on_custom_seek)
        self.custom_seek_button = ttk.Button(
            self.button_container, text="Go", width=4, command=self.on_custom_seek)
        self.custom_seek_button.grid(row=self.total_frames + 2, column=0, pady=(0, 5))

        self.loading_frame = ttk.Frame(self.image_frame)
        self.loading_label = ttk.Label(self.loading_frame, text="Rendering preview...")
        self.loading_label.grid(row=0, column=0, pady=(40, 8))
        self.loading_bar = ttk.Progressbar(self.loading_frame, mode='indeterminate', length=240)
        self.loading_bar.grid(row=1, column=0, pady=(0, 40))
        self.loading_frame.grid(row=1, column=0, columnspan=3)
        self.loading_frame.grid_remove()

        self.info_label = ttk.Label(self.control_frame, text='', foreground='gray')
        self.info_label.grid(row=7, column=0, columnspan=3, sticky=tk.W, padx=(0, 10))
        self.info_label.grid_remove()

        self.error_label = ttk.Label(self.control_frame, text='', foreground='red')
        self.error_label.grid(row=8, column=0, columnspan=3, sticky=tk.W)

        self._pro_banner = ttk.Frame(self.control_frame)
        self._pro_banner.grid(row=9, column=0, columnspan=3,
                               sticky=tk.W + tk.E, pady=(6, 2))
        self._pro_banner.grid_remove()
        ttk.Label(
            self._pro_banner,
            text='🔒 Quality, batch & container require Pro.',
            foreground='gray',
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Button(
            self._pro_banner, text='Activate License',
            command=self._open_license_dialog,
        ).grid(row=0, column=1, padx=(12, 0))

        self.button_frame = ttk.Frame(self.image_frame)
        self.button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
        self.button_frame.grid_remove()

        self.action_frame = ttk.Frame(self.root)
        self.action_frame.grid(row=3, column=0, pady=(0, 10), sticky=tk.N)

        self.open_after_conversion_checkbutton = ttk.Checkbutton(
            self.action_frame, text="Open output file after conversion",
            variable=self.open_after_conversion_var)
        self.open_after_conversion_checkbutton.grid(
            row=1, column=0, padx=(5, 5), sticky=tk.N)

        self.convert_button = ttk.Button(
            self.action_frame, text="Convert", command=self.convert_video)
        self.convert_button.grid(row=1, column=1, padx=(5, 5), pady=(0, 10), sticky=tk.N)

        self.cancel_button = ttk.Button(
            self.action_frame, text="Cancel", command=self.cancel_conversion)
        self.cancel_button.grid(row=1, column=2, padx=(5, 5), pady=(0, 10), sticky=tk.N)
        self.cancel_button.grid_remove()

        self.progress_bar = ttk.Progressbar(
            self.image_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=3, sticky=tk.W + tk.E)

        self.batch_frame = ttk.LabelFrame(self.root, text="Batch Queue", padding="10")
        self.batch_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky=tk.W + tk.E)
        self.batch_frame.columnconfigure(0, weight=1)

        batch_buttons = ttk.Frame(self.batch_frame)
        batch_buttons.grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self.add_files_button = ttk.Button(
            batch_buttons, text="Add Files", command=self.browse_batch_files)
        self.add_files_button.grid(row=0, column=0, padx=(0, 5))
        self.remove_batch_button = ttk.Button(
            batch_buttons, text="Remove", command=self.remove_selected_batch_item)
        self.remove_batch_button.grid(row=0, column=1, padx=(0, 5))
        self.clear_batch_button = ttk.Button(
            batch_buttons, text="Clear", command=self.clear_batch_queue)
        self.clear_batch_button.grid(row=0, column=2, padx=(0, 5))

        ttk.Label(self.batch_frame, foreground='gray',
                  text="Add or drop multiple files to convert them in sequence.").grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(4, 4))

        self.batch_listbox = tk.Listbox(self.batch_frame, height=8, activestyle='none')
        self.batch_listbox.grid(row=2, column=0, sticky=tk.W + tk.E + tk.N + tk.S)
        batch_scroll = ttk.Scrollbar(
            self.batch_frame, orient=tk.VERTICAL, command=self.batch_listbox.yview)
        batch_scroll.grid(row=2, column=1, sticky=tk.N + tk.S)
        self.batch_frame.rowconfigure(2, weight=1)
        self.batch_listbox.config(yscrollcommand=batch_scroll.set)
        self.batch_listbox.bind('<<ListboxSelect>>', self.on_batch_item_select)

        self.interactable_elements = [
            self.browse_button, self.convert_button, self.gamma_slider,
            self.open_after_conversion_checkbutton, self.display_image_checkbutton,
            self.input_entry, self.output_entry, self.gamma_entry,
            self.gpu_accel_checkbutton, self.quality_slider, self.format_combobox,
            self.custom_time_entry, self.custom_seek_button,
            self.add_files_button, self.clear_batch_button, self.remove_batch_button,
            self.color_depth_combobox,
        ]

        self._apply_quality_range()

    def configure_grid(self) -> None:
        """Configure the grid layout for the main window and frames."""
        self.control_frame.columnconfigure(0, weight=0)
        self.control_frame.columnconfigure(1, weight=1)
        self.control_frame.columnconfigure(2, weight=0)
        for i in range(10):
            self.control_frame.rowconfigure(i, weight=0)

        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.columnconfigure(1, weight=1)
        self.image_frame.columnconfigure(2, weight=0)
        self.image_frame.rowconfigure(0, weight=0)
        self.image_frame.rowconfigure(1, weight=1)
        self.image_frame.rowconfigure(2, weight=0)
        self.image_frame.rowconfigure(3, weight=0)

        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    # ── File loading ───────────────────────────────────────────────────────────

    def select_file(self) -> None:
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
                ("All files", "*.*"),
            ]
        )
        if file_path:
            self._load_input_file(file_path)

    def _load_input_file(self, file_path: str) -> None:
        """Load a file into the input/output boxes and refresh the preview."""
        self.input_path_var.set(file_path)
        fmt = self._format_for_input(file_path) if self._licensed else 'MP4'
        self.format_var.set(fmt)
        base = os.path.splitext(file_path)[0]
        self.output_path_var.set(self._output_path_with_format(f"{base}_sdr", fmt))
        self.original_image = None
        self.converted_image_base = None
        self._reset_custom_seek()
        self._reset_preview_cache()
        self._update_info_label(file_path)
        self.button_frame.grid()
        self.image_frame.grid()
        self.action_frame.grid()
        self.update_frame_preview()
        self.highlight_frame_button(1)

    def _unload_input_file(self) -> None:
        """Clear the loaded input file and hide its preview area."""
        self.input_path_var.set('')
        self.output_path_var.set('')
        self.original_image = None
        self.converted_image_base = None
        self._converted_preview_base = None
        self._reset_custom_seek()
        self._reset_preview_cache()
        if hasattr(self, 'info_label'):
            self.info_label.config(text='')
            self.info_label.grid_remove()
        self.update_frame_preview()
        if hasattr(self, 'image_frame'):
            self.image_frame.grid_remove()

    # ── Path/format helpers ────────────────────────────────────────────────────

    @staticmethod
    def _output_path_with_format(path: str, fmt: str) -> str:
        """Return ``path`` with its extension replaced by the chosen container."""
        base = os.path.splitext(path)[0]
        return f"{base}.{fmt.lower()}"

    @classmethod
    def _format_for_input(cls, input_path: str) -> str:
        """Pick a sensible default output container from the input's extension."""
        ext = os.path.splitext(input_path)[1].lower().lstrip('.')
        return cls._INPUT_FORMAT_MAP.get(ext, 'MKV')

    def _on_format_change(self, event: object = None) -> None:
        """Rewrite the output path's extension when the container dropdown changes."""
        current = self.output_path_var.get()
        if current:
            self.output_path_var.set(
                self._output_path_with_format(current, self.format_var.get()))
        if hasattr(self, 'format_combobox'):
            self.format_combobox.selection_clear()

    # ── Output Color Depth ──────────────────────────────────────────────────────

    def _on_color_depth_change(self, event: object = None) -> None:
        """10-bit output is a Pro feature; unlicensed selections revert to 8-bit
        and surface the upsell instead of leaving the widget inertly disabled,
        so an unlicensed user can discover the feature by trying it."""
        if self.color_depth_var.get() == '10-bit' and not self._licensed:
            self.color_depth_var.set('8-bit')
            self._open_license_dialog()

    # ── Quality slider ─────────────────────────────────────────────────────────

    def _apply_quality_range(self) -> None:
        """Set the Quality slider's range for the current CPU/GPU mode."""
        worst, best = self._CQ_RANGE if self.gpu_accel_var.get() else self._CRF_RANGE
        old_from = float(self.quality_slider.cget('from'))
        old_to = float(self.quality_slider.cget('to'))
        current = float(self.quality_slider.get())
        if old_to != old_from:
            fraction = min(max((current - old_from) / (old_to - old_from), 0.0), 1.0)
        else:
            fraction = 0.0
        new_value = worst + fraction * (best - worst)
        self.quality_slider.configure(from_=worst, to=best)
        self.quality_slider.set(new_value)
        self.quality_var.set(int(round(new_value)))

    def _on_quality_change(self, value: str) -> None:
        """Snap the Quality slider to whole CRF/CQ steps (the scale emits floats)."""
        self.quality_var.set(int(float(value)))

    # ── Info strip ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_info_text(properties: dict, maxcll: float | None = None) -> str:  # type: ignore[type-arg]
        """Format key video metadata as a compact one-line string."""
        w = properties.get('width', '?')
        h = properties.get('height', '?')
        fps = properties.get('frame_rate', 0)
        codec = (properties.get('codec_name') or '?').upper()
        audio = (properties.get('audio_codec') or 'none').upper()
        primaries = properties.get('color_primaries', '')
        transfer = properties.get('color_transfer', '')
        is_hdr = primaries == 'bt2020' or transfer in ('smpte2084', 'arib-std-b67')
        hdr_tag = 'HDR' if is_hdr else 'SDR'
        fps_str = f"{fps:.3f} fps" if fps else "? fps"
        if is_hdr:
            maxcll_str = f"  Max Nits: {int(maxcll)}" if maxcll is not None else "  Max Nits: N/A"
        else:
            maxcll_str = ""
        # Output is always capped to 8-bit (free) / 10-bit (Pro); call out sources
        # above that so higher-bit-depth masters aren't silently downsampled unnoticed.
        bit_depth = properties.get('bit_depth', 0)
        bit_depth_str = f"  {bit_depth}-bit source" if bit_depth > 10 else ""
        return f"{w}×{h}  {fps_str}  {codec}  {hdr_tag}{maxcll_str}{bit_depth_str}  Audio: {audio}"

    def _update_info_label(self, file_path: str) -> None:
        """Probe file metadata and update the info strip below the output path."""
        if not hasattr(self, 'info_label'):
            return
        props = get_video_properties(file_path)
        if props:
            maxcll = get_maxcll(file_path)
            self.info_label.config(text=self._build_info_text(props, maxcll=maxcll))
            self.info_label.grid()
        else:
            self.info_label.config(text='')
            self.info_label.grid_remove()

    # ── Conversion ─────────────────────────────────────────────────────────────

    def handle_file_drop(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle file drop events."""
        try:
            if not self.drop_target_registered:
                return
            paths = self._parse_drop_paths(getattr(event, 'data', ''))
            if not paths:
                return
            if len(paths) > 1:
                if not self._licensed:
                    messagebox.showinfo(
                        'Pro Feature',
                        'Batch processing requires a Pro license.\n\n'
                        'Click "Activate License" to unlock.')
                    return
                self.add_batch_files(paths)
                return
            file_path = paths[0]
            if file_path:
                self._load_input_file(file_path)
        except Exception as e:
            logging.error(f"Error handling file drop: {e}")
            messagebox.showerror("Error", f"Error handling file drop: {e}")

    def convert_video(self) -> None:
        """Convert the video from HDR to SDR."""
        if getattr(self, 'batch_items', None):
            self.start_batch()
            return
        try:
            if not self.input_path_var.get() or not self.output_path_var.get():
                messagebox.showwarning(
                    "Warning",
                    "Please select both an input file and specify an output file.")
                return

            input_path = os.path.normpath(self.input_path_var.get())
            output_path = os.path.normpath(self.output_path_var.get())
            gamma = self.gamma_var.get()
            use_gpu = self.gpu_accel_var.get()
            tonemapper = self.tonemap_var.get().lower()
            quality = int(self.quality_var.get())
            ten_bit = self._licensed and self.color_depth_var.get() == '10-bit'

            output_path = self._output_path_with_format(output_path, self.format_var.get())
            self.output_path_var.set(output_path)

            if not os.path.isfile(input_path):
                messagebox.showerror("Error", f"Input file not found: {input_path}")
                return

            if os.path.exists(output_path):
                answer = messagebox.askyesno(
                    "File Exists",
                    f"The file '{output_path}' already exists. Do you want to overwrite it?")
                if not answer:
                    return

            if self.drop_target_registered:
                self.unregister_drop_target()

            self.cancel_button.grid()

            logging.info(
                f"Starting conversion - Input: {input_path}, "
                f"Output: {output_path}, Gamma: {gamma}")

            conversion_manager.start_conversion(
                input_path, output_path, gamma, use_gpu,
                self.progress_var, self.interactable_elements, self,
                self.open_after_conversion_var.get(), self.cancel_button,
                tonemapper=tonemapper, quality=quality, ten_bit=ten_bit,
            )
        except Exception as e:
            logging.error(f"Conversion error: {str(e)}", exc_info=True)
            messagebox.showerror("Conversion Error",
                                 f"An error occurred during conversion: {e}")

    def cancel_conversion(self) -> None:
        """Cancel the ongoing video conversion process."""
        conversion_manager.cancel_conversion(
            self, self.interactable_elements, self.cancel_button)

    # ── Drop-target ────────────────────────────────────────────────────────────

    def register_drop_target(self) -> None:
        """Register the drag and drop target."""
        if not self.drop_target_registered:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
            self.drop_target_registered = True

    def unregister_drop_target(self) -> None:
        """Unregister the drag and drop target."""
        if self.drop_target_registered:
            self.root.drop_target_unregister()
            self.drop_target_registered = False

    def disable_ui(self, elements: list) -> None:  # type: ignore[type-arg]
        """Disable the specified UI elements."""
        for element in elements:
            element.config(state='disabled')

    # ── GPU acceleration ───────────────────────────────────────────────────────

    def check_gpu_acceleration(self) -> None:
        """Check if GPU acceleration is available when the checkbox is toggled."""
        if self.gpu_accel_var.get():
            try:
                available = conversion_manager.is_gpu_acceleration_available()
                if not available:
                    self.gpu_accel_var.set(False)
                    messagebox.showwarning(
                        "GPU Acceleration",
                        "GPU acceleration is not available on this system. "
                        "It needs either a supported hardware encoder "
                        "(NVIDIA h264_nvenc, AMD h264_amf, Intel h264_qsv) or "
                        "GPU tonemapping (libplacebo/Vulkan). Switching to CPU mode.")
            except Exception as e:
                self.gpu_accel_var.set(False)
                logging.error(f"Error checking GPU acceleration: {e}")
                messagebox.showerror(
                    "Error",
                    f"An error occurred while checking GPU acceleration:\n{e}")
        if hasattr(self, 'quality_slider'):
            self._apply_quality_range()

    # ── Tooltips ───────────────────────────────────────────────────────────────

    def show_tooltip(self, event: tk.Event, text: str) -> None:  # type: ignore[type-arg]
        """Show tooltip window at mouse position."""
        x = event.widget.winfo_rootx() + 25
        y = event.widget.winfo_rooty() + 20
        self.hide_tooltip()
        self.tooltip = tk.Toplevel(self.root)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(self.tooltip, text=text, justify=tk.LEFT,
                          relief=tk.SOLID, borderwidth=1, padding=(5, 5))
        label.pack()

    def hide_tooltip(self, event: object = None) -> None:
        """Hide and destroy tooltip window."""
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None
