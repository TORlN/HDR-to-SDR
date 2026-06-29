import os
import sys
import tempfile
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox
from tkinter import ttk
from dark_theme import apply_dark_theme
from conversion import conversion_manager  # Import the conversion_manager instance
from utils import (extract_frame_with_conversion, extract_frame, TONEMAP,
                   get_video_properties, get_maxcll, clear_maxfall_cache,
                   extract_frames_batch, extract_frames_with_conversion_batch)
from settings import load_settings, save_settings
from licensing import activate_license, InvalidKeyError, DeviceLimitError, NetworkError, LicenseError
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES
import logging
import threading
import re

# ── License dialog ─────────────────────────────────────────────────────────────
_BG       = '#1e1e1e'
_FG       = '#ffffff'
_ENTRY_BG = '#2d2d2d'
_ACCENT   = '#0078d4'
_ERROR_FG = '#ff6b6b'
_FONT      = ('Segoe UI', 10)
_FONT_BOLD = ('Segoe UI', 13, 'bold')
_FONT_SM   = ('Segoe UI', 9)


class _LicenseDialog(tk.Toplevel):
    """Dark-themed modal for entering a Pro license key."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.configure(bg=_BG)
        self.title('Activate HDR to SDR Converter')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._activated = False
        self._build_ui()
        self.update_idletasks()
        w = max(self.winfo_reqwidth() + 40, 460)
        h = max(self.winfo_reqheight() + 20, 220)
        px = master.winfo_rootx() + (master.winfo_width() - w) // 2
        py = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f'{w}x{h}+{px}+{py}')
        self.grab_set()
        self.focus_set()

    def _build_ui(self) -> None:
        tk.Label(self, text='Activate HDR to SDR Converter',
                 bg=_BG, fg=_FG, font=_FONT_BOLD).pack(pady=(28, 4))
        tk.Label(self, text='Enter your license key to unlock Pro features.',
                 bg=_BG, fg='#aaaaaa', font=_FONT_SM).pack(pady=(0, 10))
        self._key_var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self._key_var, width=44,
                         bg=_ENTRY_BG, fg=_FG, insertbackground=_FG,
                         relief='flat', font=_FONT)
        entry.pack(padx=32, ipady=7)
        entry.focus_set()
        entry.bind('<Return>', lambda _: self._submit())
        self._status_var = tk.StringVar()
        tk.Label(self, textvariable=self._status_var,
                 bg=_BG, fg=_ERROR_FG, font=_FONT_SM).pack(pady=(6, 0))
        tk.Button(self, text='Activate', command=self._submit,
                  bg=_ACCENT, fg=_FG,
                  activebackground='#005fa3', activeforeground=_FG,
                  relief='flat', padx=20, pady=7,
                  font=_FONT, cursor='hand2').pack(pady=14)
        link = tk.Label(self, text='Need to free up a machine slot? Manage activations →',
                        bg=_BG, fg='#888888', font=_FONT_SM, cursor='hand2')
        link.pack()
        link.bind('<Button-1>', lambda _: self._open_manage_url())
        link.bind('<Enter>', lambda _: link.config(fg='#aaaaaa'))
        link.bind('<Leave>', lambda _: link.config(fg='#888888'))

    def _open_manage_url(self) -> None:
        webbrowser.open('https://app.lemonsqueezy.com/my-orders')

    def _submit(self) -> None:
        key = self._key_var.get().strip()
        if not key:
            self._status_var.set('Please enter your license key.')
            return
        self._status_var.set('Validating…')
        self.update()
        try:
            activate_license(key)
            self._activated = True
            self.destroy()
        except InvalidKeyError:
            self._status_var.set('Invalid license key — please check and try again.')
        except DeviceLimitError:
            self._status_var.set('Device limit reached. Deactivate another machine first.')
        except NetworkError:
            self._status_var.set('Cannot reach license server. Check your connection.')
        except LicenseError as exc:
            self._status_var.set(str(exc))

    def _on_close(self) -> None:
        self._activated = False
        self.destroy()

    @property
    def activated(self) -> bool:
        return self._activated

# ── Update dialog ──────────────────────────────────────────────────────────────

class _UpdateDialog(tk.Toplevel):
    """Dark-themed modal that prompts the user to install an available update."""

    def __init__(self, master: tk.Misc, current_ver: str, new_ver: str, download_url: str) -> None:
        super().__init__(master)
        self.configure(bg=_BG)
        self.title('Update Available')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self._current_ver = current_ver
        self._new_ver = new_ver
        self._url = download_url
        self._build_ui()
        self.update_idletasks()
        w = max(self.winfo_reqwidth() + 40, 430)
        h = max(self.winfo_reqheight() + 20, 200)
        px = master.winfo_rootx() + (master.winfo_width() - w) // 2
        py = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f'{w}x{h}+{px}+{py}')
        self.grab_set()
        self.focus_set()

    def _build_ui(self) -> None:
        tk.Label(self, text='Update Available',
                 bg=_BG, fg=_FG, font=_FONT_BOLD).pack(pady=(24, 4))
        tk.Label(self,
                 text=f'Version {self._new_ver} is available  (you have {self._current_ver})',
                 bg=_BG, fg='#aaaaaa', font=_FONT_SM).pack(pady=(0, 4))
        tk.Label(self,
                 text='The app will close and the installer will open automatically.',
                 bg=_BG, fg='#666666', font=_FONT_SM).pack(pady=(0, 12))

        self._status_var = tk.StringVar()
        tk.Label(self, textvariable=self._status_var,
                 bg=_BG, fg='#aaaaaa', font=_FONT_SM).pack()

        self._progress_var = tk.DoubleVar(value=0)
        self._progress = ttk.Progressbar(self, variable=self._progress_var,
                                          maximum=100, length=360)

        self._btn_frame = tk.Frame(self, bg=_BG)
        self._btn_frame.pack(pady=(10, 0))

        self._update_btn = tk.Button(
            self._btn_frame, text='Update Now', command=self._start_download,
            bg=_ACCENT, fg=_FG, activebackground='#005fa3', activeforeground=_FG,
            relief='flat', padx=18, pady=7, font=_FONT, cursor='hand2',
        )
        self._update_btn.grid(row=0, column=0, padx=8)

        self._later_btn = tk.Button(
            self._btn_frame, text='Later', command=self.destroy,
            bg='#3a3a3a', fg=_FG, activebackground='#4a4a4a', activeforeground=_FG,
            relief='flat', padx=18, pady=7, font=_FONT, cursor='hand2',
        )
        self._later_btn.grid(row=0, column=1, padx=8)

    def _start_download(self) -> None:
        from updater import download_installer
        self._update_btn.config(state='disabled', text='Downloading…')
        self._later_btn.config(state='disabled')
        self._status_var.set('Starting download…')
        self._progress.pack(pady=(6, 0))
        self.protocol('WM_DELETE_WINDOW', lambda: None)

        tmp_dir = tempfile.mkdtemp(prefix='hdr_to_sdr_update_')
        dest = os.path.join(tmp_dir, 'HDR_to_SDR_Setup.exe')

        def _on_progress(downloaded: int, total: int) -> None:
            if total > 0:
                pct = downloaded / total * 100
                mb_done = downloaded / 1_048_576
                mb_total = total / 1_048_576
                self.after(0, lambda p=pct, d=mb_done, t=mb_total:
                           self._update_progress(p, d, t))

        def _worker() -> None:
            try:
                download_installer(self._url, dest, _on_progress)
                self.after(0, lambda: self._on_download_complete(dest))
            except Exception as exc:
                self.after(0, lambda e=str(exc): self._on_download_error(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _update_progress(self, pct: float, mb_done: float, mb_total: float) -> None:
        self._progress_var.set(pct)
        self._status_var.set(f'Downloading… {mb_done:.1f} / {mb_total:.1f} MB')

    def _on_download_complete(self, path: str) -> None:
        self._progress_var.set(100)
        self._status_var.set('Download complete — launching installer…')
        self.after(900, lambda: self._launch_and_close(path))

    def _launch_and_close(self, path: str) -> None:
        from updater import launch_installer
        launch_installer(path)
        self.master.destroy()

    def _on_download_error(self, msg: str) -> None:
        self._progress.pack_forget()
        self._status_var.set('Download failed — please try again later.')
        self._update_btn.config(state='normal', text='Retry')
        self._later_btn.config(state='normal')
        self.protocol('WM_DELETE_WINDOW', self.destroy)


DEFAULT_MIN_SIZE = (550, 150)
PREVIEW_SIZE = (3840, 2160)     # ffmpeg render cap; never upscales past source resolution
INITIAL_PANE_SIZE = (640, 360)  # comfortable per-pane size on the first preview reveal
_MIN_PANE_W = 240               # don't shrink a preview pane narrower than this
_RESIZE_DEBOUNCE_MS = 60        # coalesce a burst of resize events into one rescale
_PREVIEW_WIDTH_RESERVE = 160    # frame-button column + inter-pane padding (per row)
_PREVIEW_HEIGHT_RESERVE = 130   # titles + frame-button row + progress bar + padding
_MIN_SIZE_MARGIN = (16, 16)     # buffer added to the computed min size so nothing clips

class HDRConverterGUI:
    """
    A class encapsulating the GUI components and functionality for the HDR to SDR Converter application.
    """

    _licensed: bool = True  # class-level default for bare instances that bypass __init__

    def __init__(self, root, licensed: bool = False):
        """Initialize the GUI and set up all components."""
        self.root = root
        self._licensed = licensed
        self.root.title("HDR to SDR Converter")
        self._set_window_icon()
        self.root.after(0, self._set_window_icon)
        # Color-based dark theme (clam). Applied before create_widgets so the
        # classic Listbox inherits the dark colors via the option database. Not
        # image-based (unlike sv_ttk), so the widget tree doesn't re-render from
        # PNG assets on every resize tick -- keeps window resizing smooth.
        apply_dark_theme(self.root)
        self.root.minsize(*DEFAULT_MIN_SIZE)
        self.root.resizable(True, True)  # let the user size the window now that it holds more

        # Variables
        _s = load_settings()
        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.gamma_var = tk.DoubleVar(value=_s['gamma'])
        self.progress_var = tk.DoubleVar(value=0)
        self.open_after_conversion_var = tk.BooleanVar(value=_s['open_after_conversion'])
        self.display_image_var = tk.BooleanVar(value=_s['display_preview'])
        self.original_image = None  # Cache for the original frame
        self.converted_image_base = None  # Cache for the converted SDR frame
        self.gpu_accel_var = tk.BooleanVar(value=_s['gpu_accel'])
        self.tonemap_var = tk.StringVar(value=_s['tonemapper'])
        self.quality_var = tk.IntVar(value=_s['quality'])
        self.format_var = tk.StringVar(value=_s['filetype'])  # output container; set from input on load
        self.custom_time_var = tk.StringVar()  # HH:MM:SS for the custom-seek entry
        self.custom_time_position = None  # absolute seconds when a custom seek is active
        self.batch_items = []  # queued files: {input, output, format, status}
        self._current_batch_item = None  # the item currently converting
        self.tooltip = None  # Add this line for tooltip tracking
        self.current_frame_index = 1  # Default to 1 (1/6 of the video)
        self.total_frames = 5
        self.last_time_position = None
        self._preview_generation = 0  # Debounce token for preview worker threads
        self._preview_thread = None
        self._converted_preview_base = None  # display-sized SDR frame; gamma applied on top
        self._duration_path = None           # input path the cached duration belongs to
        self._duration_value = None          # cached video duration (avoids repeat ffprobe)
        self._preview_cache_original = {}    # (path, time) -> extracted HDR frame
        self._preview_cache_converted = {}   # (path, time, filter, tonemapper) -> SDR frame
        self._cache_lock = threading.Lock()  # display + pre-warm workers share the caches

        # Create widgets and configure layout
        self.create_widgets()
        self.configure_grid()
        self._apply_license_state(licensed)

        # Derive the minimum window size from the controls so the user can never
        # drag the window small enough to clip them (the preview pane shrinks to
        # take up the slack -- see _on_window_configure).
        self._min_window_size = self._compute_min_window_size()
        self._apply_min_window_size()

        # Bind events
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
        self.drop_target_registered = True  # Set to True after registering

        # Bind the window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Rescale the previews to follow the window (debounced -- see handler).
        self._resize_job = None
        self._window_auto_fitted = False
        self.root.bind('<Configure>', self._on_window_configure)

        self.cancelled = False  # Flag to track cancellation

        # ffmpeg/ffprobe are resolved lazily at import; if that failed (binaries
        # missing) surface it here rather than letting later actions fail cryptically.
        self.check_ffmpeg_available()

        # Check for updates 3 s after startup so it never delays the UI appearing.
        self.root.after(3000, self._start_update_check)

    def _set_window_icon(self) -> None:
        # PyInstaller sets sys.frozen; Nuitka does not — detect Nuitka by exe name
        exe_name = os.path.basename(sys.executable).lower()
        is_compiled = getattr(sys, 'frozen', False) or not exe_name.startswith('python')
        if is_compiled:
            # PyInstaller 6.x onedir stores data files under sys._MEIPASS (_internal/).
            # Nuitka standalone stores them alongside the exe; sys._MEIPASS is not set.
            meipass: str | None = getattr(sys, '_MEIPASS', None)
            base_dir = meipass if meipass is not None else os.path.dirname(os.path.abspath(sys.executable))
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

    def check_ffmpeg_available(self):
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

        self.gpu_accel_checkbutton.config(state=pro)
        if not licensed:
            self.gpu_accel_var.set(False)
            if hasattr(self, 'quality_slider'):
                self._apply_quality_range()

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
        """Keep interactable_elements in sync with license state.

        The conversion manager disables/re-enables this list during a job.
        Premium widgets are excluded when unlicensed so they are never
        accidentally re-enabled after a conversion finishes.
        """
        free = [
            self.browse_button, self.convert_button, self.gamma_slider,
            self.open_after_conversion_checkbutton, self.display_image_checkbutton,
            self.input_entry, self.output_entry, self.gamma_entry,
        ]
        premium = [
            self.gpu_accel_checkbutton, self.quality_slider, self.format_combobox,
            self.custom_time_entry, self.custom_seek_button,
            self.add_files_button, self.clear_batch_button, self.remove_batch_button,
        ]
        self.interactable_elements = free + premium if self._licensed else free

    # ── Window / session ────────────────────────────────────────────────────────

    def on_close(self):
        """Handle the window close event by cancelling ongoing conversions and cleaning up."""
        if conversion_manager.process and conversion_manager.process.poll() is None:
            if messagebox.askokcancel("Quit", "A conversion is in progress. Do you want to cancel and exit?"):
                conversion_manager.cancel_conversion(
                    self, self.interactable_elements, self.cancel_button
                )
                self._save_current_settings()
                self.root.destroy()
        else:
            self._save_current_settings()
            self.root.destroy()

    def _save_current_settings(self):
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
            })
        except AttributeError:
            pass  # bare/partially-initialized instance (test contexts only)

    def create_widgets(self):
        """Create and arrange the widgets in the main window."""
        # Control Frame
        self.control_frame = ttk.Frame(self.root, padding="10")
        self.control_frame.grid(row=0, column=0, sticky=tk.W + tk.E + tk.N)

        # Input File Widgets
        ttk.Label(self.control_frame, text="Input File:").grid(row=0, column=0, sticky=tk.W)
        self.input_entry = ttk.Entry(self.control_frame, textvariable=self.input_path_var, width=40)
        self.input_entry.grid(row=0, column=1, sticky=tk.W + tk.E, padx=(10, 10))
        self.browse_button = ttk.Button(
            self.control_frame,
            text="Browse",
            command=self.select_file
        )
        self.browse_button.grid(row=0, column=2, sticky=tk.W + tk.E, padx=(5, 0))

        # Output File Widgets
        ttk.Label(self.control_frame, text="Output File:").grid(row=1, column=0, sticky=tk.W)
        self.output_entry = ttk.Entry(self.control_frame, textvariable=self.output_path_var, width=40)
        self.output_entry.grid(row=1, column=1, sticky=tk.W + tk.E, padx=(10, 10))
        # Output container dropdown: an explicit MP4/MKV/MOV choice (col 2).
        self.format_combobox = ttk.Combobox(
            self.control_frame, textvariable=self.format_var,
            values=self._OUTPUT_FORMATS, state='readonly', width=6
        )
        self.format_combobox.grid(row=1, column=2, sticky=tk.W + tk.E, padx=(5, 0))
        self.format_combobox.bind('<<ComboboxSelected>>', self._on_format_change)

        # Gamma Adjustment Widgets
        ttk.Label(self.control_frame, text="Gamma:").grid(row=2, column=0, sticky=tk.W)
        self.gamma_slider = ttk.Scale(
            self.control_frame,
            variable=self.gamma_var,
            from_=0.1,
            to=3.0,
            orient=tk.HORIZONTAL,
            length=200,
            command=self.on_gamma_change
        )
        self.gamma_slider.grid(row=2, column=1, sticky=tk.W + tk.E, padx=(10, 10))
        # Make a click on the trough jump the knob to the click (see _gamma_slider_jump).
        self.gamma_slider.bind('<Button-1>', self._gamma_slider_jump)
        self.gamma_entry = ttk.Entry(self.control_frame, textvariable=self.gamma_var, width=5)
        self.gamma_entry.grid(row=2, column=2, sticky=tk.W + tk.E, padx=(5, 0))
        self.gamma_entry.bind('<Return>', self.on_gamma_change)

        # GPU Acceleration Checkbox
        self.gpu_accel_checkbutton = ttk.Checkbutton(
            self.control_frame,
            text="Enable GPU Acceleration",
            variable=self.gpu_accel_var,
            command=self.check_gpu_acceleration
        )
        self.gpu_accel_checkbutton.grid(row=3, column=0, sticky=tk.W, pady=(5, 0))

        # Display Image Checkbox - col 0
        self.display_image_checkbutton = ttk.Checkbutton(
            self.control_frame,
            text="Display Frame Preview",
            variable=self.display_image_var,
            command=self.update_frame_preview
        )
        self.display_image_checkbutton.grid(row=4, column=0, sticky=tk.W, pady=(5, 0))

        # Tonemapper combobox + ⓘ button in a small frame at col 1 (mirrors filter_frame)
        tonemap_frame = ttk.Frame(self.control_frame)
        tonemap_frame.grid(row=3, column=1, sticky=tk.W, padx=(10, 10), pady=(5, 0))

        self.tonemap_combobox = ttk.Combobox(
            tonemap_frame,
            textvariable=self.tonemap_var,
            values=TONEMAP,
            state='readonly',
            width=15
        )
        self.tonemap_combobox.grid(row=0, column=0, padx=(0, 5))
        self.tonemap_combobox.bind('<<ComboboxSelected>>', self.update_frame_preview)

        info_button_tonemap = ttk.Label(tonemap_frame, text="ⓘ", cursor="hand2")
        info_button_tonemap.grid(row=0, column=1)

        tooltip_text_tonemap = (
            "Reinhard: Basic HDR to SDR conversion\n"
            "Mobius: Natural-looking conversion\n"
            "Hable: Game-like conversion (Cyberpunk 2077)"
        )
        info_button_tonemap.bind('<Enter>', lambda e: self.show_tooltip(e, tooltip_text_tonemap))
        info_button_tonemap.bind('<Leave>', self.hide_tooltip)

        # Quality slider: one control whose range depends on CPU (CRF 17-28) vs
        # GPU (CQ 15-30) mode. Driven by a command that snaps to whole steps (the
        # scale emits floats); the value label and range are kept in sync.
        quality_frame = ttk.Frame(self.control_frame)
        quality_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W + tk.E, pady=(5, 0))
        ttk.Label(quality_frame, text="Quality:").grid(row=0, column=0, sticky=tk.W)
        self.quality_slider = ttk.Scale(
            quality_frame, from_=self._CRF_RANGE[0], to=self._CRF_RANGE[1],
            orient=tk.HORIZONTAL, length=200, command=self._on_quality_change
        )
        self.quality_slider.grid(row=0, column=1, sticky=tk.W + tk.E, padx=(10, 8))
        self.quality_slider.set(self.quality_var.get())
        # Snap the knob to a trough click (see _jump_slider_to_click), matching
        # the gamma slider instead of ttk's slide-to-end page-jump.
        self.quality_slider.bind('<Button-1>', self._quality_slider_jump)
        self.quality_value_label = ttk.Label(quality_frame, textvariable=self.quality_var, width=3)
        self.quality_value_label.grid(row=0, column=2, sticky=tk.W)
        ttk.Label(quality_frame, text="Smaller File  ◀──▶  Better Quality",
                  foreground='gray').grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=(10, 0))
        quality_frame.columnconfigure(1, weight=1)

        # Image Frame for Displaying Images
        self.image_frame = ttk.Frame(self.root, padding="10")
        self.image_frame.grid(row=1, column=0, sticky=tk.W + tk.E + tk.N + tk.S)
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
        self.original_image_label.grid(row=1, column=0, columnspan=1, sticky=tk.W + tk.E + tk.N + tk.S, padx=(10, 10))
        self.converted_image_label = ttk.Label(self.image_frame)
        self.converted_image_label.grid(row=1, column=1, sticky=tk.W + tk.E + tk.N + tk.S, padx=(10, 0))

        self.button_container = ttk.Frame(self.image_frame)  # New container frame
        self.button_container.grid(row=1, column=2, sticky=tk.N, padx=(5, 10))
        self.button_container.grid_remove()  # Initially hide the button container
        
        self.frame_buttons = []  # List to store frame buttons
        style = ttk.Style()
        style.configure('Selected.TButton', relief='sunken')  # Style for selected button

        for i in range(1, 6):
            btn = ttk.Button(self.button_container, text=str(i),
                           command=lambda idx=i: self.on_frame_button_click(idx))
            btn.grid(row=i-1, column=0, pady=5)
            self.frame_buttons.append(btn)

        # Custom seek: type an exact HH:MM:SS (or MM:SS / SS) below the numbered
        # buttons to preview any moment. Lives in button_container so it hides and
        # reveals together with the frame buttons during loading. A small caption
        # above the entry explains what the bare "Go" button does and the format.
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

        # Loading indicator shown over the image area while a preview frame is
        # being extracted (the titles, frame buttons and images stay hidden until
        # the frames are actually ready -- see _show_preview_loading/_reveal_preview).
        self.loading_frame = ttk.Frame(self.image_frame)
        self.loading_label = ttk.Label(self.loading_frame, text="Rendering preview...")
        self.loading_label.grid(row=0, column=0, pady=(40, 8))
        self.loading_bar = ttk.Progressbar(self.loading_frame, mode='indeterminate', length=240)
        self.loading_bar.grid(row=1, column=0, pady=(0, 40))
        self.loading_frame.grid(row=1, column=0, columnspan=3)
        self.loading_frame.grid_remove()  # hidden until a preview is loading

        # Info strip: shows video metadata (resolution, fps, codec, HDR/SDR) after a file is loaded
        self.info_label = ttk.Label(self.control_frame, text='', foreground='gray')
        self.info_label.grid(row=6, column=0, columnspan=3, sticky=tk.W, padx=(0, 10))
        self.info_label.grid_remove()  # hidden until a file is loaded

        # Error Label (row 7 so it doesn't overlap the quality/info rows)
        self.error_label = ttk.Label(self.control_frame, text='', foreground='red')
        self.error_label.grid(row=7, column=0, columnspan=3, sticky=tk.W)

        # Pro upgrade banner (row 8) — shown when unlicensed
        self._pro_banner = ttk.Frame(self.control_frame)
        self._pro_banner.grid(row=8, column=0, columnspan=3, sticky=tk.W + tk.E, pady=(6, 2))
        self._pro_banner.grid_remove()
        ttk.Label(
            self._pro_banner,
            text='🔒 GPU, quality, batch & container require Pro.',
            foreground='gray',
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Button(
            self._pro_banner, text='Activate License',
            command=self._open_license_dialog,
        ).grid(row=0, column=1, padx=(12, 0))

        # Button Frame
        self.button_frame = ttk.Frame(self.image_frame)
        self.button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
        self.button_frame.grid_remove()

        # Action Frame: Convert/Cancel live here and stay visible at the bottom, so
        # the queue can be converted even when no single preview file is loaded.
        self.action_frame = ttk.Frame(self.root)
        self.action_frame.grid(row=3, column=0, pady=(0, 10), sticky=tk.N)

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
        self.progress_bar.grid(row=3, column=0, columnspan=3, sticky=tk.W + tk.E)

        # Batch Queue panel: add several files and convert them one after another.
        # Always visible so files can be queued before selecting a single preview.
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

        # A taller list that fills the panel vertically, so a queue of several
        # files is visible at a glance and the scrollbar spans the whole list.
        self.batch_listbox = tk.Listbox(self.batch_frame, height=8, activestyle='none')
        self.batch_listbox.grid(row=2, column=0, sticky=tk.W + tk.E + tk.N + tk.S)
        batch_scroll = ttk.Scrollbar(
            self.batch_frame, orient=tk.VERTICAL, command=self.batch_listbox.yview)
        batch_scroll.grid(row=2, column=1, sticky=tk.N + tk.S)
        self.batch_frame.rowconfigure(2, weight=1)
        self.batch_listbox.config(yscrollcommand=batch_scroll.set)
        self.batch_listbox.bind('<<ListboxSelect>>', self.on_batch_item_select)

        # List of interactable elements
        self.interactable_elements = [
            self.browse_button, self.convert_button, self.gamma_slider,
            self.open_after_conversion_checkbutton, self.display_image_checkbutton,
            self.input_entry, self.output_entry, self.gamma_entry, self.gpu_accel_checkbutton,
            self.quality_slider, self.format_combobox,
            self.custom_time_entry, self.custom_seek_button,
            self.add_files_button, self.clear_batch_button, self.remove_batch_button
        ]

        # Set the Quality slider's range for the loaded CPU/GPU mode.
        self._apply_quality_range()

    def configure_grid(self):
        """Configure the grid layout for the main window and frames."""
        # Control Frame Grid Configuration
        self.control_frame.columnconfigure(0, weight=0)
        self.control_frame.columnconfigure(1, weight=1)
        self.control_frame.columnconfigure(2, weight=0)
        for i in range(9):
            self.control_frame.rowconfigure(i, weight=0)

        # Image Frame Grid Configuration
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.columnconfigure(1, weight=1)
        # The frame-button column keeps its natural width (weight 0) so it hugs
        # the converted preview instead of floating in an empty third of the
        # window when maximized; the two image columns share the extra width.
        self.image_frame.columnconfigure(2, weight=0)
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
            self._load_input_file(file_path)

    def _load_input_file(self, file_path):
        """Load a file into the input/output boxes and refresh the preview.

        Shared by Browse, single-file drop, and the batch queue (which loads the
        top of the queue here so its first frames show as if it were selected).
        """
        self.input_path_var.set(file_path)
        # Default the output container to the input's (Pro only; free users always get MP4).
        fmt = self._format_for_input(file_path) if self._licensed else 'MP4'
        self.format_var.set(fmt)
        base = os.path.splitext(file_path)[0]
        self.output_path_var.set(self._output_path_with_format(f"{base}_sdr", fmt))
        # Reset the cached images
        self.original_image = None
        self.converted_image_base = None
        self._reset_custom_seek()  # a new file starts on the numbered frames
        self._reset_preview_cache()
        self._update_info_label(file_path)
        self.button_frame.grid()
        self.image_frame.grid()
        self.action_frame.grid()
        self.update_frame_preview()
        self.highlight_frame_button(1)  # Highlight button 1 when image is loaded

    def _unload_input_file(self):
        """Clear the loaded input file and hide its preview area.

        The inverse of :meth:`_load_input_file`: used when the previewed file is
        removed from (or the whole) batch queue, leaving nothing to show.
        """
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
        # With an empty input path this clears the images and hides the
        # titles/frame buttons; then drop the whole image frame too.
        self.update_frame_preview()
        if hasattr(self, 'image_frame'):
            self.image_frame.grid_remove()

    # Output containers the user can pick. The converter always encodes H.264, so
    # all three can hold the video; subtitle/audio handling per container lives in
    # ConversionManager._container_stream_args.
    _OUTPUT_FORMATS = ['MP4', 'MKV', 'MOV']
    _INPUT_FORMAT_MAP = {'mp4': 'MP4', 'm4v': 'MP4', 'mov': 'MOV', 'mkv': 'MKV'}

    @staticmethod
    def _output_path_with_format(path, fmt):
        """Return ``path`` with its extension replaced by the chosen container."""
        base = os.path.splitext(path)[0]
        return f"{base}.{fmt.lower()}"

    @classmethod
    def _format_for_input(cls, input_path):
        """Pick a sensible default output container from the input's extension.

        WebM/AVI (and anything unrecognized) can't map 1:1 to MP4/MKV/MOV, so they
        default to MKV, which holds essentially any stream.
        """
        ext = os.path.splitext(input_path)[1].lower().lstrip('.')
        return cls._INPUT_FORMAT_MAP.get(ext, 'MKV')

    # Quality slider ranges as (worst, best): left end = smaller file (higher
    # CRF/CQ), right end = better quality (lower). CPU uses CRF, GPU uses CQ.
    _CRF_RANGE = (28, 17)
    _CQ_RANGE = (30, 15)

    def _apply_quality_range(self):
        """Set the Quality slider's range for the current CPU/GPU mode.

        CPU uses CRF (28..17) and GPU uses CQ (30..15) -- different numeric
        scales, so the *same* value lands at a different spot on the track. To
        avoid the knob visibly sliding when GPU is toggled, preserve its
        fractional position and remap the underlying CRF/CQ number to the new
        range (the displayed value barely moves, the knob doesn't).
        """
        worst, best = self._CQ_RANGE if self.gpu_accel_var.get() else self._CRF_RANGE
        old_from = float(self.quality_slider.cget('from'))
        old_to = float(self.quality_slider.cget('to'))
        # The slider's float value is the true knob position (quality_var is the
        # rounded display copy); use it so repeated toggles don't drift.
        current = float(self.quality_slider.get())
        if old_to != old_from:
            fraction = min(max((current - old_from) / (old_to - old_from), 0.0), 1.0)
        else:
            fraction = 0.0
        new_value = worst + fraction * (best - worst)
        self.quality_slider.configure(from_=worst, to=best)
        self.quality_slider.set(new_value)          # exact position -> no jump
        self.quality_var.set(int(round(new_value)))  # rounded CRF/CQ for display

    def _on_quality_change(self, value):
        """Snap the Quality slider to whole CRF/CQ steps (the scale emits floats)."""
        self.quality_var.set(int(float(value)))

    @staticmethod
    def _parse_timestamp(text):
        """Parse 'HH:MM:SS', 'MM:SS', or 'SS' (fractions allowed) into seconds.

        Raises ValueError on empty input, non-numeric parts, a negative value, or
        more than three colon-separated fields.
        """
        text = text.strip()
        if not text:
            raise ValueError("empty timestamp")
        parts = text.split(':')
        if len(parts) > 3:
            raise ValueError("too many ':' separators")
        seconds = 0.0
        for part in parts:
            value = float(part)  # raises ValueError on non-numeric
            if value < 0:
                raise ValueError("negative time component")
            seconds = seconds * 60 + value
        return seconds

    def _preview_time_position(self, duration):
        """Return the preview's seek position, honoring an active custom seek.

        Defaults to the current frame button's evenly-spaced slot; a custom seek
        (set via the HH:MM:SS entry) overrides it, clamped to the video duration.
        """
        custom = getattr(self, 'custom_time_position', None)
        if custom is not None:
            return max(0.0, min(custom, duration))
        return (self.current_frame_index / (self.total_frames + 1)) * duration

    def on_custom_seek(self, event=None):
        """Preview the timestamp typed in the custom-seek entry."""
        try:
            seconds = self._parse_timestamp(self.custom_time_var.get())
        except ValueError:
            self.error_label.config(text="Invalid time. Use HH:MM:SS, MM:SS, or seconds.")
            return
        self.error_label.config(text="")
        self.custom_time_position = seconds
        self.original_image = None       # invalidate the cached visible frame
        self.converted_image_base = None
        self.highlight_frame_button(0)   # no numbered button corresponds to a custom seek
        self.update_frame_preview()

    def _on_format_change(self, event=None):
        """Rewrite the output path's extension when the container dropdown changes."""
        current = self.output_path_var.get()
        if current:
            self.output_path_var.set(
                self._output_path_with_format(current, self.format_var.get()))
        if hasattr(self, 'format_combobox'):
            self.format_combobox.selection_clear()

    @staticmethod
    def _build_info_text(properties, maxcll=None):
        """Format key video metadata as a compact one-line string for the info strip."""
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
        return f"{w}×{h}  {fps_str}  {codec}  {hdr_tag}{maxcll_str}  Audio: {audio}"

    def _update_info_label(self, file_path):
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

    def adjust_gamma(self, image, gamma):
        """Adjust gamma of a PIL.Image."""
        # gamma == 1.0 is the identity transform; skip the per-pixel LUT pass that
        # runs on every gamma-slider tick.
        if abs(gamma - 1.0) < 1e-6:
            return image
        inv_gamma = 1.0 / gamma
        lut = [pow(i / 255.0, inv_gamma) * 255 for i in range(256)]
        # Extend LUT for all channels
        lut = lut * len(image.getbands())
        lut = [int(round(v)) for v in lut]  # Ensure values are integers
        return image.point(lut)

    def clear_preview(self):
        """Clear the frame preview images and reset cached images."""
        self.original_image_label.config(image='')
        self.converted_image_label.config(image='')
        self.original_image = None
        self.converted_image_base = None
        self._converted_preview_base = None
        self._apply_min_window_size()

    def _compute_min_window_size(self):
        """Smallest window that keeps every control visible (the preview may shrink).

        Derived from the chrome that must never be clipped -- the controls, the
        batch queue and the action buttons -- but deliberately *excludes* the
        preview pane, which is free to rescale down to nothing as the window
        shrinks (see :meth:`_on_window_configure`). Width is the widest chrome
        frame; height is the three stacked frames. Falls back to
        ``DEFAULT_MIN_SIZE`` on bare/mocked instances where widget geometry isn't
        real, and never returns less than that floor.
        """
        try:
            self.root.update_idletasks()
            frames = (self.control_frame, self.batch_frame, self.action_frame)
            widths = [f.winfo_reqwidth() for f in frames]
            heights = [f.winfo_reqheight() for f in frames]
        except Exception:
            return DEFAULT_MIN_SIZE
        if not all(isinstance(v, int) for v in widths + heights):
            return DEFAULT_MIN_SIZE  # mocked geometry -> use the floor
        margin_w, margin_h = _MIN_SIZE_MARGIN
        min_w = max(widths) + margin_w
        min_h = sum(heights) + margin_h
        return (max(min_w, DEFAULT_MIN_SIZE[0]), max(min_h, DEFAULT_MIN_SIZE[1]))

    def _apply_min_window_size(self):
        """Apply the computed minimum window size (``DEFAULT_MIN_SIZE`` pre-layout)."""
        self.root.minsize(*getattr(self, '_min_window_size', DEFAULT_MIN_SIZE))

    def adjust_window_size(self):
        """Fit the window to the previews on first reveal; keep minsize small.

        The minimum size stays at ``DEFAULT_MIN_SIZE`` so the user can always
        drag the window smaller than the previews -- the panes rescale to follow
        (see :meth:`_on_window_configure`). We only shrink-wrap the geometry
        once, on the first preview, so re-rendering a later frame never yanks a
        window the user has since resized.
        """
        self._apply_min_window_size()
        if getattr(self, '_window_auto_fitted', False):
            return

        # If the window is already maximized the user has explicitly sized it —
        # calling geometry("") would un-maximize it and collapse the preview to
        # INITIAL_PANE_SIZE.  Instead, mark the fit as done and immediately
        # rescale to the live (fullscreen) frame geometry.
        if self.root.wm_state() == 'zoomed':
            self._window_auto_fitted = True
            self.root.update_idletasks()
            self._rescale_preview_to_window()
            return

        self.root.geometry("")  # shrink-wrap to the first previews
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        if self.root.winfo_width() > screen_width or self.root.winfo_height() > screen_height:
            # First previews already overflow the screen -> shrink them to fit.
            self.resize_images(screen_width - 100, screen_height - 100)
            self.root.geometry("")
            self.root.update_idletasks()
        self._window_auto_fitted = True

    @staticmethod
    def _fit_preview_pane(available_width, available_height):
        """Size one 16:9 preview pane to fit a box, never upscaling past source.

        Scales to the available width (clamped to ``_MIN_PANE_W`` so a pane never
        collapses), then trims to the available height when the height-limited
        box is the tighter constraint. Returns a (w, h) of at least 1px.
        """
        src_w, src_h = PREVIEW_SIZE
        w = min(src_w, max(_MIN_PANE_W, int(available_width)))
        h = round(w * src_h / src_w)
        if available_height and h > available_height:
            h = int(available_height)
            w = round(h * src_w / src_h)
        return (max(1, int(w)), max(1, int(h)))

    def _preview_target_size(self):
        """Per-pane preview size derived from the live image-frame geometry."""
        if not hasattr(self, 'image_frame'):
            return PREVIEW_SIZE
        frame_w = self.image_frame.winfo_width()
        frame_h = self.image_frame.winfo_height()
        if not isinstance(frame_w, int) or frame_w <= 1:
            return PREVIEW_SIZE  # not laid out yet (or mocked) -> native size
        avail_w = (frame_w - _PREVIEW_WIDTH_RESERVE) / 2  # two panes share the width
        avail_h = max(0, frame_h - _PREVIEW_HEIGHT_RESERVE) if frame_h > 1 else 0
        return self._fit_preview_pane(avail_w, avail_h)

    def _initial_preview_size(self):
        """Per-pane size for the very first preview, before the window auto-fits.

        On first load the image frame is still only as wide as the controls
        above it, so deriving the pane size from its live geometry would render a
        cramped thumbnail and then shrink-wrap the window down to match. Use a
        comfortable default instead, capped so two panes plus the chrome still
        fit on screen. After the first auto-fit, resizing uses the live geometry
        (:meth:`_preview_target_size`) so the user stays in control.
        """
        pane_w, pane_h = INITIAL_PANE_SIZE
        try:
            screen_w = self.root.winfo_screenwidth()
        except Exception:
            return (pane_w, pane_h)
        if not isinstance(screen_w, int):
            return (pane_w, pane_h)  # mocked geometry -> use the default
        max_pane_w = (screen_w - 100 - _PREVIEW_WIDTH_RESERVE) // 2
        if 0 < max_pane_w < pane_w:
            return self._fit_preview_pane(max_pane_w, 0)  # cap to fit the screen
        return (pane_w, pane_h)

    @staticmethod
    def _keep_image_ref(label, photo):
        """Pin a PhotoImage to its label so Tk doesn't garbage-collect it.

        Tkinter keeps only a weak reference to a displayed image, so the photo
        must be held somewhere or it vanishes. Stashing it on the widget via
        setattr keeps the standard idiom (``label.image = photo``) without
        tripping the type checker, which doesn't know widgets accept arbitrary
        attributes.
        """
        setattr(label, 'image', photo)

    def _render_preview_at_size(self, size):
        """(Re)render both panes at ``size``; SDR pane keeps the live gamma.

        Resizes from the cached PREVIEW_SIZE bases (``original_image`` and
        ``converted_image_base``) -- a couple of PIL resizes, no ffmpeg.
        """
        original = getattr(self, 'original_image', None)
        if original is None:
            return  # nothing extracted yet
        self._preview_render_size = size
        original_resized = original.resize(size, Image.Resampling.LANCZOS)
        original_photo = ImageTk.PhotoImage(original_resized)
        self.original_image_label.config(image=original_photo)
        self._keep_image_ref(self.original_image_label, original_photo)
        if self.converted_image_base is not None:
            self._converted_preview_base = self.converted_image_base.resize(size, Image.Resampling.LANCZOS)
            self._apply_gamma_to_preview()

    def _on_window_configure(self, event=None):
        """Coalesce live resize events into a single debounced preview rescale.

        Dragging the window edge fires ``<Configure>`` rapidly; rescaling two
        images on every event is what makes resizing feel laggy. Cancel any
        pending rescale and schedule one shortly after the last event, so the
        previews snap to the new size once the drag settles instead of thrashing
        PIL on every pixel. Ignores Configure events from child widgets and does
        nothing when there is no preview on screen to rescale.
        """
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, 'original_image', None) is None:
            return
        if getattr(self, '_resize_job', None) is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(_RESIZE_DEBOUNCE_MS, self._rescale_preview_to_window)

    def _rescale_preview_to_window(self):
        """Re-render the previews at the size that fits the settled window."""
        self._resize_job = None
        if getattr(self, 'original_image', None) is None:
            return
        self._render_preview_at_size(self._preview_target_size())

    def resize_images(self, max_width, max_height):
        """Resize images to fit within the specified maximum width and height."""
        if self.original_image:
            original_image_resized = self.original_image.resize((max_width // 2, max_height // 2), Image.Resampling.LANCZOS)
            original_photo = ImageTk.PhotoImage(original_image_resized)
            self.original_image_label.config(image=original_photo)
            self._keep_image_ref(self.original_image_label, original_photo)

        if self.converted_image_base:
            gamma = self.gamma_var.get()
            adjusted_converted_image = self.adjust_gamma(self.converted_image_base, gamma)
            converted_image_resized = adjusted_converted_image.resize((max_width // 2, max_height // 2), Image.Resampling.LANCZOS)
            converted_photo = ImageTk.PhotoImage(converted_image_resized)
            self.converted_image_label.config(image=converted_photo)
            self._keep_image_ref(self.converted_image_label, converted_photo)

    def arrange_widgets(self, image_frame):
        """Arrange the widgets in the appropriate frames."""
        if image_frame:
            self.button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
            self.progress_bar.grid(row=3, column=0, columnspan=3, sticky=tk.W + tk.E)
        else:
            self.button_frame.grid(row=5, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
            self.progress_bar.grid(row=6, column=0, columnspan=3, sticky=tk.W + tk.E)
        self.open_after_conversion_checkbutton.grid(row=1, column=0, padx=(5, 5), sticky=tk.N)
        self.convert_button.grid(row=1, column=1, padx=(5, 5), pady=(0, 10), sticky=tk.N)
        self.cancel_button.grid_remove()  # Ensure cancel button is hidden

    def handle_preview_error(self, error):
        """Handle errors that occur during frame preview update."""
        self.error_label.config(text=f"Error displaying image: {error}")
        self._hide_preview_loading()
        self.clear_preview()
        self.original_title_label.grid_remove()
        self.converted_title_label.grid_remove()
        self.button_container.grid_remove()

    def handle_file_drop(self, event):
        """Handle file drop events and update the input and output path variables."""
        try:
            if not self.drop_target_registered:
                return  # Ignore drop if not registered
            paths = self._parse_drop_paths(event.data)
            if not paths:
                return
            if len(paths) > 1:
                if not self._licensed:
                    messagebox.showinfo(
                        'Pro Feature',
                        'Batch processing requires a Pro license.\n\nClick "Activate License" to unlock.')
                    return
                self.add_batch_files(paths)
                return
            file_path = paths[0]
            if file_path:
                self._load_input_file(file_path)
        except Exception as e:
            logging.error(f"Error handling file drop: {e}")
            messagebox.showerror("Error", f"Error handling file drop: {e}")

    def convert_video(self):
        """Convert the video from HDR to SDR.

        When the batch queue holds files, Convert runs the queue sequentially;
        otherwise it converts the single input/output selected above.
        """
        if getattr(self, 'batch_items', None):
            self.start_batch()
            return
        try:
            # Validate the raw values before normpath -- os.path.normpath('') is
            # '.', which would otherwise mask an empty field as a bogus path.
            if not self.input_path_var.get() or not self.output_path_var.get():
                messagebox.showwarning("Warning", "Please select both an input file and specify an output file.")
                return

            input_path = os.path.normpath(self.input_path_var.get())
            output_path = os.path.normpath(self.output_path_var.get())
            gamma = self.gamma_var.get()
            use_gpu = self.gpu_accel_var.get()
            tonemapper = self.tonemap_var.get().lower()
            quality = int(self.quality_var.get())

            # The chosen container (format dropdown) governs the output extension,
            # regardless of the input format; keep the displayed path in sync.
            output_path = self._output_path_with_format(output_path, self.format_var.get())
            self.output_path_var.set(output_path)

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

            conversion_manager.start_conversion(
                input_path, output_path, gamma, use_gpu,
                self.progress_var, self.interactable_elements, self,
                self.open_after_conversion_var.get(), self.cancel_button,
                tonemapper=tonemapper,
                quality=quality
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

    # --- Batch queue (sequential multi-file conversion) ---

    _STATUS_ICONS = {'Pending': '•', 'Converting': '▶', 'Done': '✓', 'Failed': '✗'}

    @staticmethod
    def _parse_drop_paths(data):
        """Split a tkdnd drop payload into individual file paths.

        tkdnd joins multiple dropped paths with spaces and wraps any path that
        contains spaces in ``{}``. Returns the list of unwrapped, non-empty paths.
        """
        tokens = re.findall(r'\{[^}]*\}|\S+', data or '')
        return [t.strip('{}') for t in tokens if t.strip('{}')]

    def browse_batch_files(self):
        """Open a multi-select dialog and add the chosen files to the queue."""
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("All Video Files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                ("All files", "*.*"),
            ]
        )
        if paths:
            self.add_batch_files(paths)

    def add_batch_files(self, paths):
        """Append video files to the batch queue, building each output path."""
        for path in paths:
            if not path:
                continue
            fmt = self._format_for_input(path)
            base = os.path.splitext(path)[0]
            output_path = self._output_path_with_format(f"{base}_sdr", fmt)
            self.batch_items.append(
                {'input': path, 'output': output_path, 'format': fmt, 'status': 'Pending'})
        self._refresh_batch_list()
        # Load the top of the queue into the preview (as if it had been selected),
        # unless a file is already loaded -- don't clobber a deliberate selection.
        if (self.batch_items and hasattr(self, 'input_path_var')
                and not self.input_path_var.get()):
            self._load_input_file(self.batch_items[0]['input'])

    def remove_selected_batch_item(self):
        """Remove the highlighted queue entries."""
        if not hasattr(self, 'batch_listbox'):
            return
        selected = sorted(self.batch_listbox.curselection(), reverse=True)
        removed_inputs = [self.batch_items[i]['input'] for i in selected]
        for index in selected:
            del self.batch_items[index]
        self._refresh_batch_list()
        self._resync_preview_after_queue_change(removed_inputs)

    def clear_batch_queue(self):
        """Empty the batch queue."""
        removed_inputs = [it['input'] for it in self.batch_items]
        self.batch_items = []
        self._refresh_batch_list()
        self._resync_preview_after_queue_change(removed_inputs)

    def _resync_preview_after_queue_change(self, removed_inputs):
        """Keep the preview consistent after queue entries are removed/cleared.

        If the file currently shown in the preview was one of the removed
        entries, fall forward to the new top of the queue; if nothing is left,
        unload the preview entirely. A file the user picked by hand (not via the
        queue) is left untouched.
        """
        if not hasattr(self, 'input_path_var'):
            return  # bare/partial instance (tests) -- no preview to sync
        if self.input_path_var.get() not in removed_inputs:
            return  # the shown file wasn't removed -- leave the preview as-is
        if self.batch_items:
            self._load_input_file(self.batch_items[0]['input'])
        else:
            self._unload_input_file()

    def on_batch_item_select(self, event=None):
        """Preview the queue entry the user clicks.

        Selecting a file other than the one already on screen loads it into the
        input/output boxes and renders its frames, exactly as if it had been
        browsed. Re-selecting the file already shown is a no-op (no spinner
        flash), and an empty selection is ignored.
        """
        if not hasattr(self, 'batch_listbox') or not hasattr(self, 'input_path_var'):
            return
        selection = self.batch_listbox.curselection()
        if not selection:
            return
        item = self.batch_items[selection[0]]
        if self.input_path_var.get() == item['input']:
            return  # already showing this file -- skip the reload
        self._load_input_file(item['input'])

    def _refresh_batch_list(self):
        """Redraw the queue listbox from batch_items with per-file status icons."""
        if not hasattr(self, 'batch_listbox'):
            return
        self.batch_listbox.delete(0, tk.END)
        for item in self.batch_items:
            icon = self._STATUS_ICONS.get(item['status'], '•')
            self.batch_listbox.insert(tk.END, f"{icon}  {os.path.basename(item['input'])}")

    def start_batch(self):
        """Begin converting the queued files one after another."""
        if not self.batch_items:
            return False
        # If the queue was already fully processed (nothing left Pending), a
        # fresh Convert click means "run it again" -- requeue every item so the
        # same batch re-runs (e.g. to compare CPU vs GPU). Otherwise we'd jump
        # straight to the completion summary without converting anything.
        if not any(it['status'] == 'Pending' for it in self.batch_items):
            for it in self.batch_items:
                it['status'] = 'Pending'
            self._refresh_batch_list()
        # Mirror the single-file convert path: free the drop target and reveal
        # the cancel button (start_conversion disables the rest of the UI).
        if self.drop_target_registered:
            self.unregister_drop_target()
        self.cancel_button.grid()
        return self._start_next_batch_item()

    def _start_next_batch_item(self):
        """Start the next Pending item, or finish the batch if none remain."""
        item = next((it for it in self.batch_items if it['status'] == 'Pending'), None)
        if item is None:
            self._finish_batch()
            return False

        input_path = os.path.normpath(item['input'])
        if not os.path.isfile(input_path):
            logging.error(f"Batch input not found, skipping: {input_path}")
            item['status'] = 'Failed'
            self._refresh_batch_list()
            return self._start_next_batch_item()  # skip to the next file

        output_path = os.path.normpath(item['output'])
        if os.path.exists(output_path):
            logging.warning(f"Batch output already exists, skipping: {output_path}")
            item['status'] = 'Failed'
            self._refresh_batch_list()
            return self._start_next_batch_item()  # skip to the next file

        item['status'] = 'Converting'
        self._current_batch_item = item
        self._refresh_batch_list()
        self.progress_var.set(0)
        # Switch the preview to the file now being converted, as if it had been
        # selected, so the frames track the queue's progress -- but skip the
        # reload (and its spinner) when that file is already the one on screen.
        current = self.input_path_var.get() if hasattr(self, 'input_path_var') else None
        if current != item['input']:
            self._load_input_file(item['input'])
        gamma = self.gamma_var.get()
        use_gpu = self.gpu_accel_var.get()
        tonemapper = self.tonemap_var.get().lower()
        quality = int(self.quality_var.get())

        conversion_manager.start_conversion(
            input_path, output_path, gamma, use_gpu,
            self.progress_var, self.interactable_elements, self,
            self.open_after_conversion_var.get(), self.cancel_button,
            tonemapper=tonemapper, quality=quality,
            on_complete=self._on_batch_item_complete
        )
        return True

    def _on_batch_item_complete(self, success):
        """Mark the finished item and advance the queue (runs on the main thread)."""
        if self._current_batch_item is not None:
            self._current_batch_item['status'] = 'Done' if success else 'Failed'
        self._current_batch_item = None
        self._refresh_batch_list()
        if not conversion_manager.cancelled:
            self._start_next_batch_item()  # finishes the batch when nothing is left

    def _finish_batch(self):
        """Re-enable the UI and report a one-line summary once the queue drains."""
        done = sum(1 for it in self.batch_items if it['status'] == 'Done')
        failed = sum(1 for it in self.batch_items if it['status'] == 'Failed')
        for element in self.interactable_elements:
            element.config(state='normal')
        self.cancel_button.grid_remove()
        if hasattr(self, 'register_drop_target'):
            self.register_drop_target()
        messagebox.showinfo(
            "Batch Complete", f"Batch finished: {done} succeeded, {failed} failed.")

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
                available = conversion_manager.is_gpu_acceleration_available()
                logging.debug(f"GPU available: {available}")
                if not available:
                    self.gpu_accel_var.set(False)
                    messagebox.showwarning("GPU Acceleration",
                                           "GPU acceleration is not available on this system. It needs either a supported hardware encoder (NVIDIA h264_nvenc, AMD h264_amf, Intel h264_qsv) or GPU tonemapping (libplacebo/Vulkan). Switching to CPU mode.")
            except Exception as e:
                self.gpu_accel_var.set(False)
                logging.error(f"Error checking GPU acceleration: {e}")
                messagebox.showerror("Error", f"An error occurred while checking GPU acceleration:\n{e}")
        # CPU and GPU use different quality scales (CRF vs CQ); re-range the slider.
        if hasattr(self, 'quality_slider'):
            self._apply_quality_range()

    def show_tooltip(self, event, text):
        """Show tooltip window at mouse position"""
        x = event.widget.winfo_rootx() + 25
        y = event.widget.winfo_rooty() + 20

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
        self.custom_time_position = None  # a numbered button overrides a custom seek
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
        """Kick off frame extraction on a worker thread and render on the main thread.

        The ffmpeg calls in :meth:`_extract_preview_images` are slow, so running
        them inline would freeze the Tk event loop. We read the Tk-owned values
        (filter, tonemapper, cached frame) on the calling (main) thread, hand the
        plain values to a daemon worker, then marshal the result back onto the
        main thread via ``root.after`` for the Tk rendering in
        :meth:`_render_preview_images`.
        """
        tonemapper = self.tonemap_var.get().lower()

        # Debounce: every request bumps a generation token. A worker only renders
        # if it is still the most recent request, so a slow extraction kicked off
        # by an earlier change can never clobber a newer preview.
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        generation = self._preview_generation

        def worker():
            try:
                duration = self._get_duration(video_path)
                time_position = self._preview_time_position(duration)
                original, converted = self._extract_preview_images(
                    video_path, time_position, tonemapper
                )
                if generation == self._preview_generation:
                    self._schedule_on_main(lambda: self._render_preview_images(
                        original, converted, time_position, generation))
                # With the visible frame rendered, pre-extract the other seek
                # buttons in the background so their first click is an instant
                # cache hit instead of a multi-hundred-ms ffmpeg wait.
                self._prewarm_other_frames(video_path, duration, tonemapper, generation)
            except Exception as e:  # surface failures on the main thread
                self._schedule_on_main(lambda err=e: self.handle_preview_error(err))

        self._preview_thread = threading.Thread(target=worker, daemon=True)
        self._preview_thread.start()

    def _get_duration(self, video_path):
        """Return the video duration, probing ffprobe only once per file.

        The duration never changes for a given input, so caching it keeps filter/
        frame/tonemapper changes from re-running ffprobe on every preview.
        """
        if getattr(self, '_duration_path', None) == video_path and getattr(self, '_duration_value', None):
            return self._duration_value
        properties = get_video_properties(video_path)
        if not properties or not properties.get('duration'):
            raise ValueError("Failed to retrieve video properties.")
        self._duration_path = video_path
        self._duration_value = properties['duration']
        return self._duration_value

    def _schedule_on_main(self, callback):
        """Run a callback on the Tk main thread, tolerating shutdown races.

        ``root.after`` raises once the interpreter/root is torn down (e.g. the
        window was closed while a preview was still extracting); dropping the
        stale UI update in that case is the correct, safe behavior.
        """
        try:
            self.root.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    _PREVIEW_CACHE_MAX = 48  # bound preview-frame memory (~1.5MB each at 960x540)

    def _extract_preview_images(self, video_path, time_position, tonemapper):
        """Return (original, converted) preview frames, caching ffmpeg results.

        Safe to call off the main thread; must not touch Tk objects. Frames are
        cached by content key so revisiting a frame/tonemapper combo is a cache
        hit with no ffmpeg work. The original HDR frame depends only on the time
        position, so it is shared across tonemappers.
        """
        if not hasattr(self, '_preview_cache_original'):  # bare instances (tests)
            self._preview_cache_original = {}
            self._preview_cache_converted = {}

        time_key = round(time_position, 3)
        original_key = (video_path, time_key)
        original = self._preview_cache_original.get(original_key)
        if original is None:
            original = extract_frame(video_path, time_position=time_position,
                                     width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1])
            self._cache_store(self._preview_cache_original, original_key, original)

        converted_key = (video_path, time_key, tonemapper)
        converted = self._preview_cache_converted.get(converted_key)
        if converted is None:
            converted = extract_frame_with_conversion(
                video_path, gamma=1.0,
                tonemapper=tonemapper, time_position=time_position,
                width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1]
            )
            self._cache_store(self._preview_cache_converted, converted_key, converted)
        return original, converted

    def _cache_store(self, cache, key, value):
        """Insert into a preview cache, evicting the oldest entry past the cap.

        Locked: the display worker and the background pre-warm worker can write
        concurrently, and the FIFO eviction iterates the dict.
        """
        if not hasattr(self, '_cache_lock'):  # bare instances (tests)
            self._cache_lock = threading.Lock()
        with self._cache_lock:
            cache[key] = value
            if len(cache) > self._PREVIEW_CACHE_MAX:
                cache.pop(next(iter(cache)))

    def _prewarm_other_frames(self, video_path, duration, tonemapper, generation):
        """Pre-extract non-visible seek frames using 2 batch ffmpeg calls.

        One call extracts all uncached original frames; a second call extracts
        the converted (tonemapped) versions. Both run on the preview worker
        thread after the visible frame has been rendered. If a newer preview
        request supersedes this one the method bails between the two calls so
        we never waste a long tonemap run on stale state. Failures are swallowed
        so the background work never crashes the worker.
        """
        if generation != self._preview_generation:
            return

        if not hasattr(self, '_preview_cache_original'):
            self._preview_cache_original = {}
            self._preview_cache_converted = {}

        # Collect timestamps for frames not yet in either cache.
        positions: list[float] = []
        for index in range(1, self.total_frames + 1):
            if index == self.current_frame_index:
                continue
            t = (index / (self.total_frames + 1)) * duration
            t_key = round(t, 3)
            if ((video_path, t_key) not in self._preview_cache_original or
                    (video_path, t_key, tonemapper) not in self._preview_cache_converted):
                positions.append(t)

        if not positions:
            return

        # Batch 1: all original frames in one ffmpeg process.
        try:
            originals = extract_frames_batch(
                video_path, positions, PREVIEW_SIZE[0], PREVIEW_SIZE[1])
            for t, img in zip(positions, originals):
                self._cache_store(
                    self._preview_cache_original, (video_path, round(t, 3)), img)
        except Exception:
            logging.exception('preview batch original pre-warm failed')

        if generation != self._preview_generation:
            return

        # Batch 2: all converted frames in one ffmpeg process.
        try:
            converted = extract_frames_with_conversion_batch(
                video_path, positions, 1.0, tonemapper,
                PREVIEW_SIZE[0], PREVIEW_SIZE[1])
            for t, img in zip(positions, converted):
                self._cache_store(
                    self._preview_cache_converted,
                    (video_path, round(t, 3), tonemapper), img)
        except Exception:
            logging.exception('preview batch converted pre-warm failed')

    def _reset_custom_seek(self):
        """Clear any active custom seek so a newly loaded file starts on frame 1."""
        self.custom_time_position = None
        self.custom_time_var.set('')

    def _reset_preview_cache(self):
        """Drop all cached preview frames (e.g. when a new file is loaded)."""
        self._preview_cache_original = {}
        self._preview_cache_converted = {}
        # MAXFALL is memoized per path in utils; drop it so a replaced file at the
        # same path re-probes instead of reusing stale mastering metadata.
        clear_maxfall_cache()

    def _render_preview_images(self, original_image, converted_image_base, time_position,
                               generation=None):
        """Apply extracted frames to the Tk labels. Must run on the main thread.

        ``generation`` re-checks the debounce token at render time so a callback
        already queued on the event loop is dropped if a newer preview superseded
        it between scheduling and execution.
        """
        if generation is not None and generation != getattr(self, '_preview_generation', generation):
            return
        # Frames are ready: drop the spinner and reveal the preview.
        self._hide_preview_loading()
        self.original_image = original_image
        self.last_time_position = time_position
        self.converted_image_base = converted_image_base

        # Render both panes at the right size. First preview uses a generous
        # default (then the window auto-fits to it). Subsequent previews reuse
        # _preview_render_size — the size from the last successful render —
        # because the image labels are hidden during loading, which makes
        # image_frame.winfo_height() unreliable for computing the target size
        # (it reflects the full row-1 height, not just the spinner height, and
        # subtracting _PREVIEW_HEIGHT_RESERVE can over-constrain the image).
        # Window resizes are handled independently by _rescale_preview_to_window
        # which calls _preview_target_size() while images are still visible.
        if getattr(self, '_window_auto_fitted', False):
            size = getattr(self, '_preview_render_size', None) or self._preview_target_size()
        else:
            size = self._initial_preview_size()
        self._render_preview_at_size(size)

        self._reveal_preview()  # show titles, frame buttons and the images
        self.adjust_window_size()  # Ensure window size is adjusted after displaying images

    def _apply_gamma_to_preview(self):
        """Apply the current gamma to the cached display-sized SDR frame.

        Runs on every gamma-slider tick; deliberately cheap (one PIL point() pass
        on a 960x540 image, no extraction, no window resize).
        """
        base = self._converted_preview_base
        if base is None:
            return
        adjusted = self.adjust_gamma(base, self.gamma_var.get())
        converted_photo = ImageTk.PhotoImage(adjusted)
        self.converted_image_label.config(image=converted_photo)
        self._keep_image_ref(self.converted_image_label, converted_photo)

    def _jump_slider_to_click(self, slider, event, snap=False):
        """Move a ttk.Scale's knob straight to a trough click instead of nudging.

        ttk.Scale's default behavior when you click the trough (the bar, not the
        knob) is to step the value by a fixed page increment toward the click and
        keep stepping while held -- so the knob slides to the end rather than
        landing where you clicked. Intercept trough clicks and set the value from
        the click position so the knob jumps under the cursor; ``snap`` rounds to
        whole steps (the quality slider). ``Scale.set`` fires the slider's
        ``command`` so dependent state refreshes. Clicks on the knob itself fall
        through to native drag.
        """
        if 'slider' in slider.identify(event.x, event.y):
            return  # clicking the knob: let the default drag handle it
        width = slider.winfo_width()
        if width <= 0:
            return
        fraction = min(max(event.x / width, 0.0), 1.0)
        low = float(slider.cget('from'))
        high = float(slider.cget('to'))
        value = low + fraction * (high - low)
        if snap:
            value = round(value)
        slider.set(value)
        return 'break'  # suppress the default page-jump

    def _gamma_slider_jump(self, event):
        """Jump the gamma knob to a trough click (continuous, no snapping)."""
        return self._jump_slider_to_click(self.gamma_slider, event)

    def _quality_slider_jump(self, event):
        """Snap the quality knob to the nearest whole CRF/CQ step at a click."""
        return self._jump_slider_to_click(self.quality_slider, event, snap=True)

    def _show_preview_loading(self):
        """Show the loading spinner and hide the preview until frames are ready."""
        # Freeze the window at its current size so removing the image labels
        # doesn't cause a visible shrink while the spinner is shown.
        if hasattr(self, 'root'):
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w > 1 and h > 1:
                self.root.minsize(w, h)
        self.original_title_label.grid_remove()
        self.converted_title_label.grid_remove()
        self.button_container.grid_remove()
        self.original_image_label.grid_remove()
        self.converted_image_label.grid_remove()
        self.loading_frame.grid()
        self.loading_bar.start(12)

    def _hide_preview_loading(self):
        """Stop and hide the loading spinner."""
        self.loading_bar.stop()
        self.loading_frame.grid_remove()
        if hasattr(self, 'root'):
            self._apply_min_window_size()  # restore the real minsize

    def _reveal_preview(self):
        """Reveal the titles, frame buttons and images once frames have rendered."""
        self.original_image_label.grid()
        self.converted_image_label.grid()
        self.original_title_label.grid()
        self.converted_title_label.grid()
        self.button_container.grid()

    def on_gamma_change(self, event=None):
        """Handle gamma slider/entry changes.

        Gamma is a pure post-process on the already-extracted SDR frame, so when a
        preview frame is cached we re-apply gamma directly instead of re-running
        the ffmpeg extraction pipeline.
        """
        if self.display_image_var.get() and self._converted_preview_base is not None:
            self._apply_gamma_to_preview()
        else:
            self.update_frame_preview()

    def _preview_in_cache(self, video_path: str) -> bool:
        """Return True if both frames for the current state are already cached."""
        if not hasattr(self, '_preview_cache_original'):
            return False
        duration = getattr(self, '_duration_value', None)
        if getattr(self, '_duration_path', None) != video_path or not duration:
            return False
        time_position = self._preview_time_position(duration)
        time_key = round(time_position, 3)
        tonemapper = self.tonemap_var.get().lower()
        return (
            (video_path, time_key) in self._preview_cache_original
            and (video_path, time_key, tonemapper) in self._preview_cache_converted
        )

    def update_frame_preview(self, event=None):
        """Update the frame preview without blocking the UI."""
        if self.display_image_var.get() and self.input_path_var.get():
            try:
                video_path = self.input_path_var.get()
                self.error_label.config(text="")
                # Only show the spinner when both frames aren't already cached;
                # cached hits return in <1 ms so no spinner is needed and
                # skipping it prevents the one-frame flash when switching buttons.
                if not self._preview_in_cache(video_path):
                    self._show_preview_loading()
                self.display_frames(video_path)
                self.arrange_widgets(image_frame=True)
            except Exception as e:
                self.handle_preview_error(e)
        else:
            self.clear_preview()
            self._hide_preview_loading()
            self.original_title_label.grid_remove()
            self.converted_title_label.grid_remove()
            self.button_container.grid_remove()  # Hide frame buttons
            self.arrange_widgets(image_frame=False)
        self.tonemap_combobox.selection_clear()