import sys
from tkinterdnd2 import TkinterDnD
from gui import HDRConverterGUI
from licensing import check_license_nonblocking
from utils import setup_dpi_awareness

if __name__ == "__main__":
    setup_dpi_awareness()
    root = TkinterDnD.Tk()
    root.withdraw()
    gui_holder: dict = {}

    def _on_license_change(licensed: bool) -> None:
        root.after(0, lambda: gui_holder['gui']._apply_license_state(licensed))

    # Trust the cached local token immediately so startup never blocks on the
    # occasional (~monthly) Lemon Squeezy revalidation call; if that deferred
    # check later disagrees, _apply_license_state re-gates the UI live.
    initial_licensed = check_license_nonblocking(on_change=_on_license_change)
    gui_holder['gui'] = HDRConverterGUI(root, licensed=initial_licensed)
    root.deiconify()
    root.mainloop()
