import sys
from tkinterdnd2 import TkinterDnD
from gui import HDRConverterGUI
from licensing import check_license
from utils import setup_dpi_awareness

if __name__ == "__main__":
    setup_dpi_awareness()
    root = TkinterDnD.Tk()
    root.withdraw()
    HDRConverterGUI(root, licensed=check_license())
    root.deiconify()
    root.mainloop()
