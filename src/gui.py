import os
import sys
import tkinter as tk
import webbrowser
from typing import TypeVar
from tkinter import filedialog, messagebox
from tkinter import ttk
from dark_theme import apply_dark_theme
from conversion import conversion_manager
from utils import (get_video_properties, get_maxcll, TONEMAP, clear_maxfall_cache,
                   is_gpu_only_tonemapper, vulkan_libplacebo_available,
                   VIDEO_FILE_FILTER)
from settings import load_settings, save_settings
from licensing import InvalidKeyError, DeviceLimitError, NetworkError, LicenseError
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from dialogs import _LicenseDialog, _UpdateDialog, activate_license
from preview import (
    DEFAULT_MIN_SIZE, PREVIEW_SIZE, INITIAL_PANE_SIZE,
    _MIN_PANE_W, _RESIZE_DEBOUNCE_MS, _PREVIEW_WIDTH_RESERVE,
    _PREVIEW_HEIGHT_RESERVE, _MIN_SIZE_MARGIN, _PREVIEW_POOL_WORKERS,
    _INITIAL_WIDTH_STRETCH, _HDRPreviewMixin,
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
    setattr(_src_pkg, 'dialogs', _sys.modules['dialogs'])
    setattr(_src_pkg, 'preview', _sys.modules['preview'])
    setattr(_src_pkg, 'batch', _sys.modules['batch'])
    del _src_pkg
del _sys

# Re-export webbrowser so existing patches (patch('src.gui.webbrowser')) still resolve.
# (The name was importable from this module before the dialogs split.)
webbrowser = webbrowser  # noqa: F811


_Number = TypeVar('_Number', int, float)


def _clamp(value: _Number, lo: _Number, hi: _Number) -> _Number:
    """Clamp value into [lo, hi]. lo/hi may be given in either order."""
    lo, hi = min(lo, hi), max(lo, hi)
    return min(max(value, lo), hi)


class HDRConverterGUI(_BatchMixin, _HDRPreviewMixin):
    """Main application window for the HDR to SDR Converter."""

    _licensed: bool = True  # class-level default for bare instances that bypass __init__

    # Output containers the user can pick.
    _OUTPUT_FORMATS = ['MP4', 'MKV', 'MOV']
    _ISSUES_URL = 'https://github.com/TORlN/HDR-to-SDR/issues'
    _INPUT_FORMAT_MAP = {'mp4': 'MP4', 'm4v': 'MP4', 'mov': 'MOV', 'mkv': 'MKV'}

    # Quality slider ranges as (worst, best): left end = smaller file, right = better.
    _CRF_RANGE = (28, 17)
    _CQ_RANGE = (30, 15)

    # Target Bitrate mode's slider bounds and unknown-source fallback.
    _BITRATE_FLOOR_KBPS = 1000
    _BITRATE_FALLBACK_KBPS = 8000  # matches conversion.py's nvenc/qsv zero-bitrate guard

    # Coalesces rapid-fire batch-listbox rebuilds (e.g. every tick of a
    # gamma/quality slider drag) into one refresh -- see
    # _write_back_current_settings/_schedule_batch_list_refresh.
    _BATCH_LIST_REFRESH_DEBOUNCE_MS = 150

    _QUALITY_MODE_TO_INTERNAL = {'Constant Quality': 'cq', 'Target Bitrate': 'bitrate'}
    _QUALITY_MODE_FROM_INTERNAL = {'cq': 'Constant Quality', 'bitrate': 'Target Bitrate'}

    # Appended to a GPU-only tonemapper's combobox label when it's unselectable
    # (GPU tonemapping isn't active) -- the entry stays visible/greyed instead
    # of being removed from the list, per _apply_tonemap_choices.
    _GPU_ONLY_SUFFIX = " (GPU Only)"

    def __init__(self, root: "TkinterDnD.Tk", licensed: bool = False) -> None:
        """Initialize the GUI and set up all components."""
        self.root = root
        self._licensed = licensed
        from updater import APP_VERSION
        self.root.title(f"HDR to SDR Converter v{APP_VERSION}")
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
        # Persisted export setting: applies the gamut-correction LUT on GPU
        # exports (costs a CPU round-trip, ~2x slower at 4K -- see
        # build_libplacebo_filter's docstring). No effect on CPU exports,
        # which always apply it. Also drives the preview pane (see
        # preview.py's display_frames), so toggling it shows the same
        # difference real GPU export would produce.
        self.lut_export_var = tk.BooleanVar(value=_s['lut_enabled'])
        self.tonemap_var = tk.StringVar(value=_s['tonemapper'])
        # Tracks the last selection that was actually valid, so a click on a
        # greyed-out GPU-only row (still clickable -- see _on_tonemap_selected)
        # has something sane to revert to.
        self._last_valid_tonemapper = self.tonemap_var.get()
        self.quality_var = tk.IntVar(value=_s['quality'])
        self.bitrate_var = tk.IntVar(value=_s['quality_bitrate_kbps'])
        self.quality_mode_var = tk.StringVar(
            value=self._QUALITY_MODE_FROM_INTERNAL.get(_s['quality_mode'], 'Constant Quality'))
        self.quality_display_var = tk.StringVar()
        # Target Bitrate reseeds to 50% of the source whenever a new file is
        # loaded (see _update_info_label) -- set True there, consumed here.
        self._bitrate_needs_reseed = False
        self.quality_var.trace_add('write', self._sync_quality_display)
        self.bitrate_var.trace_add('write', self._sync_quality_display)
        self._sync_quality_display()
        self.format_var = tk.StringVar(value=_s['filetype'])
        # Not persisted to settings -- resets per file load, since it's only
        # meaningful for the current source (see _update_bit_depth_choice).
        # Queued files keep their own choice via the item's
        # settings['bit_depth_choice'] key, restored on (re)load (see
        # _on_bit_depth_toggle).
        self.bit_depth_var = tk.StringVar(value='10-bit')
        # Mirrors settings['bitrate_customized'] for whichever item is
        # currently loaded, exactly like bit_depth_var mirrors
        # bit_depth_choice: True once the user has deliberately dragged this
        # item's bitrate slider, so the Target Bitrate reseed (see
        # _apply_bitrate_range) stops overriding their choice for this item.
        self._bitrate_customized_for_current_item = False
        self.custom_time_var = tk.StringVar()
        self.custom_time_position: float | None = None
        self.batch_items: list[dict] = []  # type: ignore[type-arg]
        self._current_batch_item: dict | None = None  # type: ignore[type-arg]
        self._batch_conflict_groups: list[list[dict]] | None = None  # type: ignore[type-arg]
        self._batch_conflict_selection: dict[int, bool] = {}
        self.tooltip = None
        self.current_frame_index = 1
        self.total_frames = 5
        self.last_time_position: float | None = None
        self._preview_generation = 0
        self._preview_pool = ThreadPoolExecutor(
            max_workers=_PREVIEW_POOL_WORKERS, thread_name_prefix='frame-fetch')
        self._preview_thread: Future | None = None
        self._converted_preview_base: Image.Image | None = None
        self._duration_path: str | None = None
        self._duration_value: float | None = None
        self._source_bit_depth: int = 8
        self._preview_cache_original: dict = {}
        self._preview_cache_converted: dict = {}
        self._cache_lock = threading.Lock()

        self.create_widgets()
        self.configure_grid()
        self._apply_license_state(licensed)

        self._min_window_size = self._compute_min_window_size()
        self._apply_min_window_size()
        self._apply_initial_window_geometry()

        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.handle_file_drop)
        self.drop_target_registered = True

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._resize_job: str | None = None
        self._batch_list_refresh_job: str | None = None
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
        if os.environ.get('HDRSDR_DEV_SHOW_UPDATE_DIALOG') == '1':
            from updater import APP_VERSION, RELEASES_URL
            self._show_update_dialog(
                APP_VERSION, '99.0.0', 'https://example.com/HDR_to_SDR_Setup.exe',
                RELEASES_URL)
            return

        def _worker() -> None:
            from updater import check_for_update, APP_VERSION
            result = check_for_update()
            if result:
                new_ver, url, release_url = result
                self.root.after(0, lambda: self._show_update_dialog(
                    APP_VERSION, new_ver, url, release_url))
        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_dialog(self, current_ver: str, new_ver: str, url: str,
                             release_url: str) -> None:
        _UpdateDialog(self.root, current_ver, new_ver, url, release_url)

    def _open_issues_page(self) -> None:
        webbrowser.open(self._ISSUES_URL)

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
        # A ttk.Combobox must stay 'readonly' when enabled, not 'normal' (which
        # would let the user free-type into it) -- matching format_combobox's
        # existing pattern, where 'state' is never set to the plain pro/disabled
        # toggle directly.
        self.quality_mode_combobox.config(state='readonly' if licensed else 'disabled')
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
            # The mode combobox only gets disabled below, not reset -- a
            # Target Bitrate choice saved while licensed would otherwise
            # survive into an unlicensed session (convert_video has no
            # license gate of its own on quality_mode).
            if self.quality_mode_var.get() != 'Constant Quality':
                self.quality_mode_var.set('Constant Quality')
                self._apply_quality_mode()

        self.add_files_button.config(state=pro)
        self.remove_batch_button.config(state=pro)
        self.clear_batch_button.config(state=pro)
        self.apply_settings_button.config(state=pro)

        # Refresh the 12-bit toggle's label/enabled state and the info strip's
        # Pro hint immediately, in case a >10-bit file is already loaded when
        # the license is activated.
        self._update_bit_depth_choice()
        self._refresh_info_label_text()

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
            self.gpu_accel_checkbutton, self.bit_depth_10_radio, self.batch_listbox,
        ]
        premium = [
            self.quality_slider, self.quality_mode_combobox, self.format_combobox,
            self.custom_time_entry, self.custom_seek_button,
            self.add_files_button, self.clear_batch_button, self.remove_batch_button,
            self.bit_depth_12_radio, self.apply_settings_button,
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
                'quality_mode': self._QUALITY_MODE_TO_INTERNAL.get(
                    self.quality_mode_var.get(), 'cq'),
                'quality_bitrate_kbps': self.bitrate_var.get(),
                'filetype': self.format_var.get(),
                'lut_enabled': self.lut_export_var.get(),
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
        self.output_entry.bind('<Return>', self._on_output_path_change)
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

        self.tonemap_frame = ttk.Frame(self.control_frame)
        self.tonemap_frame.grid(row=3, column=1, sticky=tk.W, padx=(10, 10), pady=(5, 0))
        self.tonemap_combobox = ttk.Combobox(
            self.tonemap_frame, textvariable=self.tonemap_var,
            values=TONEMAP, state='readonly', width=15)
        self.tonemap_combobox.grid(row=0, column=0, padx=(0, 5))
        self.tonemap_combobox.bind('<<ComboboxSelected>>', self._on_tonemap_selected)
        info_button_tonemap = ttk.Label(self.tonemap_frame, text="ⓘ", cursor="hand2")
        info_button_tonemap.grid(row=0, column=1)
        tooltip_text_tonemap = (
            "Reinhard: Basic HDR to SDR conversion\n"
            "Mobius: Natural-looking conversion\n"
            "Hable: Game-like conversion (Cyberpunk 2077)\n"
            "BT.2390: Broadcast-standard highlight rolloff (GPU Only)\n"
            "Spline: Scene-adaptive libplacebo default (GPU Only)"
        )
        info_button_tonemap.bind('<Enter>',
                                  lambda e: self.show_tooltip(e, tooltip_text_tonemap))
        info_button_tonemap.bind('<Leave>', self.hide_tooltip)

        self.lut_export_frame = ttk.Frame(self.tonemap_frame)
        self.lut_export_frame.grid(row=0, column=2, sticky=tk.W, padx=(15, 0))
        self.lut_export_checkbutton = ttk.Checkbutton(
            self.lut_export_frame, text="Accurate GPU Color",
            variable=self.lut_export_var, command=self._on_lut_export_toggle)
        self.lut_export_checkbutton.grid(row=0, column=0)
        info_button_lut_export = ttk.Label(self.lut_export_frame, text="ⓘ", cursor="hand2")
        info_button_lut_export.grid(row=0, column=1)
        tooltip_text_lut_export = (
            "Applies precise BT.2020→BT.709 color correction on GPU exports.\n"
            "Uncheck for ~2-3x faster GPU exports using libplacebo's own gamut\n"
            "conversion instead; the difference is usually invisible on real\n"
            "footage (it only shows on strongly saturated colors, e.g. neon\n"
            "signs). No effect on CPU exports, which always apply accurate\n"
            "color correction."
        )
        info_button_lut_export.bind(
            '<Enter>', lambda e: self.show_tooltip(e, tooltip_text_lut_export))
        info_button_lut_export.bind('<Leave>', self.hide_tooltip)

        # Conditional 10/12-bit toggle: hidden unless the loaded source has
        # more than 10 bits to preserve (see _update_bit_depth_choice). Nested
        # inside tonemap_frame so it sits next to the tonemapper selector but
        # lives in control_frame's stretchy column 1 -- gridding it into
        # column 2 would widen the Browse/format/gamma widgets stacked there.
        self.bit_depth_frame = ttk.Frame(self.tonemap_frame)
        self.bit_depth_frame.grid(row=0, column=3, sticky=tk.W, padx=(15, 0))
        ttk.Label(self.bit_depth_frame, text="Bit Depth:").grid(row=0, column=0, sticky=tk.W)
        self.bit_depth_10_radio = ttk.Radiobutton(
            self.bit_depth_frame, text='10-bit',
            variable=self.bit_depth_var, value='10-bit',
            command=self._on_bit_depth_toggle)
        self.bit_depth_10_radio.grid(row=0, column=1, padx=(5, 0))
        self.bit_depth_12_radio = ttk.Radiobutton(
            self.bit_depth_frame, text='12-bit (CPU Only)',
            variable=self.bit_depth_var, value='12-bit',
            command=self._on_bit_depth_toggle)
        self.bit_depth_12_radio.grid(row=0, column=2, padx=(5, 0))
        self.bit_depth_frame.grid_remove()

        self.quality_mode_frame = ttk.Frame(self.control_frame)
        self.quality_mode_frame.grid(row=4, column=1, sticky=tk.W, padx=(10, 10), pady=(5, 0))
        self.quality_mode_combobox = ttk.Combobox(
            self.quality_mode_frame, textvariable=self.quality_mode_var,
            values=['Constant Quality', 'Target Bitrate'], state='readonly', width=15)
        self.quality_mode_combobox.grid(row=0, column=0, padx=(0, 5))
        self.quality_mode_combobox.bind('<<ComboboxSelected>>', self._on_quality_mode_selected)
        info_button_quality_mode = ttk.Label(self.quality_mode_frame, text="ⓘ", cursor="hand2")
        info_button_quality_mode.grid(row=0, column=1)
        info_button_quality_mode.bind(
            '<Enter>', lambda e: self.show_tooltip(e, self._quality_mode_tooltip_text()))
        info_button_quality_mode.bind('<Leave>', self.hide_tooltip)

        quality_frame = ttk.Frame(self.control_frame)
        quality_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W + tk.E, pady=(5, 0))
        ttk.Label(quality_frame, text="Quality:").grid(row=0, column=0, sticky=tk.W)
        self.quality_slider = ttk.Scale(
            quality_frame, from_=self._CRF_RANGE[0], to=self._CRF_RANGE[1],
            orient=tk.HORIZONTAL, length=200, command=self._on_quality_change)
        self.quality_slider.grid(row=0, column=1, sticky=tk.W + tk.E, padx=(10, 8))
        self.quality_slider.set(self.quality_var.get())
        self.quality_slider.bind('<Button-1>', self._quality_slider_jump)
        self.quality_value_label = ttk.Label(
            quality_frame, textvariable=self.quality_display_var, width=10)
        self.quality_value_label.grid(row=0, column=2, sticky=tk.W)
        ttk.Label(quality_frame, text="Smaller File  ◀──▶  Better Quality",
                  foreground='gray').grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=(10, 0))
        quality_frame.columnconfigure(1, weight=1)

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
        self.info_label.grid(row=6, column=0, columnspan=3, sticky=tk.W, padx=(0, 10))
        self.info_label.grid_remove()

        self.error_label = ttk.Label(self.control_frame, text='', foreground='red')
        self.error_label.grid(row=7, column=0, columnspan=3, sticky=tk.W)

        self._pro_banner = ttk.Frame(self.control_frame)
        self._pro_banner.grid(row=8, column=0, columnspan=3,
                               sticky=tk.W + tk.E, pady=(6, 2))
        self._pro_banner.grid_remove()
        ttk.Label(
            self._pro_banner,
            text='Quality, batch, container and 12-bit require Pro.',
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

        self.footer_frame = ttk.Frame(self.root)
        self.footer_frame.grid(row=4, column=0, sticky=tk.W + tk.E, padx=10, pady=(0, 5))
        self.footer_frame.columnconfigure(0, weight=1)

        self.feedback_link = ttk.Label(
            self.footer_frame, text="Report an Issue",
            foreground='#4ea1ff', cursor="hand2")
        self.feedback_link.grid(row=0, column=1, sticky=tk.E)
        self.feedback_link.bind('<Button-1>', lambda _e: self._open_issues_page())

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
        self.apply_settings_button = ttk.Button(
            batch_buttons, text="Apply to All", command=self.apply_settings_to_all_batch_items)
        self.apply_settings_button.grid(row=0, column=3, padx=(0, 5))
        self.batch_review_cancel_button = ttk.Button(
            batch_buttons, text="Cancel Review", command=self._cancel_batch_conflict_review)
        self.batch_review_cancel_button.grid(row=0, column=4, padx=(0, 5))
        self.batch_review_cancel_button.grid_remove()
        self.batch_settings_info_button = ttk.Label(
            batch_buttons, text="ⓘ", cursor="hand2")
        self.batch_settings_info_button.grid(row=0, column=5, padx=(5, 0))
        self.batch_settings_info_button.bind(
            '<Enter>', lambda e: self.show_tooltip(e, self._batch_settings_tooltip_text()))
        self.batch_settings_info_button.bind('<Leave>', self.hide_tooltip)

        self.batch_hint_label = ttk.Label(
            self.batch_frame, foreground='gray',
            text="Add or drop multiple files to convert them in sequence.")
        self.batch_hint_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(4, 4))

        self.batch_listbox = tk.Listbox(self.batch_frame, height=8, activestyle='none')
        self.batch_listbox.grid(row=2, column=0, sticky=tk.W + tk.E + tk.N + tk.S)
        batch_scroll = ttk.Scrollbar(
            self.batch_frame, orient=tk.VERTICAL, command=self.batch_listbox.yview)
        batch_scroll.grid(row=2, column=1, sticky=tk.N + tk.S)
        self.batch_frame.rowconfigure(2, weight=1)
        self.batch_listbox.config(yscrollcommand=batch_scroll.set)
        self.batch_listbox.bind('<<ListboxSelect>>', self.on_batch_item_select)
        self.batch_listbox.bind('<Button-1>', self._on_batch_listbox_click)

        self.interactable_elements = [
            self.browse_button, self.convert_button, self.gamma_slider,
            self.open_after_conversion_checkbutton, self.display_image_checkbutton,
            self.input_entry, self.output_entry, self.gamma_entry,
            self.gpu_accel_checkbutton, self.batch_listbox,
            self.quality_slider, self.quality_mode_combobox,
            self.format_combobox,
            self.custom_time_entry, self.custom_seek_button,
            self.add_files_button, self.clear_batch_button, self.remove_batch_button,
            self.bit_depth_10_radio, self.bit_depth_12_radio, self.apply_settings_button,
        ]

        self._apply_quality_mode()
        self._apply_tonemap_choices()
        self._apply_lut_export_availability()

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
                VIDEO_FILE_FILTER,
                ("MP4 files", "*.mp4"),
                ("MKV files", "*.mkv"),
                ("MOV files", "*.mov"),
                ("AVI files", "*.avi"),
                ("WebM files", "*.webm"),
                ("M4V files", "*.m4v"),
                ("All files", "*.*"),
            ]
        )
        if not file_path:
            return
        if self._licensed:
            # Mirrors handle_file_drop's single-file licensed path: route
            # through the queue so Browse and drag-and-drop behave the same.
            self.add_batch_files([file_path])
            if self.input_path_var.get() != file_path:
                self._load_input_file(file_path)
        else:
            self._load_input_file(file_path)

    def _load_input_file(self, file_path: str) -> None:
        """Load a file into the input/output boxes and refresh the preview."""
        self.input_path_var.set(file_path)
        fmt = self._format_for_input(file_path) if self._licensed else 'MP4'
        self.format_var.set(fmt)
        self.original_image = None
        self.converted_image_base = None
        self._reset_custom_seek()
        self._reset_preview_cache()
        self._restoring_batch_item_settings = True
        try:
            item = self._batch_item_for_current_input()
            # A queued item's own (possibly user-edited) output path wins over
            # recomputing the auto default, so a prior edit survives reselect
            # instead of being silently overwritten back to <name>_sdr.<ext>.
            if item is not None and item.get('output'):
                self.output_path_var.set(item['output'])
            else:
                base = os.path.splitext(file_path)[0]
                self.output_path_var.set(self._output_path_with_format(f"{base}_sdr", fmt))
            self._update_info_label(file_path)
            if item is not None and item.get('settings'):
                self._restore_settings_dict(item['settings'])
        finally:
            self._restoring_batch_item_settings = False
        self._write_back_current_settings()
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
        # Drop the probe state so nothing (info strip, bit-depth toggle) can be
        # re-rendered later from a file that is no longer loaded.
        self._source_bit_depth = 8
        self._cached_props = None
        self._cached_maxcll = None
        # A deliberate Target Bitrate customization is only meaningful for
        # the file it was made on -- without this, a later unrelated file
        # queued while nothing is loaded would inherit the stale flag (and
        # get seeded from a bogus fraction computed against the
        # unknown-source fallback ceiling, since it hasn't been probed yet).
        self._bitrate_customized_for_current_item = False
        self._update_bit_depth_choice()  # hides the 10/12-bit toggle
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
        self._write_back_current_settings()

    def _on_output_path_change(self, event: object = None) -> None:
        """<Return> handler for output_entry: persist a manually-typed output
        path onto the selected queue item (mirrors on_gamma_change/
        gamma_entry) -- without this, editing the box for a queued item is
        silently discarded the moment the batch actually runs, since
        _start_next_batch_item reads item['output'], not the live var."""
        self._write_back_current_settings()

    # ── Output Color Depth ──────────────────────────────────────────────────────

    def _update_bit_depth_choice(self) -> None:
        """Show the 10-bit/12-bit toggle only for sources where the choice is
        actually meaningful (>10-bit) -- hidden otherwise, matching the fully
        automatic 8/10-bit behavior for lower-bit-depth sources. 12-bit is
        CPU-only and Pro-gated, so its label/enabled state track license
        state; the toggle defaults to 10-bit each time it (re)appears."""
        if not hasattr(self, 'bit_depth_frame'):
            return  # bare/partially-initialized instance (test contexts only)
        source = getattr(self, '_source_bit_depth', 8)
        if source > 10:
            # Default to 10-bit, but a queued file remembers its own choice
            # (stored by _on_bit_depth_toggle) so batch runs and queue clicks
            # restore it instead of silently reverting to 10-bit.
            choice = '10-bit'
            if self._licensed:
                item = self._batch_item_for_current_input()
                if item is not None:
                    choice = item.get('settings', {}).get('bit_depth_choice', '10-bit')
            self.bit_depth_var.set(choice)
            pro_text = 'CPU Only' if self._licensed else 'Pro'
            self.bit_depth_12_radio.config(
                text=f'12-bit ({pro_text})',
                state='normal' if self._licensed else 'disabled')
            self.bit_depth_frame.grid()
        else:
            self.bit_depth_frame.grid_remove()

    def _selected_bit_depth(self) -> int:
        """The output bit depth for the current source: 8/10-bit are fully
        automatic; above 10-bit, a licensed user's 10/12-bit toggle choice
        is honored (unlicensed is capped at 10 regardless of the var)."""
        source = getattr(self, '_source_bit_depth', 8)
        if source <= 8:
            return 8
        if source <= 10:
            return 10
        if self._licensed and getattr(self, 'bit_depth_var', None) is not None \
                and self.bit_depth_var.get() == '12-bit':
            return 12
        return 10

    def _current_settings_dict(self) -> dict:  # type: ignore[type-arg]
        """Snapshot every per-file conversion control's live value. Used to
        seed a newly-queued item, to restore/compare a queued item's stored
        settings against what's currently shown, and as the source for
        "Apply to All"."""
        return {
            'gamma': self.gamma_var.get(),
            'quality_mode': self._QUALITY_MODE_TO_INTERNAL.get(
                self.quality_mode_var.get(), 'cq'),
            'quality': self.quality_var.get(),
            'bitrate_fraction': self.bitrate_var.get() / self._bitrate_ceiling_kbps(),
            'tonemapper': self.tonemap_var.get(),
            'gpu_accel': self.gpu_accel_var.get(),
            'bit_depth_choice': self.bit_depth_var.get(),
            'bitrate_customized': getattr(self, '_bitrate_customized_for_current_item', False),
            'lut_enabled': self.lut_export_var.get(),
        }

    def _restore_settings_dict(self, settings: dict) -> None:  # type: ignore[type-arg]
        """Push a stored settings snapshot into the live controls (the
        counterpart to _current_settings_dict), then re-run the existing
        range/fallback logic so the restored values are re-validated against
        whichever file is now loaded -- e.g. Target Bitrate's ceiling clamps
        to this file's own source bitrate, and a GPU-only tonemapper falls
        back to Mobius if this file's load left GPU accel off.

        Self-guarded against _write_back_current_settings: the slider moves
        below are internal, intermediate state, not a user edit, and must
        not be stamped onto whichever item is currently loaded before this
        restore completes. _load_input_file (the only production caller)
        already wraps this in the same guard, but that's a caller
        convention, not an enforced invariant -- guarding here too means a
        future direct caller can't reintroduce the leak."""
        already_restoring = getattr(self, '_restoring_batch_item_settings', False)
        self._restoring_batch_item_settings = True
        try:
            self.gamma_var.set(settings.get('gamma', self.gamma_var.get()))
            self.gpu_accel_var.set(settings.get('gpu_accel', self.gpu_accel_var.get()))
            self.tonemap_var.set(settings.get('tonemapper', self.tonemap_var.get()))
            self.lut_export_var.set(settings.get('lut_enabled', self.lut_export_var.get()))
            self.quality_mode_var.set(self._QUALITY_MODE_FROM_INTERNAL.get(
                settings.get('quality_mode', 'cq'), 'Constant Quality'))
            self.quality_var.set(settings.get('quality', self.quality_var.get()))
            self._bitrate_customized_for_current_item = settings.get('bitrate_customized', False)
            if self._bitrate_customized_for_current_item:
                ceiling = self._bitrate_ceiling_kbps()
                fraction = settings.get('bitrate_fraction', 0.5)
                value = round(fraction * ceiling / 500) * 500
                value = _clamp(value, self._BITRATE_FLOOR_KBPS, ceiling)
                self.bitrate_var.set(value)
                self._bitrate_needs_reseed = False
            if hasattr(self, 'quality_slider'):
                self._apply_quality_mode()
            if hasattr(self, 'tonemap_combobox'):
                self._apply_tonemap_choices()
            if hasattr(self, 'lut_export_checkbutton'):
                self._apply_lut_export_availability()
        finally:
            self._restoring_batch_item_settings = already_restoring

    def _write_back_current_settings(self, debounce_listbox: bool = False) -> None:
        """Persist the live controls' current values onto whichever queue
        item is loaded, if any -- the counterpart to _restore_settings_dict.
        Called from every control's change handler so editing a control
        while a file is selected edits that file's settings only.

        No-ops while _load_input_file is mid-restore: _update_info_label's
        internal slider-range remap (_apply_quality_mode) and
        _restore_settings_dict's own re-validation calls both move the
        quality slider programmatically, which synchronously fires this
        method via the slider's command callback -- before the target
        item's settings have actually been restored into the live
        controls. Without this guard, that premature write-back stamps
        the *previous* item's stale live-control values onto the
        newly-selected item, which _restore_settings_dict then faithfully
        restores back into the widgets -- corrupting the newly-loaded
        item's settings on every queue reselect.

        debounce_listbox=True (used by the gamma/quality slider drag
        handlers, whose command= callback fires on every tick of a drag, not
        just on release) always writes the settings dict immediately -- that
        part is cheap -- but coalesces the listbox rebuild itself (a full
        delete+reinsert with a per-item settings comparison) into a single
        refresh shortly after the last call, the same debounce pattern
        already used for window-resize (_on_window_configure/_resize_job)."""
        if getattr(self, '_restoring_batch_item_settings', False):
            return
        item = self._batch_item_for_current_input()
        if item is not None:
            item['settings'] = self._current_settings_dict()
            item['output'] = self.output_path_var.get()
            if debounce_listbox:
                self._schedule_batch_list_refresh()
            else:
                self._refresh_batch_list()

    def _schedule_batch_list_refresh(self) -> None:
        """Coalesce rapid-fire batch-listbox rebuilds (see
        _write_back_current_settings) into one refresh after the calls
        settle."""
        job = getattr(self, '_batch_list_refresh_job', None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._batch_list_refresh_job = self.root.after(
            self._BATCH_LIST_REFRESH_DEBOUNCE_MS, self._refresh_batch_list)

    def _on_bit_depth_toggle(self) -> None:
        """Handle a 10/12-bit radio click: persist the choice on the queue
        entry for the loaded file (so batch runs honor it per item, surviving
        the reload that resets the live toggle) and refresh the info strip."""
        self._write_back_current_settings()
        self._refresh_info_label_text()

    # ── Quality slider ─────────────────────────────────────────────────────────

    def _sync_quality_display(self, *_args) -> None:
        """Keep quality_display_var in sync with whichever backing var is
        active for the current quality mode."""
        if self.quality_mode_var.get() == 'Target Bitrate':
            self.quality_display_var.set(f"{self.bitrate_var.get():,} kbps")
        else:
            self.quality_display_var.set(str(self.quality_var.get()))

    def _batch_settings_tooltip_text(self) -> str:
        """Explains the per-file batch settings model: each queued file has
        its own settings, so this tooltip exists to make that (and the "*"
        marker's meaning) discoverable from the queue panel itself."""
        return (
            "Each queued file remembers its own settings (gamma, quality, "
            "tonemapper, GPU accel, bit depth, Accurate GPU Color).\n\n"
            "Selecting a queued file loads its own settings into the controls "
            "above; changing a control while a file is selected edits that "
            "file's settings only.\n\n"
            "A \"*\" next to a queued file means its settings differ from what "
            "the controls currently show.\n\n"
            "\"Apply to All\" copies the currently-displayed settings onto "
            "every other queued file."
        )

    def _quality_mode_tooltip_text(self) -> str:
        """Built at hover-time (not a fixed string) so the 'This file' line
        can reference the currently loaded source's probed bitrate."""
        text = (
            "Constant Quality: encoder auto-varies bitrate per scene to hit a "
            "quality target. Typical good range: 18-23 (lower = better "
            "quality, bigger file).\n\n"
            "Target Bitrate: you set the average output bitrate directly. "
            "Rule of thumb: 50-70% of the source usually keeps quality close "
            "to unnoticeable while meaningfully shrinking the file."
        )
        props = getattr(self, '_cached_props', None)
        if props and props.get('bit_rate'):
            source_kbps = props['bit_rate'] // 1000
            low, high = round(source_kbps * 0.5), round(source_kbps * 0.7)
            tilde = '~' if props.get('bit_rate_estimated') else ''
            text += (f"\n  This file: source is {tilde}{source_kbps:,} kbps "
                     f"-> try {low:,}-{high:,} kbps.")
        return text

    def _source_bitrate_kbps(self) -> int:
        """The loaded source's probed video bitrate in kbps, or the standard
        8,000 kbps fallback (matches conversion.py's nvenc/qsv zero-bitrate
        guard) when unprobed or reported as 0."""
        props = getattr(self, '_cached_props', None) or {}
        bit_rate = props.get('bit_rate') or 0
        if props.get('bit_rate_estimated') and props.get('audio_bit_rate'):
            # An estimated bit_rate is format.bit_rate -- the whole
            # container's total (video+audio+overhead), not a per-stream
            # video reading. Net out the known audio share so the Target
            # Bitrate ceiling/default reflect the video stream alone rather
            # than inflating both by the audio track's own bitrate.
            bit_rate = max(bit_rate - props['audio_bit_rate'], 0)
        return (bit_rate // 1000) or self._BITRATE_FALLBACK_KBPS

    def _bitrate_ceiling_kbps(self) -> int:
        """Target Bitrate's slider ceiling: the source bitrate rounded to the
        nearest 500 kbps step, never below the floor."""
        source = self._source_bitrate_kbps()
        return max(self._BITRATE_FLOOR_KBPS, round(source / 500) * 500)

    def _apply_bitrate_range(self) -> None:
        """Reconfigure the quality slider for Target Bitrate mode. The range
        (1,000 kbps to the source bitrate) is encoder-agnostic -- unlike
        Constant Quality, it does not depend on the GPU toggle."""
        ceiling = self._bitrate_ceiling_kbps()
        has_file = getattr(self, '_cached_props', None) is not None
        if has_file and self._bitrate_needs_reseed:
            # A new file was just loaded (see _update_info_label): reseed to
            # 50% of its bitrate rather than keeping a value left over from
            # a previous file or session.
            seed = _clamp(round(ceiling * 0.5 / 500) * 500, self._BITRATE_FLOOR_KBPS, ceiling)
            self.bitrate_var.set(seed)
            self._bitrate_needs_reseed = False
        elif not has_file:
            # No file has been probed yet (e.g. this is the startup call in
            # __init__, or the input was cleared): _bitrate_ceiling_kbps() is
            # only the unknown-source fallback here, not a real file's
            # bitrate, so it must not clamp down a real saved choice.
            ceiling = max(ceiling, self.bitrate_var.get())
        value = _clamp(self.bitrate_var.get(), self._BITRATE_FLOOR_KBPS, ceiling)
        self.quality_slider.configure(from_=self._BITRATE_FLOOR_KBPS, to=ceiling)
        # ttk.Scale.set() fires its own -command (_on_quality_change) even for
        # this purely programmatic call, which would otherwise misread the
        # reseed/clamp as a deliberate user drag and wrongly mark this file
        # "customized". Suppress just that side effect for this one call.
        self._applying_bitrate_range = True
        try:
            self.quality_slider.set(value)
        finally:
            self._applying_bitrate_range = False
        self.bitrate_var.set(value)

    def _apply_quality_mode(self) -> None:
        """Reconfigure the shared quality slider for the current quality-mode
        selection. Called when the mode dropdown changes, the GPU toggle
        changes, and a new file is probed."""
        mode = self.quality_mode_var.get()
        switched = getattr(self, '_last_quality_mode_applied', None) != mode
        self._last_quality_mode_applied = mode
        if mode == 'Target Bitrate':
            self._apply_bitrate_range()
        elif switched or getattr(self, '_restoring_batch_item_settings', False):
            # Direct restore: either coming from Target Bitrate's unrelated
            # kbps range (or the first build), or mid-restore of a batch item
            # whose GPU setting may differ from whatever item's range the
            # slider widget currently reflects. In both cases a fractional
            # remap against that (unrelated or stale) range would be
            # meaningless -- restore quality_var's own value directly instead.
            worst, best = self._CQ_RANGE if self.gpu_accel_var.get() else self._CRF_RANGE
            lo, hi = min(worst, best), max(worst, best)
            value = _clamp(self.quality_var.get(), lo, hi)
            self.quality_slider.configure(from_=worst, to=best)
            self.quality_slider.set(value)
            self.quality_var.set(value)
        else:
            self._apply_quality_range()  # unchanged CRF<->CQ knob-preserving remap

    def _apply_quality_range(self) -> None:
        """Set the Quality slider's range for the current CPU/GPU mode."""
        worst, best = self._CQ_RANGE if self.gpu_accel_var.get() else self._CRF_RANGE
        old_from = float(self.quality_slider.cget('from'))
        old_to = float(self.quality_slider.cget('to'))
        if getattr(self, '_restoring_batch_item_settings', False):
            # Mid-restore (see _load_input_file): _restore_settings_dict just
            # set quality_var directly without moving the slider widget, so
            # the widget's position is stale -- trust the var instead. Outside
            # a restore (e.g. a GPU toggle), the widget and quality_var are
            # always in sync, so reading the widget's exact float position is
            # both correct and preserves knob-position precision across
            # repeated toggles (see test_knob_position_held_across_gpu_toggle).
            current = float(self.quality_var.get())
        else:
            current = float(self.quality_slider.get())
        if old_to != old_from:
            fraction = min(max((current - old_from) / (old_to - old_from), 0.0), 1.0)
        else:
            fraction = 0.0
        new_value = worst + fraction * (best - worst)
        self.quality_slider.configure(from_=worst, to=best)
        self.quality_slider.set(new_value)
        self.quality_var.set(int(round(new_value)))

    def _apply_tonemap_choices(self) -> None:
        """Always show all tonemapper entries; the GPU-only ones (BT.2390,
        Spline) get a "(GPU Only)" suffix and are greyed out in the dropdown
        instead of being removed when GPU tonemapping isn't actually active
        (toggle on and the Vulkan/libplacebo probe passed). Resets the
        selection to Mobius if it becomes unavailable while selected."""
        gpu_active = self.gpu_accel_var.get() and vulkan_libplacebo_available()
        display_values = [
            t if gpu_active or not is_gpu_only_tonemapper(t)
            else f"{t}{self._GPU_ONLY_SUFFIX}"
            for t in TONEMAP
        ]
        self.tonemap_combobox.configure(values=display_values)
        if not gpu_active and is_gpu_only_tonemapper(self.tonemap_var.get()):
            self.tonemap_var.set('Mobius')
        self._last_valid_tonemapper = self.tonemap_var.get()

    def _apply_lut_export_availability(self) -> None:
        """Accurate GPU Color (lut_export_var) only affects the GPU/
        libplacebo export path -- CPU exports always apply accurate color
        correction regardless (see the checkbox's tooltip). Grey it out
        whenever GPU acceleration is off, since it wouldn't change anything
        then. Applies uniformly across every tonemapper: libplacebo's own
        gamut handling was found to measurably diverge from the LUT
        reference for tonemappers with a CPU implementation too, not just
        GPU-only ones (see _effective_lut_enabled in preview.py), so the
        tradeoff is real regardless of which tonemapper is selected. Only
        touches the widget's interactive state, never lut_export_var itself,
        so the underlying setting is preserved across GPU toggles,
        tonemapper switches, and batch-item switches, and the checkbox
        always keeps showing it."""
        available = self.gpu_accel_var.get()
        self.lut_export_checkbutton.config(state='normal' if available else 'disabled')

    def _on_lut_export_toggle(self) -> None:
        """Accurate GPU Color checkbox click: persist the choice on the
        queue entry for the loaded file, exactly like every other per-file
        batch setting (see _batch_settings_tooltip_text), then refresh the
        preview to reflect the new effective value."""
        self._write_back_current_settings()
        self.update_frame_preview()

    def _on_tonemap_selected(self, event: tk.Event = None) -> None:  # type: ignore[type-arg]
        """<<ComboboxSelected>> handler. A greyed GPU-only row is still
        clickable in a plain Tk listbox (the grey is cosmetic only), so this
        is the actual guard: any selection carrying the "(GPU Only)" suffix
        is refused and reverted to the last valid selection instead."""
        raw = self.tonemap_var.get()
        if raw.endswith(self._GPU_ONLY_SUFFIX):
            self.tonemap_var.set(self._last_valid_tonemapper)
            return
        self._last_valid_tonemapper = raw
        if hasattr(self, 'lut_export_checkbutton'):
            self._apply_lut_export_availability()
        self._write_back_current_settings()
        self.update_frame_preview(event)

    def _on_quality_change(self, value: str) -> None:
        """Snap the slider to whole steps: CRF/CQ ints in Constant Quality
        mode, or 500 kbps increments in Target Bitrate mode (the scale emits
        floats either way)."""
        if getattr(self, '_applying_bitrate_range', False):
            # This call is _apply_bitrate_range's own programmatic .set(),
            # not a user drag -- it already applies bitrate_var itself and
            # every caller of _apply_bitrate_range already writes settings
            # back explicitly afterward, so there is nothing left to do here.
            return
        if self.quality_mode_var.get() == 'Target Bitrate':
            self.bitrate_var.set(round(float(value) / 500) * 500)
            self._bitrate_customized_for_current_item = True
        else:
            self.quality_var.set(int(float(value)))
        self._write_back_current_settings(debounce_listbox=True)

    def _on_quality_mode_selected(self, event: tk.Event = None) -> None:  # type: ignore[type-arg]
        """<<ComboboxSelected>> handler for the quality-mode dropdown."""
        self._apply_quality_mode()
        self._write_back_current_settings()
        if hasattr(self, 'quality_mode_combobox'):
            self.quality_mode_combobox.selection_clear()

    # ── Info strip ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_info_text(
            properties: dict, maxcll: float | None = None,  # type: ignore[type-arg]
            bit_depth: int = 8, licensed: bool = False) -> str:
        """Format key video metadata as a compact one-line string.

        *bit_depth* is the actual resolved output depth (the live 10/12-bit
        toggle choice above a 10-bit source, or the automatic 8/10-bit choice
        otherwise). Shows "{source}-bit -> {output}-bit" whenever they differ
        so the conversion is visible at a glance, or just "{N}-bit" when they
        match. An unlicensed source capped to 10-bit (rather than a licensed
        user's own toggle choice) gets a "(Pro Only)" suffix.

        Any detected dynamic-metadata format (currently just Dolby Vision) is
        inserted as its own "|"-separated segment between the codec and the
        HDR/SDR tag; sources without one (plain HDR10, SDR) omit the segment
        entirely rather than showing an empty slot.
        """
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
        source_bit_depth = properties.get('bit_depth', 8)
        if source_bit_depth != bit_depth:
            bit_depth_str = f"{source_bit_depth}-bit -> {bit_depth}-bit"
            # Only call out the license as the reason when it's actually the
            # reason: an unlicensed source that got capped to 10-bit. A
            # licensed user's own 10-bit toggle choice (or a 16-bit source
            # still exceeding even Pro's 12-bit ceiling) isn't a license issue.
            if source_bit_depth > 10 and not licensed:
                bit_depth_str += " (Pro Only)"
        else:
            bit_depth_str = f"{bit_depth}-bit"
        format_tags = []
        if properties.get('is_dolby_vision'):
            format_tags.append('Dolby Vision')
        # total_bit_rate (video+audio, container-duration-rounded) is what
        # matches Windows Explorer's Properties -> Details "Total bitrate"
        # exactly -- bit_rate alone (video-only) is reserved for the Target
        # Bitrate slider's ceiling, see _source_bitrate_kbps.
        bit_rate = properties.get('total_bit_rate') or properties.get('bit_rate') or 0
        parts = [f"{w}×{h}", fps_str, codec, *format_tags,
                 f"{hdr_tag}{maxcll_str}", bit_depth_str]
        if bit_rate:
            tilde = '~' if properties.get('bit_rate_estimated') else ''
            parts.append(f"Bitrate: {tilde}{bit_rate // 1000:,} kbps")
        parts.append(f"Audio: {audio}")
        return " | ".join(parts)

    def _update_info_label(self, file_path: str) -> None:
        """Probe file metadata and update the info strip below the output path."""
        if not hasattr(self, 'info_label'):
            return
        props = get_video_properties(file_path)
        self._source_bit_depth = props.get('bit_depth', 8) if props else 8
        self._update_bit_depth_choice()
        self._cached_props = props
        self._cached_maxcll = get_maxcll(file_path) if props else None
        # A newly-loaded file gets its own 50%-of-source Target Bitrate seed,
        # not whatever was left over from a previous file or session.
        self._bitrate_needs_reseed = True
        if hasattr(self, 'quality_slider'):
            self._apply_quality_mode()
        self._refresh_info_label_text()

    def _refresh_info_label_text(self) -> None:
        """Re-render the info strip from the last probe results, without
        re-probing the file -- used after a fresh load and whenever the user
        flips the 10/12-bit toggle, so the shown bit depth stays live."""
        if not hasattr(self, 'info_label'):
            return
        props = getattr(self, '_cached_props', None)
        if props:
            self.info_label.config(
                text=self._build_info_text(
                    props, maxcll=getattr(self, '_cached_maxcll', None),
                    bit_depth=self._selected_bit_depth(),
                    licensed=getattr(self, '_licensed', False)))
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
            if not file_path:
                return
            if self._licensed:
                # Licensed drops route through the queue so single- and
                # multi-file drops behave consistently: the queue is the work
                # list, and dropping onto a populated queue adds to it instead
                # of bypassing it. add_batch_files may already load the file
                # (first-load path); only load explicitly if it didn't.
                self.add_batch_files([file_path])
                if self.input_path_var.get() != file_path:
                    self._load_input_file(file_path)
            else:
                # Batch is Pro -- unlicensed drops keep the plain load.
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
            quality_mode = self._QUALITY_MODE_TO_INTERNAL.get(self.quality_mode_var.get(), 'cq')
            quality = (int(self.bitrate_var.get()) if quality_mode == 'bitrate'
                       else int(self.quality_var.get()))
            bit_depth = self._selected_bit_depth()

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

            logging.info(
                f"Starting conversion - Input: {input_path}, "
                f"Output: {output_path}, Gamma: {gamma}")

            # Only touch drag-and-drop/Cancel once the conversion has actually
            # started -- start_conversion returns False (without raising) when
            # a guard rejects the file (e.g. undetermined duration), and doing
            # this beforehand would leave DnD permanently unregistered with the
            # Cancel button stuck visible with no process behind it.
            started = conversion_manager.start_conversion(
                input_path, output_path, gamma, use_gpu,
                self.progress_var, self.interactable_elements, self,
                self.open_after_conversion_var.get(), self.cancel_button,
                tonemapper=tonemapper, quality=quality, quality_mode=quality_mode,
                bit_depth=bit_depth, licensed=self._licensed,
                lut_enabled=self._effective_lut_enabled(),
            )
            if started:
                if self.drop_target_registered:
                    self.unregister_drop_target()
                self.cancel_button.grid()
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
            self._apply_quality_mode()
        if hasattr(self, 'tonemap_combobox'):
            self._apply_tonemap_choices()
        if hasattr(self, 'lut_export_checkbutton'):
            self._apply_lut_export_availability()
        self._write_back_current_settings()
        self.update_frame_preview()

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
