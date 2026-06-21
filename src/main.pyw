import sys
from tkinterdnd2 import TkinterDnD
from licensing import check_license

if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.withdraw()
    from gui import HDRConverterGUI
    HDRConverterGUI(root, licensed=check_license())
    root.deiconify()
    root.mainloop()
