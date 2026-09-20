"""Modal dialogs: license activation and auto-update prompt."""
from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import threading
import tkinter as tk
import webbrowser
from typing import Callable
from tkinter import ttk

from dialog_theme import (
    _ACCENT,
    _BG,
    _ERROR_FG,
    _FG,
    _FONT,
    _FONT_BOLD,
    _FONT_SM,
    _center_over_master,
)

# Imported as a module object, not `from pro.license_dialog import (...)` --
# same pyright unresolved-import trick as src/licensing.py.
try:
    _pro_license_dialog = importlib.import_module('pro.license_dialog')
except ModuleNotFoundError as exc:
    if exc.name not in {'pro', 'pro.license_dialog'}:
        raise
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


class _CustomResolutionDialog(tk.Toplevel):
    """Aspect-locked custom-resolution editor for licensed users."""

    def __init__(self, master: tk.Misc, initial_dimensions: tuple[int, int] | None,
                 dimensions_for_value: Callable[[int, bool], tuple[int, int]],
                 apply_callback: Callable[[int, bool], None]) -> None:
        super().__init__(master)
        try:
            master_iconbitmap = getattr(master, 'iconbitmap', None)
            icon_path = master_iconbitmap() if callable(master_iconbitmap) else ''
            if icon_path:
                self.iconbitmap(icon_path)
        except Exception:
            pass
        self.configure(bg=_BG)
        self.title('Custom Resolution')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self._dimensions_for_value = dimensions_for_value
        self._apply_callback = apply_callback
        self._width_authoritative = True
        self._syncing = False
        self.width_var = tk.StringVar(
            value=str(initial_dimensions[0]) if initial_dimensions else '')
        self.height_var = tk.StringVar(
            value=str(initial_dimensions[1]) if initial_dimensions else '')
        self.error_var = tk.StringVar()

        tk.Label(self, text='Width', bg=_BG, fg=_FG, font=_FONT).grid(
            row=0, column=0, padx=(22, 5), pady=(20, 5))
        self.width_entry = ttk.Entry(self, textvariable=self.width_var, width=8)
        self.width_entry.grid(row=1, column=0, padx=(22, 5), pady=(0, 4))
        tk.Label(self, text='×', bg=_BG, fg=_FG, font=_FONT_BOLD).grid(
            row=1, column=1, padx=2, pady=(0, 4))
        tk.Label(self, text='Height', bg=_BG, fg=_FG, font=_FONT).grid(
            row=0, column=2, padx=(5, 22), pady=(20, 5))
        self.height_entry = ttk.Entry(self, textvariable=self.height_var, width=8)
        self.height_entry.grid(row=1, column=2, padx=(5, 22), pady=(0, 4))
        tk.Label(self, textvariable=self.error_var, bg=_BG, fg=_ERROR_FG,
                 font=_FONT_SM).grid(row=2, column=0, columnspan=3, pady=(0, 5))
        self.apply_button = tk.Button(
            self, text='Apply', command=self._apply, bg='#3a3a3a', fg=_FG,
            activebackground='#4a4a4a', activeforeground=_FG, relief='flat',
            padx=18, pady=7, font=_FONT, cursor='hand2')
        self.apply_button.grid(row=3, column=0, columnspan=3, pady=(2, 16))

        self.width_var.trace_add('write', lambda *_: self._sync_dimensions(True))
        self.height_var.trace_add('write', lambda *_: self._sync_dimensions(False))
        self.width_entry.bind('<FocusIn>', lambda _: self._set_authority(True))
        self.height_entry.bind('<FocusIn>', lambda _: self._set_authority(False))
        self.width_entry.bind('<Button-1>', lambda _: self._set_authority(True))
        self.height_entry.bind('<Button-1>', lambda _: self._set_authority(False))
        self.width_entry.bind('<Return>', self._apply)
        self.height_entry.bind('<Return>', self._apply)
        self._set_authority(True)
        _center_over_master(self, master, min_w=300, min_h=165)

    def _set_authority(self, width_authoritative: bool) -> None:
        self._width_authoritative = width_authoritative
        self.width_entry.config(state='normal' if width_authoritative else 'readonly')
        self.height_entry.config(state='readonly' if width_authoritative else 'normal')
        self._sync_dimensions(width_authoritative)

    def _sync_dimensions(self, width_authoritative: bool) -> None:
        if self._syncing or width_authoritative != self._width_authoritative:
            return
        value = self.width_var.get() if width_authoritative else self.height_var.get()
        try:
            dimension = int(value.strip(), 10)
            if dimension <= 0:
                raise ValueError
        except ValueError:
            paired_var = self.height_var if width_authoritative else self.width_var
            if paired_var.get():
                self._syncing = True
                paired_var.set('')
                self._syncing = False
            return

        width, height = self._dimensions_for_value(dimension, width_authoritative)
        paired_var = self.height_var if width_authoritative else self.width_var
        paired_value = str(height if width_authoritative else width)
        if paired_var.get() != paired_value:
            self._syncing = True
            paired_var.set(paired_value)
            self._syncing = False

    def _apply(self, event: object = None) -> str:
        value = self.width_var.get() if self._width_authoritative else self.height_var.get()
        try:
            dimension = int(value.strip(), 10)
            if dimension <= 0:
                raise ValueError
        except ValueError:
            self.error_var.set('Enter a positive whole number.')
            return 'break'

        try:
            self._apply_callback(dimension, self._width_authoritative)
        except ValueError as exc:
            self.error_var.set(str(exc))
            return 'break'
        self.destroy()
        return 'break'


class _UpdateDialog(tk.Toplevel):
    """Dark-themed modal that prompts the user to install an available update."""

    def __init__(self, master: tk.Misc, current_ver: str, new_ver: str, download_url: str,
                 release_url: str, expected_size: int, expected_sha256: str,
                 shutdown_callback: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self.configure(bg=_BG)
        self.title('Update Available')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self._current_ver = current_ver
        self._new_ver = new_ver
        self._url = download_url
        self._release_url = release_url
        self._expected_size = expected_size
        self._expected_sha256 = expected_sha256
        self._shutdown_callback = shutdown_callback
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

        # Clean up a prior failed attempt's temp dir here, not in
        # _on_download_error -- a successful one is still needed by the installer.
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
                download_installer(
                    self._url,
                    dest,
                    self._expected_size,
                    self._expected_sha256,
                    _on_progress,
                )
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
        try:
            launch_installer(path)
        except Exception:
            if self._tmp_dir is not None:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
                self._tmp_dir = None
            self._on_download_error('')
            return
        if self._shutdown_callback is not None:
            self._shutdown_callback()
        else:
            self.master.destroy()

    def _on_download_error(self, msg: str) -> None:
        self._progress.pack_forget()
        self._status_var.set('Download failed — please try again later.')
        self._update_btn.config(state='normal', text='Retry')
        self._later_btn.config(state='normal')
        self.protocol('WM_DELETE_WINDOW', self.destroy)

    def destroy(self) -> None:
        # Reached via Later/window-close after a failed download -- clean up
        # the now-orphaned attempt's directory.
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        super().destroy()
