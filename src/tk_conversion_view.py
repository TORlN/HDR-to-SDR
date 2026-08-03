"""The Tk side of the ConversionView port.

Everything conversion.py used to do to widgets directly lives here: the
messagebox calls, the root.after(0, ...) marshalling, the enable/disable
sweep and the drop-target restore. This is the only module in the port that
imports tkinter.
"""
from __future__ import annotations

import logging
import webbrowser
from typing import Any, Callable
from tkinter import messagebox, ttk

from conversion_view import Notice

# Looked up by name at call time, not pre-bound to the function object here:
# pre-binding would capture the real tkinter.messagebox functions at import
# time, before a test's `patch('src.tk_conversion_view.messagebox')` ever has
# a chance to take effect -- notify() would keep calling the real, unmocked
# dialog functions regardless of the patch.
_SHOW = {
    'info': 'showinfo',
    'warning': 'showwarning',
    'error': 'showerror',
}


class TkConversionView:
    """Renders a conversion onto the real Tk GUI."""

    def __init__(self, gui_instance: Any, progress_var: Any,
                 interactable_elements: list[Any], cancel_button: Any,
                 on_complete: Callable[[bool], None] | None = None) -> None:
        self._gui = gui_instance
        self._progress_var = progress_var
        self._elements = interactable_elements
        self._cancel_button = cancel_button
        self.on_complete = on_complete

    def notify(self, notice: Notice) -> None:
        getattr(messagebox, _SHOW[notice.kind])(notice.title, notice.body)

    def schedule(self, fn: Callable[[], None]) -> None:
        self._gui.root.after(0, fn)

    def set_progress(self, pct: float) -> None:
        # Marshalling lives here so callers on the ffmpeg monitor thread are
        # thread-safe by construction rather than by remembering to wrap.
        self._gui.root.after(0, lambda: self._progress_var.set(pct))
        self._gui.root.after(0, self._gui.root.update_idletasks)

    def set_inputs_enabled(self, enabled: bool) -> None:
        for element in self._elements:
            if not enabled:
                element.config(state='disabled')
                continue
            # A ttk.Combobox re-enabled to 'normal' becomes freely typeable,
            # not just clickable -- these are only ever built 'readonly', so
            # restore that instead of clobbering it with 'normal'.
            state = 'readonly' if isinstance(element, ttk.Combobox) else 'normal'
            element.config(state=state)

    def set_cancel_visible(self, visible: bool,
                           on_cancel: Callable[[], None] | None = None) -> None:
        if visible:
            if on_cancel is not None:
                self._cancel_button.config(command=on_cancel)
            self._cancel_button.grid()
        else:
            self._cancel_button.grid_remove()

    def restore_drop_target(self) -> None:
        register = getattr(self._gui, 'register_drop_target', None)
        if register is not None:
            register()

    def on_gpu_fallback(self) -> None:
        # DANGER: _write_back_current_settings() writes to whichever batch item
        # the CURRENT widgets name, but the retry re-encodes the ORIGINAL
        # request's paths. Safe only because every path widget is disabled for
        # the duration of a conversion. If a future change unlocks the batch
        # listbox mid-conversion, this stamps gpu_accel=False onto the wrong
        # queue item.
        self._gui.gpu_accel_var.set(False)
        # A raw Variable.set() doesn't fire the checkbox's command= callback
        # (check_gpu_acceleration), which is normally what persists a GPU
        # toggle onto the current batch item's stored settings -- without
        # this, reselecting the item later would restore the stale,
        # pre-failure gpu_accel=True.
        self._gui._write_back_current_settings()

    def open_output(self, path: str) -> None:
        webbrowser.open(path)


class BatchConversionView(TkConversionView):
    """A queued run: no human is watching for a modal.

    Overrides exactly one method. A modal here stalls the whole queue until
    someone clicks it, and the item's status is already visible in the batch
    list.
    """

    def notify(self, notice: Notice) -> None:
        logging.error(f"Conversion notice ({notice.kind}): "
                      f"{notice.title}: {notice.body}")
