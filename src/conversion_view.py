"""The seam between a conversion and whatever is watching it.

conversion.py used to import tkinter and call messagebox directly, which made
a headless/CLI mode impossible and forced 44 messagebox patches into the test
suite. It now depends on this module instead: standard library only, no
tkinter, no gui, no conversion. Importable in a headless process.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class Notice:
    """One user-facing message, with no opinion on how it is shown."""
    kind: str            # 'info' | 'warning' | 'error'
    title: str
    body: str

    @classmethod
    def info(cls, title: str, body: str) -> "Notice":
        return cls('info', title, body)

    @classmethod
    def warning(cls, title: str, body: str) -> "Notice":
        return cls('warning', title, body)

    @classmethod
    def error(cls, title: str, body: str) -> "Notice":
        return cls('error', title, body)


class ConversionView(Protocol):
    """Everything a conversion needs from the outside world.

    Nine members. `on_complete` is an attribute rather than a method because
    it is queue control flow supplied by the caller, not something a view
    implements -- but it lives here so that "is anyone watching this run?"
    is answerable from one object instead of two.
    """

    on_complete: Callable[[bool], None] | None

    def notify(self, notice: Notice) -> None: ...
    def schedule(self, fn: Callable[[], None]) -> None: ...
    def set_progress(self, pct: float) -> None: ...
    def set_inputs_enabled(self, enabled: bool) -> None: ...
    def set_cancel_visible(self, visible: bool,
                           on_cancel: Callable[[], None] | None = None) -> None: ...
    def restore_drop_target(self) -> None: ...
    def on_gpu_fallback(self) -> None: ...
    def open_output(self, path: str) -> None: ...
