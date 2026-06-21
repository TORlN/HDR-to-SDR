import sys
import tkinter as tk
from tkinterdnd2 import TkinterDnD, DND_FILES
from PIL import Image

from licensing import (
    LicenseError,
    InvalidKeyError,
    DeviceLimitError,
    NetworkError,
    activate_license,
    check_license,
)

"""
This script initializes and runs a Tkinter GUI application.
Modules:
    tkinter: Standard Python interface to the Tk GUI toolkit.
    gui: Custom module containing the class to create the main window.
Functions:
    create_main_window(root): Sets up the main window of the application.
Execution:
    When run as the main module, this script creates the main TkinterDnD window,
    sets up the main window using the HDRConverterGUI class, and starts
    the Tkinter main event loop.
"""

_BG = '#1e1e1e'
_FG = '#ffffff'
_ENTRY_BG = '#2d2d2d'
_ACCENT = '#0078d4'
_ERROR_FG = '#ff6b6b'
_FONT = ('Segoe UI', 10)
_FONT_BOLD = ('Segoe UI', 13, 'bold')
_FONT_SMALL = ('Segoe UI', 9)


class _LicenseDialog(tk.Toplevel):
    """Dark-themed modal that blocks app startup until a valid license is entered."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.configure(bg=_BG)
        self.title('Activate HDR to SDR Converter')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._activated = False
        self._build_ui()
        self.update_idletasks()
        w, h = 460, 230
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f'{w}x{h}+{x}+{y}')
        self.grab_set()
        self.focus_set()

    def _build_ui(self) -> None:
        tk.Label(
            self, text='Activate HDR to SDR Converter',
            bg=_BG, fg=_FG, font=_FONT_BOLD,
        ).pack(pady=(28, 4))

        tk.Label(
            self,
            text='Enter your license key to continue.',
            bg=_BG, fg='#aaaaaa', font=_FONT_SMALL,
        ).pack(pady=(0, 10))

        self._key_var = tk.StringVar()
        entry = tk.Entry(
            self, textvariable=self._key_var, width=44,
            bg=_ENTRY_BG, fg=_FG, insertbackground=_FG,
            relief='flat', font=_FONT,
        )
        entry.pack(padx=32, ipady=7)
        entry.focus_set()
        entry.bind('<Return>', lambda _: self._submit())

        self._status_var = tk.StringVar()
        tk.Label(
            self, textvariable=self._status_var,
            bg=_BG, fg=_ERROR_FG, font=_FONT_SMALL,
        ).pack(pady=(6, 0))

        tk.Button(
            self, text='Activate',
            command=self._submit,
            bg=_ACCENT, fg=_FG,
            activebackground='#005fa3', activeforeground=_FG,
            relief='flat', padx=20, pady=7,
            font=_FONT, cursor='hand2',
        ).pack(pady=14)

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


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.withdraw()

    if not check_license():
        dlg = _LicenseDialog(root)
        root.wait_window(dlg)
        if not dlg.activated:
            root.destroy()
            sys.exit(0)

    from gui import HDRConverterGUI
    app = HDRConverterGUI(root)
    root.deiconify()
    root.mainloop()
