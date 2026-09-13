"""Preview frame extraction, rendering, caching, and resize handling."""
from __future__ import annotations

import logging
import math
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

from utils import (
    extract_frame,
    extract_frame_with_conversion,
    extract_frame_with_gpu_conversion,
    get_video_properties,
    clear_hdr_metadata_cache,
    extract_frames_batch,
    extract_frames_with_conversion_batch,
    extract_frames_with_gpu_conversion_batch,
    is_gpu_only_tonemapper,
    vulkan_libplacebo_available,
)

# ── Module-level constants ─────────────────────────────────────────────────────

DEFAULT_MIN_SIZE = (550, 150)
PREVIEW_SIZE = (3840, 2160)

_PREVIEW_POOL_WORKERS = max(1, (os.cpu_count() or 1) // 4)

INITIAL_PANE_SIZE = (640, 360)
_MIN_PANE_W = 240
_RESIZE_DEBOUNCE_MS = 60
_PREVIEW_WIDTH_RESERVE = 160
_PREVIEW_HEIGHT_RESERVE = 130
_MIN_SIZE_MARGIN = (16, 16)
_INITIAL_WIDTH_STRETCH = 400
_GAMMA_MIN = 0.1
_GAMMA_MAX = 3.0
_MIB = 1024 ** 2
_GIB = 1024 ** 3
_NORMAL_MEMORY_THRESHOLD = 2 * _GIB
_LOW_MEMORY_THRESHOLD = 512 * _MIB


def _available_memory_bytes() -> int | None:
    """Return available physical memory on Windows, or None when unavailable."""
    if os.name != 'nt':
        return None
    try:
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
    except (AttributeError, OSError):
        pass
    return None


# ── _HDRPreviewMixin ───────────────────────────────────────────────────────────

class _HDRPreviewMixin:
    """Mixin that provides all preview-related methods for HDRConverterGUI.

    Attributes accessed via ``self`` are provided by HDRConverterGUI.__init__;
    they are declared below inside ``if TYPE_CHECKING:`` so that static
    analysis tools can resolve them without creating runtime instance variables
    on the mixin itself.
    """

    if TYPE_CHECKING:
        from tkinterdnd2 import TkinterDnD
        root: TkinterDnD.Tk
        original_image: Image.Image | None
        converted_image_base: Image.Image | None
        _converted_preview_base: Image.Image | None
        _preview_render_size: tuple[int, int] | None
        original_image_label: ttk.Label
        converted_image_label: ttk.Label
        original_title_label: ttk.Label
        converted_title_label: ttk.Label
        button_container: ttk.Frame
        button_frame: ttk.Frame
        loading_frame: ttk.Frame
        loading_bar: ttk.Progressbar
        progress_bar: ttk.Progressbar
        image_frame: ttk.Frame
        control_frame: ttk.Frame
        action_frame: ttk.Frame
        batch_frame: ttk.LabelFrame
        error_label: ttk.Label
        display_image_var: tk.BooleanVar
        input_path_var: tk.StringVar
        gamma_var: tk.DoubleVar
        tonemap_var: tk.StringVar
        lut_export_var: tk.BooleanVar
        tonemap_combobox: ttk.Combobox
        custom_time_var: tk.StringVar
        custom_time_position: float | None
        gamma_slider: ttk.Scale
        quality_slider: ttk.Scale
        open_after_conversion_checkbutton: ttk.Checkbutton
        convert_button: ttk.Button
        cancel_button: ttk.Button
        _preview_generation: int
        _preview_file_generation: int
        _preview_pool: ThreadPoolExecutor
        _preview_thread: Future | None
        _preview_processes: dict[int, set]
        _preview_futures: dict[int, set[Future]]
        _preview_cache_original: dict[tuple[str, float], Image.Image]
        _preview_cache_converted: dict[tuple[str, float, str, bool, bool], Image.Image]
        _active_preview_cache_position: tuple[str, float] | None
        _last_displayed_preview_key: tuple[str, float, str, bool, bool] | None
        _preview_extraction_size: tuple[int, int]
        _preview_cache_budget_bytes: int
        _preview_memory_pressure: bool
        _preview_prewarm_enabled: bool
        gpu_accel_var: tk.BooleanVar
        _cache_lock: threading.Lock
        current_frame_index: int
        total_frames: int
        last_time_position: float | None
        _duration_path: str | None
        _duration_value: float | None
        _resize_job: str | None
        _window_auto_fitted: bool
        _min_window_size: tuple[int, int]
        frame_buttons: list[ttk.Button]

    # ── Gamma ──────────────────────────────────────────────────────────────────

    def adjust_gamma(self, image: Image.Image, gamma: float) -> Image.Image:
        """Adjust gamma of a PIL.Image."""
        # gamma == 1.0 is the identity transform; skip the per-pixel LUT pass.
        if abs(gamma - 1.0) < 1e-6:
            return image
        inv_gamma = 1.0 / gamma
        lut = [pow(i / 255.0, inv_gamma) * 255 for i in range(256)]
        lut = lut * len(image.getbands())
        lut = [int(round(v)) for v in lut]
        return image.point(lut)

    def _normalized_gamma(self) -> float | None:
        """Return a finite, slider-range gamma or restore the last valid value."""
        fallback = getattr(self, '_last_valid_gamma', 1.0)
        try:
            gamma = float(self.gamma_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.gamma_var.set(fallback)
            return None
        if not math.isfinite(gamma):
            self.gamma_var.set(fallback)
            return None
        gamma = min(max(gamma, _GAMMA_MIN), _GAMMA_MAX)
        if gamma != self.gamma_var.get():
            self.gamma_var.set(gamma)
        self._last_valid_gamma = gamma
        return gamma

    def _apply_gamma_to_preview(self) -> None:
        """Apply the current gamma to the cached display-sized SDR frame.

        Runs on every gamma-slider tick; cheap (one PIL point() pass on a
        ~960x540 image, no extraction, no window resize).
        """
        base = self._converted_preview_base
        if base is None:
            return
        adjusted = self.adjust_gamma(base, self.gamma_var.get())
        converted_photo = ImageTk.PhotoImage(adjusted)
        self.converted_image_label.config(image=converted_photo)
        self._keep_image_ref(self.converted_image_label, converted_photo)

    # ── Window / min-size ──────────────────────────────────────────────────────

    def _compute_min_window_size(self) -> tuple[int, int]:
        """Smallest window that keeps every control visible (the preview may shrink).

        Derived from the chrome that must never be clipped -- the controls, the
        batch queue, the action buttons, and the parts of the preview pane that
        aren't the preview images themselves (titles, the frame-jump buttons,
        the custom seek box, the progress bar). The title/button/seek widgets
        live alongside the (deliberately shrinkable) preview images; the
        progress bar sits pinned in its own row below them. All are measured
        individually rather than via the whole image_frame. Falls back to
        ``DEFAULT_MIN_SIZE`` on bare/mocked instances.
        """
        try:
            self.root.update_idletasks()
            stacked = (self.control_frame, self.batch_frame, self.action_frame)
            widths = [f.winfo_reqwidth() for f in stacked]
            heights = [f.winfo_reqheight() for f in stacked]
            title_w = (self.original_title_label.winfo_reqwidth()
                       + self.converted_title_label.winfo_reqwidth())
            title_h = max(self.original_title_label.winfo_reqheight(),
                          self.converted_title_label.winfo_reqheight())
            preview_chrome_h = (title_h
                                 + self.button_container.winfo_reqheight()
                                 + self.button_frame.winfo_reqheight()
                                 + self.progress_bar.winfo_reqheight())
            widths.append(self.button_container.winfo_reqwidth())
            widths.append(title_w)
        except Exception:
            return DEFAULT_MIN_SIZE
        if not all(isinstance(v, int) for v in widths + heights + [preview_chrome_h]):
            return DEFAULT_MIN_SIZE
        margin_w, margin_h = _MIN_SIZE_MARGIN
        min_w = max(widths) + margin_w
        min_h = sum(heights) + preview_chrome_h + margin_h
        return (max(min_w, DEFAULT_MIN_SIZE[0]), max(min_h, DEFAULT_MIN_SIZE[1]))

    def _apply_min_window_size(self) -> None:
        """Apply the computed minimum window size (``DEFAULT_MIN_SIZE`` pre-layout)."""
        self.root.minsize(*getattr(self, '_min_window_size', DEFAULT_MIN_SIZE))

    def _apply_initial_window_geometry(self) -> None:
        """Open the window wider than the strict minimum, for a comfortable first look.

        The computed minimum (see ``_compute_min_window_size``) is the smallest
        size that keeps every control visible, not a pleasant starting size --
        at that width the window looks cramped. Called once, at startup, so it
        never fights a size the user has since chosen themselves.
        """
        min_w, min_h = getattr(self, '_min_window_size', DEFAULT_MIN_SIZE)
        self.root.geometry(f"{min_w + _INITIAL_WIDTH_STRETCH}x{min_h}")

    def adjust_window_size(self) -> None:
        """Fit the window to the previews on first reveal; keep minsize small.

        Only shrink-wraps once, on the first preview, so re-rendering a later
        frame never yanks a window the user has since resized.
        """
        self._apply_min_window_size()
        if getattr(self, '_window_auto_fitted', False):
            return

        if self.root.wm_state() == 'zoomed':
            self._window_auto_fitted = True
            self.root.update_idletasks()
            self._rescale_preview_to_window()
            return

        self.root.geometry("")
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        if self.root.winfo_width() > screen_width or self.root.winfo_height() > screen_height:
            self.resize_images(screen_width - 100, screen_height - 100)
            self.root.geometry("")
            self.root.update_idletasks()
        # Re-pin the settled size explicitly: geometry("") leaves Tk's auto-sizing
        # in effect, so a later loading spinner would shrink/regrow the window.
        self.root.geometry(f"{self.root.winfo_width()}x{self.root.winfo_height()}")
        self._window_auto_fitted = True

    # ── Preview sizing ─────────────────────────────────────────────────────────

    def _preview_source_size(self) -> tuple[int, int]:
        """The real aspect ratio to fit preview panes to, taken from the most
        recently extracted frame. Falls back to PREVIEW_SIZE's 16:9 box
        before any frame has ever been extracted (original_image is None)."""
        original = getattr(self, 'original_image', None)
        if original is not None:
            return original.size
        return PREVIEW_SIZE

    @staticmethod
    def _fit_preview_pane(
        available_width: float, available_height: float, src_size: tuple[int, int]
    ) -> tuple[int, int]:
        """Size one preview pane to fit a box at src_size's aspect ratio,
        never upscaling past src_size."""
        src_w, src_h = src_size
        w = min(src_w, max(_MIN_PANE_W, int(available_width)))
        h = round(w * src_h / src_w)
        if available_height and h > available_height:
            h = int(available_height)
            w = round(h * src_w / src_h)
        return (max(1, int(w)), max(1, int(h)))

    def _preview_target_size(self) -> tuple[int, int]:
        """Per-pane preview size derived from the live image-frame geometry."""
        if not hasattr(self, 'image_frame'):
            return PREVIEW_SIZE
        frame_w = self.image_frame.winfo_width()
        frame_h = self.image_frame.winfo_height()
        if not isinstance(frame_w, int) or frame_w <= 1:
            return PREVIEW_SIZE
        avail_w = (frame_w - _PREVIEW_WIDTH_RESERVE) / 2
        avail_h = max(0, frame_h - _PREVIEW_HEIGHT_RESERVE) if frame_h > 1 else 0
        return self._fit_preview_pane(avail_w, avail_h, self._preview_source_size())

    def _initial_preview_size(self) -> tuple[int, int]:
        """Per-pane size for the very first preview, before the window auto-fits."""
        src_size = self._preview_source_size()
        pane_w = INITIAL_PANE_SIZE[0]
        try:
            screen_w = self.root.winfo_screenwidth()
        except Exception:
            return self._fit_preview_pane(pane_w, 0, src_size)
        if isinstance(screen_w, int):
            max_pane_w = (screen_w - 100 - _PREVIEW_WIDTH_RESERVE) // 2
            if max_pane_w > 0:
                pane_w = min(pane_w, max_pane_w)
        return self._fit_preview_pane(pane_w, 0, src_size)

    # ── Rendering helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _keep_image_ref(label: ttk.Label, photo: ImageTk.PhotoImage) -> None:
        """Pin a PhotoImage to its label so Tk doesn't garbage-collect it."""
        setattr(label, 'image', photo)

    def _render_preview_at_size(self, size: tuple[int, int]) -> None:
        """(Re)render both panes at ``size``; SDR pane keeps the live gamma."""
        original = getattr(self, 'original_image', None)
        if original is None:
            return
        self._preview_render_size = size
        original_resized = original.resize(size, Image.Resampling.LANCZOS)
        original_photo = ImageTk.PhotoImage(original_resized)
        self.original_image_label.config(image=original_photo)
        self._keep_image_ref(self.original_image_label, original_photo)
        if self.converted_image_base is not None:
            self._converted_preview_base = self.converted_image_base.resize(
                size, Image.Resampling.LANCZOS)
            self._apply_gamma_to_preview()

    def _on_window_configure(self, event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        """Coalesce live resize events into a single debounced preview rescale."""
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, 'original_image', None) is None:
            return
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(_RESIZE_DEBOUNCE_MS, self._rescale_preview_to_window)

    def _rescale_preview_to_window(self) -> None:
        """Re-render the previews at the size that fits the settled window."""
        self._resize_job = None
        if getattr(self, 'original_image', None) is None:
            return
        # Flush any pending geometry recompute (e.g. mid native drag) so
        # winfo_width()/height() below reflect the settled layout.
        self.root.update_idletasks()
        if self.loading_frame.winfo_ismapped():
            # A new file is still being extracted -- skip rather than cache a
            # bad size; _render_preview_images measures fresh once it lands.
            return
        self._render_preview_at_size(self._preview_target_size())

    def resize_images(self, max_width: int, max_height: int) -> None:
        """Resize both preview panes to fit within max_width x max_height,
        preserving the real source aspect ratio. Width is halved -- the two
        panes sit side by side, sharing the width -- but height is not: both
        panes share the full height rather than splitting it vertically.
        Delegates to _render_preview_at_size so
        _converted_preview_base/_preview_render_size stay in sync --
        otherwise a later gamma-slider tick (_apply_gamma_to_preview) would
        reapply gamma to a stale, pre-resize base."""
        size = self._fit_preview_pane(
            max_width / 2, max_height, self._preview_source_size())
        self._render_preview_at_size(size)

    def clear_preview(self) -> None:
        """Clear the frame preview images and reset cached images."""
        self.original_image_label.config(image='')
        self.converted_image_label.config(image='')
        self.original_image = None
        self.converted_image_base = None
        self._converted_preview_base = None
        self._apply_min_window_size()

    def arrange_widgets(self, image_frame: bool) -> None:
        """Arrange the widgets in the appropriate frames.

        progress_bar is not repositioned here -- it's pinned directly to
        image_frame at construction time (see create_widgets/configure_grid)
        and never moves, regardless of whether the preview titles/images are
        showing."""
        if image_frame:
            self.button_frame.grid(row=2, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
        else:
            self.button_frame.grid(row=5, column=0, columnspan=3, pady=(5, 0), sticky=tk.N)
        self.open_after_conversion_checkbutton.grid(row=1, column=0, padx=(5, 5), sticky=tk.N)
        self.convert_button.grid(row=1, column=1, padx=(5, 5), pady=(0, 10), sticky=tk.N)

    def handle_preview_error(self, error: Exception) -> None:
        """Handle errors that occur during frame preview update."""
        self.error_label.config(text=f"Error displaying image: {error}")
        self._hide_preview_loading()
        self.clear_preview()
        self.original_title_label.grid_remove()
        self.converted_title_label.grid_remove()
        self.button_container.grid_remove()

    # ── Loading spinner ────────────────────────────────────────────────────────

    def _show_preview_loading(self) -> None:
        """Show the loading spinner and hide the preview until frames are ready."""
        # Invalidate so the eventual render measures fresh, not a size
        # cached under unreliable conditions mid-load.
        self._preview_render_size = None
        self.original_title_label.grid_remove()
        self.converted_title_label.grid_remove()
        self.button_container.grid_remove()
        self.original_image_label.grid_remove()
        self.converted_image_label.grid_remove()
        self.loading_frame.grid()
        self.loading_bar.start(12)

    def _hide_preview_loading(self) -> None:
        """Stop and hide the loading spinner."""
        self.loading_bar.stop()
        self.loading_frame.grid_remove()
        if hasattr(self, 'root'):
            self._apply_min_window_size()

    def _reveal_preview(self) -> None:
        """Reveal the titles, frame buttons and images once frames have rendered."""
        self.original_image_label.grid()
        self.converted_image_label.grid()
        self.original_title_label.grid()
        self.converted_title_label.grid()
        self.button_container.grid()

    # ── Gamma slider ───────────────────────────────────────────────────────────

    def on_gamma_change(self, event: object = None) -> None:
        """Handle gamma slider/entry changes."""
        if self._normalized_gamma() is None:
            return
        self._write_back_current_settings(debounce_listbox=True)  # type: ignore[attr-defined]
        if self.display_image_var.get() and self._converted_preview_base is not None:
            self._apply_gamma_to_preview()
        else:
            self.update_frame_preview()

    # ── Slider jump-to-click ───────────────────────────────────────────────────

    def _jump_slider_to_click(self, slider: ttk.Scale, event: tk.Event,  # type: ignore[type-arg]
                              snap: bool = False) -> str | None:
        """Move a ttk.Scale's knob straight to a trough click instead of nudging."""
        if 'slider' in slider.identify(event.x, event.y):
            return None
        width = slider.winfo_width()
        if width <= 0:
            return None
        fraction = min(max(event.x / width, 0.0), 1.0)
        low = float(slider.cget('from'))
        high = float(slider.cget('to'))
        value = low + fraction * (high - low)
        if snap:
            value = round(value)
        slider.set(value)
        return 'break'

    def _gamma_slider_jump(self, event: tk.Event) -> str | None:  # type: ignore[type-arg]
        """Jump the gamma knob to a trough click (continuous, no snapping)."""
        return self._jump_slider_to_click(self.gamma_slider, event)

    def _quality_slider_jump(self, event: tk.Event) -> str | None:  # type: ignore[type-arg]
        """Snap the quality knob to the nearest whole CRF/CQ step at a click."""
        return self._jump_slider_to_click(self.quality_slider, event, snap=True)

    # ── Frame buttons ──────────────────────────────────────────────────────────

    def on_frame_button_click(self, index: int) -> None:
        """Handle frame button clicks to update the displayed frames."""
        was_custom_seek = getattr(self, 'custom_time_position', None) is not None
        if was_custom_seek or index != getattr(self, 'current_frame_index', None):
            self._discard_previous_preview_cache(drop_all=was_custom_seek)
        self.current_frame_index = index
        self.custom_time_position = None
        self.original_image = None
        self.converted_image_base = None
        self.highlight_frame_button(index)
        self.update_frame_preview()

    def highlight_frame_button(self, index: int) -> None:
        """Highlight the selected frame button and reset others."""
        for i, btn in enumerate(self.frame_buttons, start=1):
            if i == index:
                btn.configure(style='Selected.TButton')
            else:
                btn.configure(style='TButton')

    # ── Custom seek ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_timestamp(text: str) -> float:
        """Parse 'HH:MM:SS', 'MM:SS', or 'SS' (fractions allowed) into seconds."""
        text = text.strip()
        if not text:
            raise ValueError("empty timestamp")
        parts = text.split(':')
        if len(parts) > 3:
            raise ValueError("too many ':' separators")
        seconds = 0.0
        for part in parts:
            value = float(part)
            if value < 0:
                raise ValueError("negative time component")
            seconds = seconds * 60 + value
        return seconds

    def _preview_time_position(self, duration: float) -> float:
        """Return the preview's seek position, honoring an active custom seek."""
        custom = getattr(self, 'custom_time_position', None)
        if custom is not None:
            return max(0.0, min(custom, duration))
        return (self.current_frame_index / (self.total_frames + 1)) * duration

    def on_custom_seek(self, event: object = None) -> None:
        """Preview the timestamp typed in the custom-seek entry."""
        try:
            seconds = self._parse_timestamp(self.custom_time_var.get())
        except ValueError:
            self.error_label.config(text="Invalid time. Use HH:MM:SS, MM:SS, or seconds.")
            return
        self.error_label.config(text="")
        previous_custom_time = getattr(self, 'custom_time_position', None)
        if previous_custom_time is not None and previous_custom_time != seconds:
            self._discard_previous_preview_cache(drop_all=True)
        elif previous_custom_time is None:
            self._discard_previous_preview_cache(drop_all=False)
        self.custom_time_position = seconds
        self.original_image = None
        self.converted_image_base = None
        self.highlight_frame_button(0)
        self.update_frame_preview()

    def _reset_custom_seek(self) -> None:
        """Clear any active custom seek so a newly loaded file starts on frame 1."""
        self.custom_time_position = None
        self.custom_time_var.set('')

    # ── Preview cache ──────────────────────────────────────────────────────────

    def _reset_preview_cache(self) -> None:
        """Drop all cached preview frames (e.g. when a new file is loaded)."""
        file_generation = getattr(self, '_preview_file_generation', 0)
        for future in getattr(self, '_preview_futures', {}).pop(file_generation, set()):
            future.cancel()
        for process in getattr(self, '_preview_processes', {}).pop(file_generation, set()):
            try:
                process.terminate()
            except OSError:
                pass
        self._preview_file_generation = file_generation + 1
        for cache in (getattr(self, '_preview_cache_original', {}),
                      getattr(self, '_preview_cache_converted', {})):
            for image in cache.values():
                self._close_preview_image(image)
        self._preview_cache_original = {}
        self._preview_cache_converted = {}
        self._active_preview_cache_position = None
        self._last_displayed_preview_key = None
        self._configure_preview_memory()
        clear_hdr_metadata_cache()

    def _register_preview_process(self, file_generation: int, process: object) -> None:
        """Associate a preview process with its file until that file is discarded."""
        if file_generation != getattr(self, '_preview_file_generation', 0):
            try:
                process.terminate()  # type: ignore[attr-defined]
            except OSError:
                pass
            return
        if not hasattr(self, '_preview_processes'):
            self._preview_processes = {}
        self._preview_processes.setdefault(file_generation, set()).add(process)

    def _submit_preview_task(self, file_generation: int, task: Callable, *args: object) -> Future:
        """Submit preview work and retain its future for file-replacement cancellation."""
        if not hasattr(self, '_preview_pool'):
            self._preview_pool = ThreadPoolExecutor(
                max_workers=_PREVIEW_POOL_WORKERS, thread_name_prefix='frame-fetch')
        future = self._preview_pool.submit(task, *args)
        if not hasattr(self, '_preview_futures'):
            self._preview_futures = {}
        self._preview_futures.setdefault(file_generation, set()).add(future)
        return future

    def _cache_store(self, cache: dict, key: object, value: Image.Image) -> None:
        """Insert into a preview cache and discard byte-budget overflow safely."""
        if not hasattr(self, '_cache_lock'):
            self._cache_lock = threading.Lock()
        with self._cache_lock:
            cache[key] = value
            discarded = self._trim_preview_cache_locked()
        for image in discarded:
            self._close_preview_image(image)

    def _configure_preview_memory(self) -> None:
        """Snapshot a conservative preview profile for the selected file."""
        available = _available_memory_bytes()
        if available is None or available >= _NORMAL_MEMORY_THRESHOLD:
            size, budget, pressure, prewarm = PREVIEW_SIZE, 256 * _MIB, False, True
        elif available >= _LOW_MEMORY_THRESHOLD:
            size, budget, pressure, prewarm = (1920, 1080), 64 * _MIB, False, True
        else:
            size, budget, pressure, prewarm = (1280, 720), 32 * _MIB, True, False
        self._preview_extraction_size = size
        self._preview_cache_budget_bytes = budget
        self._preview_memory_pressure = pressure
        self._preview_prewarm_enabled = prewarm

    def _preview_extraction_dimensions(self) -> tuple[int, int]:
        """Return the file's memory-aware preview extraction dimensions."""
        return getattr(self, '_preview_extraction_size', PREVIEW_SIZE)

    @staticmethod
    def _estimated_image_bytes(image: object) -> int:
        """Estimate decoded image bytes without creating a second pixel buffer."""
        width = getattr(image, 'width', None)
        height = getattr(image, 'height', None)
        getbands = getattr(image, 'getbands', None)
        if not isinstance(width, int) or not isinstance(height, int) or not callable(getbands):
            return 0
        bands = getbands()
        return width * height * len(bands) if isinstance(bands, tuple) else 0

    def _cache_bytes_locked(self) -> int:
        return sum(self._estimated_image_bytes(image)
                   for cache in (self._preview_cache_original, self._preview_cache_converted)
                   for image in cache.values())

    def _converted_key_is_protected(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) < 2:
            return False
        position = key[:2]
        if position == getattr(self, '_active_preview_cache_position', None):
            return True
        displayed = getattr(self, '_last_displayed_preview_key', None)
        if displayed is not None and position == displayed[:2]:
            return key == displayed
        return next((candidate for candidate in self._preview_cache_converted
                     if candidate[:2] == position), None) == key

    def _trim_preview_cache_locked(self) -> list[object]:
        """Return cache entries removable without violating the active preview policy."""
        budget = getattr(self, '_preview_cache_budget_bytes', 256 * _MIB)
        if self._cache_bytes_locked() <= budget:
            return []

        discarded: list[object] = []
        for key in list(self._preview_cache_converted):
            if not self._converted_key_is_protected(key):
                discarded.append(self._preview_cache_converted.pop(key))
                if self._cache_bytes_locked() <= budget:
                    return discarded

        if not getattr(self, '_preview_memory_pressure', False):
            return discarded

        displayed = getattr(self, '_last_displayed_preview_key', None)
        displayed_position = displayed[:2] if displayed is not None else None
        for key in list(self._preview_cache_converted):
            if key == displayed:
                continue
            discarded.append(self._preview_cache_converted.pop(key))
            if self._cache_bytes_locked() <= budget:
                return discarded
        for key in list(self._preview_cache_original):
            if key == displayed_position:
                continue
            discarded.append(self._preview_cache_original.pop(key))
            if self._cache_bytes_locked() <= budget:
                return discarded
        return discarded

    @staticmethod
    def _close_preview_image(image: object) -> None:
        """Release a discarded Pillow image when it owns external resources."""
        close = getattr(image, 'close', None)
        if callable(close):
            close()

    def _discard_previous_preview_cache(self, drop_all: bool) -> None:
        """Retain one inactive preset image, or discard a replaced custom seek."""
        path_var = getattr(self, 'input_path_var', None)
        video_path = path_var.get() if path_var is not None else ''
        time_position = getattr(self, 'last_time_position', None)
        if not video_path or time_position is None:
            return

        time_key = round(time_position, 3)
        keep_key = None if drop_all else getattr(self, '_last_displayed_preview_key', None)
        if (not isinstance(keep_key, tuple) or len(keep_key) < 2
                or keep_key[:2] != (video_path, time_key)):
            keep_key = None
        if not hasattr(self, '_cache_lock'):
            self._cache_lock = threading.Lock()

        discarded: list[object] = []
        with self._cache_lock:
            if drop_all:
                original = self._preview_cache_original.pop((video_path, time_key), None)
                if original is not None:
                    discarded.append(original)
            for key in list(self._preview_cache_converted):
                if key[:2] == (video_path, time_key) and key != keep_key:
                    discarded.append(self._preview_cache_converted.pop(key))
        for image in discarded:
            self._close_preview_image(image)

    def _store_prewarmed_converted(
        self, key: tuple[str, float, str, bool, bool], image: Image.Image,
    ) -> None:
        """Keep one fallback image for inactive presets and all variants for active one."""
        if not hasattr(self, '_cache_lock'):
            self._cache_lock = threading.Lock()
        with self._cache_lock:
            inactive = getattr(self, '_active_preview_cache_position', None) != key[:2]
            has_fallback = inactive and any(
                existing_key[:2] == key[:2]
                for existing_key in self._preview_cache_converted)
            if not has_fallback:
                self._preview_cache_converted[key] = image
                discarded = self._trim_preview_cache_locked()
            else:
                discarded = []
        if has_fallback:
            self._close_preview_image(image)
        for discarded_image in discarded:
            self._close_preview_image(discarded_image)

    def _effective_lut_enabled(self) -> bool:
        """The lut_enabled value actually used by preview/export: simply
        lut_export_var's raw checked state, applied uniformly regardless of
        tonemapper.

        This used to force the LUT off for tonemappers with a native CPU/
        zscale implementation (Reinhard/Mobius/Hable), on the assumption that
        libplacebo's own gamut handling already matched the LUT reference for
        them. That assumption was disproven empirically: real HDR10 content
        run through libplacebo with a CPU-capable tonemapper (Hable) showed a
        ~61/255 max pixel divergence between the LUT stage on vs. off -- the
        same class of defect the LUT was originally added to fix for
        GPU-only tonemappers (see the gpu-lut-libplacebo-native-broken
        investigation, and TestGpuLutForcedOffMatchesLutOnForCpuCapableTonemapper
        in test/smoke_test.py). The tonemapper played has no bearing on
        whether libplacebo's gamut conversion is accurate, so the checkbox
        now controls the tradeoff for every tonemapper, matching
        _apply_lut_export_availability's own tonemapper-independent gating.

        getattr-guarded: lut_export_var is set in HDRConverterGUI.__init__,
        but bare test doubles (object.__new__) may not have it -- default to
        the same True the real BooleanVar always starts at.

        When GPU acceleration is off, the checkbox's raw state is forced to
        True regardless of what it's actually set to: construct_ffmpeg_command's
        CPU branch never reads lut_enabled at all (CPU export always applies
        the LUT), but gui.py's _apply_lut_export_availability only greys the
        checkbox out when GPU is off -- it never resets lut_export_var. Left
        unchecked from an earlier GPU session, the stale False would otherwise
        make this CPU-routed preview show the no-LUT legacy filter while real
        CPU export always includes the LUT.
        """
        lut_export_var = getattr(self, 'lut_export_var', None)
        if lut_export_var is None:
            return True
        gpu_accel_var = getattr(self, 'gpu_accel_var', None)
        if gpu_accel_var is not None and not gpu_accel_var.get():
            return True
        return lut_export_var.get()

    def _gpu_tonemap_active(self) -> bool:
        """True when a real export would run GPU (libplacebo) tonemapping --
        mirrors gui.py's _apply_tonemap_choices ``gpu_active`` formula, so
        preview extraction picks the same path for every tonemapper (not just
        the GPU-only ones) that real export will actually use.

        Before this, preview always ran the CPU zscale tonemap for
        Reinhard/Mobius/Hable regardless of the GPU accel toggle -- only the
        LUT stage differed between "Accurate GPU Color" on/off, so toggling
        it never actually exercised libplacebo's own tonemap algorithm, which
        can render visibly differently from zscale's for the same named
        curve.

        getattr-guarded like _effective_lut_enabled: bare test doubles may
        not set gpu_accel_var.
        """
        gpu_accel_var = getattr(self, 'gpu_accel_var', None)
        if gpu_accel_var is None or not gpu_accel_var.get():
            return False
        return vulkan_libplacebo_available()

    def _use_gpu_extraction(self, tonemapper: str) -> bool:
        """Whether preview should extract this tonemapper's converted frame via
        the GPU (libplacebo) path rather than CPU zscale. Unconditional for
        GPU-only tonemappers (BT.2390/Spline have no CPU implementation at
        all); otherwise follows _gpu_tonemap_active so CPU-capable
        tonemappers match real export's choice too."""
        return is_gpu_only_tonemapper(tonemapper) or self._gpu_tonemap_active()

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
        lut_enabled = self._effective_lut_enabled()
        use_gpu = self._use_gpu_extraction(tonemapper)
        return (
            (video_path, time_key) in self._preview_cache_original
            and (video_path, time_key, tonemapper, lut_enabled, use_gpu) in self._preview_cache_converted
        )

    # ── Frame extraction ───────────────────────────────────────────────────────

    def _get_duration(self, video_path: str) -> float:
        """Return the video duration, probing ffprobe only once per file."""
        if (getattr(self, '_duration_path', None) == video_path
                and getattr(self, '_duration_value', None)):
            return self._duration_value  # type: ignore[return-value]
        properties = get_video_properties(video_path)
        if not properties or not properties.get('duration'):
            raise ValueError("Failed to retrieve video properties.")
        self._duration_path = video_path
        self._duration_value = properties['duration']
        return self._duration_value  # type: ignore[return-value]

    def _schedule_on_main(self, callback: Callable[[], object]) -> None:
        """Run a callback on the Tk main thread, tolerating shutdown races."""
        try:
            self.root.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def _extract_preview_images(
        self,
        video_path: str,
        time_position: float,
        tonemapper: str,
        lut_enabled: bool = True,
        process_started: Callable[[object], None] | None = None,
    ) -> tuple[Image.Image, Image.Image]:
        """Return (original, converted) preview frames, caching ffmpeg results.

        lut_enabled: mirrors gui.py's permanent "Accurate GPU Color" export
        setting (lut_export_var), so the preview shows what real export will
        actually produce. Controls only this SDR ("converted") frame; the HDR
        original is never affected.
        """
        if not hasattr(self, '_preview_cache_original'):
            self._preview_cache_original = {}
            self._preview_cache_converted = {}

        time_key = round(time_position, 3)
        preview_width, preview_height = self._preview_extraction_dimensions()
        original_key = (video_path, time_key)
        original = self._preview_cache_original.get(original_key)
        if original is None:
            original = extract_frame(video_path, time_position=time_position,
                                     width=preview_width, height=preview_height,
                                     process_started=process_started)
            self._cache_store(self._preview_cache_original, original_key, original)

        use_gpu = self._use_gpu_extraction(tonemapper)
        converted_key = (video_path, time_key, tonemapper, lut_enabled, use_gpu)
        converted = self._preview_cache_converted.get(converted_key)
        if converted is None:
            extract_fn = (extract_frame_with_gpu_conversion
                          if use_gpu
                          else extract_frame_with_conversion)
            converted = extract_fn(
                video_path, gamma=1.0,
                tonemapper=tonemapper, time_position=time_position,
                width=preview_width, height=preview_height,
                lut_enabled=lut_enabled, process_started=process_started,
            )
            self._cache_store(self._preview_cache_converted, converted_key, converted)
        return original, converted

    def _prewarm_batch_originals(
        self, video_path: str, positions: list[float], generation: int,
        file_generation: int | None = None,
    ) -> None:
        """Extract all original (HDR) frames for the given positions in one ffmpeg pass."""
        if file_generation is None:
            file_generation = getattr(self, '_preview_file_generation', 0)
        assert file_generation is not None
        if file_generation != getattr(self, '_preview_file_generation', 0):
            return
        try:
            preview_width, preview_height = self._preview_extraction_dimensions()
            originals = extract_frames_batch(
                video_path, positions, preview_width, preview_height,
                process_started=lambda process: self._register_preview_process(file_generation, process))
            for t, img in zip(positions, originals):
                self._cache_store(
                    self._preview_cache_original, (video_path, round(t, 3)), img)
        except Exception:
            logging.exception('preview batch original pre-warm failed')

    def _prewarm_batch_converted(
        self, video_path: str, positions: list[float], tonemapper: str, generation: int,
        file_generation: int | None = None, lut_enabled: bool = True,
    ) -> None:
        """Tonemap-convert all frames for the given positions in one ffmpeg pass
        (or, for the GPU path, N looped GPU passes -- see
        extract_frames_with_gpu_conversion_batch).

        lut_enabled and the GPU/CPU choice (_use_gpu_extraction) must match
        _extract_preview_images's real lookup key exactly, or prewarmed
        entries become unreachable (or reachable under the wrong key) from
        the real lookup path.
        """
        if file_generation is None:
            file_generation = getattr(self, '_preview_file_generation', 0)
        assert file_generation is not None
        if file_generation != getattr(self, '_preview_file_generation', 0):
            return
        try:
            preview_width, preview_height = self._preview_extraction_dimensions()
            use_gpu = self._use_gpu_extraction(tonemapper)
            if use_gpu:
                converted = extract_frames_with_gpu_conversion_batch(
                    video_path, positions, 1.0, tonemapper,
                    preview_width, preview_height, lut_enabled=lut_enabled,
                    process_started=lambda process: self._register_preview_process(file_generation, process))
            else:
                converted = extract_frames_with_conversion_batch(
                    video_path, positions, 1.0, tonemapper,
                    preview_width, preview_height, lut_enabled=lut_enabled,
                    process_started=lambda process: self._register_preview_process(file_generation, process))
            for t, img in zip(positions, converted):
                self._store_prewarmed_converted(
                    (video_path, round(t, 3), tonemapper, lut_enabled, use_gpu), img)
        except Exception:
            logging.exception('preview batch converted pre-warm failed')

    def _prewarm_other_frames(
        self,
        video_path: str,
        duration: float,
        tonemapper: str,
        generation: int,
        file_generation: int | None = None,
        lut_enabled: bool = True,
    ) -> None:
        """Dispatch pre-warm batch tasks for the non-visible seek frames."""
        if file_generation is None:
            file_generation = getattr(self, '_preview_file_generation', 0)
        assert file_generation is not None
        if file_generation != getattr(self, '_preview_file_generation', 0):
            return
        if not getattr(self, '_preview_prewarm_enabled', True):
            return

        if not hasattr(self, '_preview_cache_original'):
            self._preview_cache_original = {}
            self._preview_cache_converted = {}

        use_gpu = self._use_gpu_extraction(tonemapper)

        positions: list[float] = []
        for index in range(1, self.total_frames + 1):
            if index == self.current_frame_index:
                continue
            t = (index / (self.total_frames + 1)) * duration
            t_key = round(t, 3)
            if ((video_path, t_key) not in self._preview_cache_original or
                    (video_path, t_key, tonemapper, lut_enabled, use_gpu) not in self._preview_cache_converted):
                positions.append(t)

        if not positions:
            return

        if hasattr(self, '_preview_pool'):
            self._submit_preview_task(
                file_generation, self._prewarm_batch_originals, video_path, positions, generation,
                file_generation)
            self._submit_preview_task(
                file_generation, self._prewarm_batch_converted, video_path, positions, tonemapper, generation,
                file_generation,
                lut_enabled)
        else:
            self._prewarm_batch_originals(video_path, positions, generation, file_generation)
            self._prewarm_batch_converted(
                video_path, positions, tonemapper, generation, file_generation, lut_enabled)

    # ── Main display entrypoints ───────────────────────────────────────────────

    def display_frames(self, video_path: str) -> None:
        """Kick off frame extraction on a worker thread and render on the main thread."""
        tonemapper = self.tonemap_var.get().lower()
        lut_enabled = self._effective_lut_enabled()

        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        generation = self._preview_generation
        file_generation = getattr(self, '_preview_file_generation', 0)
        previous_visible = getattr(self, '_preview_thread', None)
        if previous_visible is not None:
            previous_visible.cancel()

        def worker() -> None:
            try:
                if file_generation != getattr(self, '_preview_file_generation', 0):
                    return
                duration = self._get_duration(video_path)
                time_position = self._preview_time_position(duration)
                time_key = round(time_position, 3)
                use_gpu = self._use_gpu_extraction(tonemapper)
                original, converted = self._extract_preview_images(
                    video_path, time_position, tonemapper, lut_enabled,
                    process_started=lambda process: self._register_preview_process(file_generation, process),
                )
                if generation == self._preview_generation:
                    self._active_preview_cache_position = (video_path, time_key)
                    self._last_displayed_preview_key = (
                        video_path, time_key, tonemapper, lut_enabled, use_gpu)
                    self._schedule_on_main(lambda: self._render_preview_images(
                        original, converted, time_position, generation))
                self._prewarm_other_frames(
                    video_path, duration, tonemapper, generation, file_generation, lut_enabled)
            except Exception as e:
                # A stale (superseded) job's error must not clobber a newer
                # preview, like the success path already guards against above.
                if generation == self._preview_generation:
                    self._schedule_on_main(lambda err=e: self.handle_preview_error(err))

        self._preview_thread = self._submit_preview_task(file_generation, worker)

    def _render_preview_images(
        self,
        original_image: Image.Image,
        converted_image_base: Image.Image,
        time_position: float,
        generation: int | None = None,
    ) -> None:
        """Apply extracted frames to the Tk labels. Must run on the main thread."""
        if generation is not None and generation != getattr(self, '_preview_generation', generation):
            return
        self._hide_preview_loading()
        self.original_image = original_image
        self.last_time_position = time_position
        self.converted_image_base = converted_image_base

        if getattr(self, '_window_auto_fitted', False):
            size = getattr(self, '_preview_render_size', None) or self._preview_target_size()
        else:
            size = self._initial_preview_size()
        self._render_preview_at_size(size)

        self._reveal_preview()
        self.adjust_window_size()

    def update_frame_preview(self, event: object = None) -> None:
        """Update the frame preview without blocking the UI."""
        if self.display_image_var.get() and self.input_path_var.get():
            try:
                video_path = self.input_path_var.get()
                self.error_label.config(text="")
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
            self.button_container.grid_remove()
            self.arrange_widgets(image_frame=False)
        self.tonemap_combobox.selection_clear()
