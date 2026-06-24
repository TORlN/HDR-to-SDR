import sys
from tkinterdnd2 import TkinterDnD
from gui import HDRConverterGUI
from licensing import check_license

if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.withdraw()
    HDRConverterGUI(root, licensed=check_license())
    root.deiconify()
    root.mainloop()
