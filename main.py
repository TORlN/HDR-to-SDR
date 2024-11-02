import tkinter as tk
from gui import create_main_window
# import logging
from PIL import Image

# Suppress specific logging message from the Pillow library
# logging.getLogger("PIL.PngImagePlugin").setLevel(logging.ERROR)

"""
This script initializes and runs a Tkinter GUI application.
Modules:
    tkinter: Standard Python interface to the Tk GUI toolkit.
    gui: Custom module containing the function to create the main window.
Functions:
    create_main_window(root): Sets up the main window of the application.
Execution:
    When run as the main module, this script creates the main Tkinter window,
    sets up the main window using the create_main_window function, and starts
    the Tkinter main event loop.
"""

if __name__ == "__main__":
    # Create the main Tkinter window
    root = tk.Tk()
    create_main_window(root)
    root.mainloop()