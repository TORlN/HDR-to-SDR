"""Shared dark-theme constants and geometry helper for the app's modal dialogs.

Extracted from dialogs.py so the Pro activation dialog (which lives in the
private pro/ package) and the public update dialog can share styling without
either importing the other.
"""
from __future__ import annotations

import tkinter as tk

_BG       = '#1e1e1e'
_FG       = '#ffffff'
_ENTRY_BG = '#2d2d2d'
_ACCENT   = '#0078d4'
_ERROR_FG = '#ff6b6b'
_FONT      = ('Segoe UI', 10)
_FONT_BOLD = ('Segoe UI', 13, 'bold')
_FONT_SM   = ('Segoe UI', 9)


def _center_over_master(win: tk.Toplevel, master: tk.Misc, min_w: int, min_h: int) -> None:
    """Size *win* to fit its already-built content (plus fixed padding),
    floored at (min_w, min_h), and center it over *master*."""
    master.update_idletasks()
    win.update_idletasks()
    w = max(win.winfo_reqwidth(), min_w)
    h = max(win.winfo_reqheight(), min_h)
    frame_x = win.winfo_rootx() - win.winfo_x()
    frame_y = win.winfo_rooty() - win.winfo_y()
    px = master.winfo_rootx() + (master.winfo_width() - w) // 2 - frame_x
    py = master.winfo_rooty() + (master.winfo_height() - h) // 2 - frame_y
    win.geometry(f'{w}x{h}+{px}+{py}')
    win.grab_set()
    win.focus_set()
