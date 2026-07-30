"""Modal dialogs: license activation and auto-update prompt."""
from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from dialog_theme import (
    _ACCENT,
    _BG,
    _ENTRY_BG,
    _ERROR_FG,
    _FG,
    _FONT,
    _FONT_BOLD,
    _FONT_SM,
    _center_over_master,
)

# Imported as a module object, not `from pro.license_dialog import (...)`. As
# in src/licensing.py: a `from`-import of an unresolved module leaves pyright
# treating the unresolved import *declaration* as authoritative for these
# names -- it wins over the perfectly good `class`/`def`s in the `except`
# block below, so every consumer's `from dialogs import _LicenseDialog` (and
# gui.py's second-hop `from dialogs import _LicenseDialog, ...`) would fail
# to type-check even though the free stub is defined right here. Going
# through `importlib.import_module` sidesteps that: there is no unresolved
# `from` target for pyright to bind these names to, so the free-edition
# definitions below are what consumers see whenever `pro/` is absent (i.e.
# in CI, and in every Community Edition build).
try:
    _pro_license_dialog = importlib.import_module('pro.license_dialog')
except ImportError:  # Community Edition — no Pro backend in this build.
    _pro_license_dialog = None

if _pro_license_dialog is not None:
    _LicenseDialog = _pro_license_dialog._LicenseDialog
    activate_license = _pro_license_dialog.activate_license
else:
    from licensing import activate_license  # the façade's raising stub

    class _LicenseDialog(tk.Toplevel):
        """Free-edition stand-in: explains Pro is absent and links to the store.

        gui._open_license_dialog constructs this, waits on it, and reads
        .activated -- so this must be a real Toplevel exposing that property,
        not a no-op. It has no key entry field because this build cannot
        activate anything.
        """

        def __init__(self, master: tk.Misc) -> None:
            super().__init__(master)
            self.configure(bg=_BG)
            self.title('HDR to SDR Converter — Pro')
            self.resizable(False, False)
            self.protocol('WM_DELETE_WINDOW', self.destroy)
            tk.Label(self, text='Pro features', bg=_BG, fg=_FG,
                     font=_FONT_BOLD).pack(pady=(26, 6))
            tk.Label(self,
                     text='This is the Community Edition, which cannot activate\n'
                          'a license. Get the full version to unlock Pro.',
                     bg=_BG, fg='#aaaaaa', font=_FONT_SM,
                     justify='center').pack(pady=(0, 12))
            link = tk.Label(self, text='Get Pro at hdrtosdr.com',
                            bg=_BG, fg=_ACCENT, font=_FONT_SM, cursor='hand2')
            link.pack(pady=(0, 6))
            link.bind('<Button-1>',
                      lambda _: webbrowser.open('https://hdrtosdr.com/#pricing'))
            tk.Button(self, text='Close', command=self.destroy,
                      bg='#3a3a3a', fg=_FG, activebackground='#4a4a4a',
                      activeforeground=_FG, relief='flat', padx=18, pady=7,
                      font=_FONT, cursor='hand2').pack(pady=(8, 16))
            _center_over_master(self, master, min_w=400, min_h=210)

        @property
        def activated(self) -> bool:
            return False


class _UpdateDialog(tk.Toplevel):
    """Dark-themed modal that prompts the user to install an available update."""

    def __init__(self, master: tk.Misc, current_ver: str, new_ver: str, download_url: str,
                 release_url: str) -> None:
        super().__init__(master)
        self.configure(bg=_BG)
        self.title('Update Available')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self._current_ver = current_ver
        self._new_ver = new_ver
        self._url = download_url
        self._release_url = release_url
        self._build_ui()
        _center_over_master(self, master, min_w=430, min_h=200)

    def _build_ui(self) -> None:
        tk.Label(self, text='Update Available',
                 bg=_BG, fg=_FG, font=_FONT_BOLD).pack(pady=(24, 4))
        tk.Label(self,
                 text=f'Version {self._new_ver} is available  (you have {self._current_ver})',
                 bg=_BG, fg='#aaaaaa', font=_FONT_SM).pack(pady=(0, 4))
        tk.Label(self,
                 text='The app will close and the installer will open automatically.',
                 bg=_BG, fg='#666666', font=_FONT_SM).pack(pady=(0, 12))

        link = tk.Label(self, text='View changelog',
                        bg=_BG, fg=_ACCENT, font=_FONT_SM, cursor='hand2')
        link.pack(pady=(0, 4))
        link.bind('<Button-1>', lambda _: self._open_changelog())

        self._status_var = tk.StringVar()
        tk.Label(self, textvariable=self._status_var,
                 bg=_BG, fg='#aaaaaa', font=_FONT_SM).pack()

        self._progress_var = tk.DoubleVar(value=0)
        self._progress = ttk.Progressbar(self, variable=self._progress_var,
                                          maximum=100, length=360)

        self._tmp_dir: str | None = None

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

    def _open_changelog(self) -> None:
        webbrowser.open(self._release_url)

    def _start_download(self) -> None:
        from updater import download_installer
        self._update_btn.config(state='disabled', text='Downloading…')
        self._later_btn.config(state='disabled')
        self._status_var.set('Starting download…')
        self._progress.pack(pady=(6, 0))
        self.protocol('WM_DELETE_WINDOW', lambda: None)

        # A prior failed attempt's temp dir is only cleaned up here, right
        # before minting a new one -- not in _on_download_error -- so a
        # successful download's directory (still needed while the detached
        # installer runs from it) is never touched.
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        self._tmp_dir = tempfile.mkdtemp(prefix='hdr_to_sdr_update_')
        dest = os.path.join(self._tmp_dir, 'HDR_to_SDR_Setup.exe')

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

    def destroy(self) -> None:
        # Reached via Later or window-close, both after a failed download
        # (during an active download the close protocol is disarmed, and a
        # successful download closes via self.master.destroy(), never this).
        # The failed attempt's directory has no installer left to run from,
        # so it must not be left behind.
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        super().destroy()
