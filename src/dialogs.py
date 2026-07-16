"""Modal dialogs: license activation and auto-update prompt."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from licensing import (
    activate_license,
    InvalidKeyError,
    DeviceLimitError,
    NetworkError,
    LicenseError,
)

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
                 bg=_BG, fg='#aaaaaa', font=_FONT_SM).pack(pady=(0, 4))
        link = tk.Label(self, text="Don't have a key? Get Pro at hdrtosdr.com",
                        bg=_BG, fg=_ACCENT, font=_FONT_SM, cursor='hand2')
        link.pack(pady=(0, 10))
        link.bind('<Button-1>', lambda _: webbrowser.open('https://hdrtosdr.com/#pricing'))
        self._key_var = tk.StringVar()
        self._entry = tk.Entry(self, textvariable=self._key_var, width=44,
                         bg=_ENTRY_BG, fg=_FG, insertbackground=_FG,
                         relief='flat', font=_FONT)
        self._entry.pack(padx=32, ipady=7)
        self._entry.focus_set()
        self._entry.bind('<Return>', lambda _: self._submit())
        self._status_var = tk.StringVar()
        tk.Label(self, textvariable=self._status_var,
                 bg=_BG, fg=_ERROR_FG, font=_FONT_SM).pack(pady=(6, 0))
        self._activate_btn = tk.Button(
            self, text='Activate', command=self._submit,
            bg=_ACCENT, fg=_FG,
            activebackground='#005fa3', activeforeground=_FG,
            relief='flat', padx=20, pady=7,
            font=_FONT, cursor='hand2')
        self._activate_btn.pack(pady=14)
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
        self._entry.config(state='disabled')
        self._activate_btn.config(state='disabled')

        # activate_license is a blocking HTTP round-trip -- running it
        # directly on the Tk main thread would freeze the whole UI for the
        # duration of the request/timeout. Run it on a worker thread and
        # marshal the result back via self.after, matching _UpdateDialog's
        # existing download pattern.
        def _worker() -> None:
            try:
                activate_license(key)
            except LicenseError as exc:
                self.after(0, lambda e=exc: self._on_activation_error(e))
            else:
                self.after(0, self._on_activation_success)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_activation_success(self) -> None:
        self._activated = True
        self.destroy()

    def _on_activation_error(self, exc: LicenseError) -> None:
        self._entry.config(state='normal')
        self._activate_btn.config(state='normal')
        if isinstance(exc, InvalidKeyError):
            self._status_var.set('Invalid license key — please check and try again.')
        elif isinstance(exc, DeviceLimitError):
            self._status_var.set('Device limit reached. Deactivate another machine first.')
        elif isinstance(exc, NetworkError):
            self._status_var.set('Cannot reach license server. Check your connection.')
        else:
            self._status_var.set(str(exc))

    def _on_close(self) -> None:
        self._activated = False
        self.destroy()

    @property
    def activated(self) -> bool:
        return self._activated


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
