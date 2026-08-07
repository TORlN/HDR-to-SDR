"""A ConversionView that records instead of rendering.

Every conversion test drives the real ConversionManager against one of these,
so assertions read as "what did the conversion tell the user" rather than
"which messagebox function was patched". It has its own tests in
test/recording_view_test.py.
"""
from __future__ import annotations

import os
import sys
from typing import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from conversion_view import Notice  # noqa: E402


class RecordingConversionView:
    """Implements every ConversionView member; renders nothing."""

    def __init__(self, on_complete: Callable[[bool, str | None], None] | None = None) -> None:
        self.on_complete = on_complete
        self.notices: list[Notice] = []
        self.progress: list[float] = []
        self.inputs_enabled: list[bool] = []
        self.cancel_visible: list[bool] = []
        self.cancel_handler: Callable[[], None] | None = None
        self.drop_target_restored = 0
        self.opened: list[str] = []

    def notify(self, notice: Notice) -> None:
        self.notices.append(notice)

    def schedule(self, fn: Callable[[], None]) -> None:
        # Inline, not deferred: the Tk view marshals onto the main thread, but
        # a test has no main loop to marshal onto. Running fn here is what
        # lets a test assert on the effects of a scheduled callback without
        # any root.after mock choreography.
        fn()

    def set_progress(self, pct: float) -> None:
        self.progress.append(pct)

    def set_inputs_enabled(self, enabled: bool) -> None:
        self.inputs_enabled.append(enabled)

    def set_cancel_visible(self, visible: bool,
                           on_cancel: Callable[[], None] | None = None) -> None:
        self.cancel_visible.append(visible)
        if on_cancel is not None:
            self.cancel_handler = on_cancel

    def restore_drop_target(self) -> None:
        self.drop_target_restored += 1

    def open_output(self, path: str) -> None:
        self.opened.append(path)
