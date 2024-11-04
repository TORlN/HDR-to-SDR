import unittest
from unittest.mock import patch, MagicMock, call
import tkinter as tk
from tkinter import ttk
from src.gui import HDRConverterGUI
import tkinterdnd2
from PIL import Image

class TestHDRConverterGUI(unittest.TestCase):

    @patch('tkinterdnd2.Tk.drop_target_register')
    @patch('tkinterdnd2.Tk.dnd_bind')
    def setUp(self, mock_dnd_bind, mock_drop_target_register):
        self.root = tkinterdnd2.Tk()  # Use tkinterdnd2.Tk instead of tk.Tk
        self.gui = HDRConverterGUI(self.root)

    def tearDown(self):
        self.root.destroy()

    @patch('src.gui.filedialog.askopenfilename')
    def test_select_file(self, mock_askopenfilename):
        mock_askopenfilename.return_value = 'test_video.mp4'
        self.gui.select_file()
        self.assertEqual(self.gui.input_path_var.get(), 'test_video.mp4')
        self.assertEqual(self.gui.output_path_var.get(), 'test_video_sdr.mp4')

    @patch('src.gui.extract_frame')
    @patch('src.gui.extract_frame_with_conversion')
    def test_update_frame_preview(self, mock_extract_frame_with_conversion, mock_extract_frame):
        mock_extract_frame.return_value = Image.new('RGB', (100, 100))
        mock_extract_frame_with_conversion.return_value = Image.new('RGB', (100, 100))
        self.gui.input_path_var.set('test_video.mp4')
        self.gui.update_frame_preview()
        mock_extract_frame.assert_called_once_with('test_video.mp4')
        mock_extract_frame_with_conversion.assert_called_once_with('test_video.mp4', self.gui.gamma_var.get())

    @patch('src.gui.messagebox.showerror')
    def test_handle_preview_error(self, mock_showerror):
        self.gui.error_label = MagicMock()
        self.gui.handle_preview_error(Exception("Test Error"))
        self.gui.error_label.config.assert_called_once_with(text="Error displaying image: Test Error")

    @patch('src.gui.messagebox.askyesno')
    @patch('src.gui.messagebox.showwarning')
    @patch('src.gui.conversion_manager.start_conversion')
    def test_convert_video(self, mock_start_conversion, mock_showwarning, mock_askyesno):
        self.gui.input_path_var.set('test_video.mp4')
        self.gui.output_path_var.set('test_video_sdr.mp4')
        mock_askyesno.return_value = True
        self.gui.convert_video()
        mock_start_conversion.assert_called_once()

        self.gui.input_path_var.set('')
        self.gui.convert_video()
        mock_showwarning.assert_called_once_with("Warning", "Please select both an input file and specify an output file.")

    @patch('src.gui.conversion_manager.cancel_conversion')
    def test_cancel_conversion(self, mock_cancel_conversion):
        self.gui.cancel_conversion()
        mock_cancel_conversion.assert_called_once()

    @patch('src.gui.messagebox.showerror')
    def test_handle_file_drop(self, mock_showerror):
        event = MagicMock()
        event.data = '{test_video.mp4}'
        self.gui.handle_file_drop(event)
        self.assertEqual(self.gui.input_path_var.get(), 'test_video.mp4')
        self.assertEqual(self.gui.output_path_var.get(), 'test_video_sdr.mp4')

        event.data = '{invalid_path}'
        self.gui.handle_file_drop(event)
        mock_showerror.assert_called_once()

    def test_disable_enable_ui(self):
        elements = [MagicMock(), MagicMock()]
        self.gui.disable_ui(elements)
        for element in elements:
            element.config.assert_called_with(state="disabled")

        self.gui.enable_ui(elements)
        for element in elements:
            element.config.assert_called_with(state="normal")

if __name__ == '__main__':
    unittest.main()
