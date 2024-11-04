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

    @patch('src.gui.conversion_manager.start_conversion')
    @patch('src.gui.HDRConverterGUI.unregister_drop_target')  # Corrected patch target
    @patch('src.gui.messagebox.askyesno')
    @patch('src.gui.messagebox.showwarning')
    def test_convert_video(self, mock_showwarning, mock_askyesno, mock_unregister_drop_target, mock_start_conversion):
        mock_askyesno.return_value = True
        self.gui.input_path_var.set('test_video.mp4')
        self.gui.output_path_var.set('test_video_sdr.mp4')
        self.gui.convert_video()
        mock_start_conversion.assert_called_once()
        mock_unregister_drop_target.assert_called_once()  # Ensure unregister_drop_target is called

        self.gui.input_path_var.set('')
        self.gui.convert_video()
        mock_showwarning.assert_called_once_with("Warning", "Please select both an input file and specify an output file.")

    @patch('src.gui.conversion_manager.cancel_conversion')
    def test_cancel_conversion(self, mock_cancel_conversion):
        self.gui.cancel_conversion()
        mock_cancel_conversion.assert_called_once()

    def test_handle_file_drop(self):
        """Test handling of file drop with invalid input leading to error display."""
        gui = self.gui

        # Create a mock event with a data attribute containing an invalid file path
        event = MagicMock()
        event.data = "invalid_file.xyz"

        # Mock extract_frame to raise an Exception when called
        with patch('src.gui.extract_frame', side_effect=Exception("Invalid file format")):
            gui.handle_file_drop(event)

        # Assert that the error_label text has been updated with the error message
        self.assertEqual(
            gui.error_label.cget("text"),
            "Error displaying image: Invalid file format"
        )

    def test_disable_enable_ui(self):
        elements = [MagicMock(), MagicMock()]
        self.gui.disable_ui(elements)
        for element in elements:
            element.config.assert_called_with(state="disabled")
            element.config.reset_mock()  # Reset mock to test the next call

        # Manually re-enable the UI elements
        for element in elements:
            element.config(state='normal')

        # Assert that elements were re-enabled
        for element in elements:
            element.config.assert_called_with(state="normal")

if __name__ == '__main__':
    unittest.main()
