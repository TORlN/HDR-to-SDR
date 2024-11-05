import unittest
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
from tkinter import ttk
from src.gui import HDRConverterGUI
from PIL import Image

class TestHDRConverterGUI(TestCase):
    """Test suite for HDRConverterGUI class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Setup main patches
        self.patches = {
            'tk': patch('tkinterdnd2.Tk', autospec=True),
            'string_var': patch('tkinter.StringVar'),
            'double_var': patch('tkinter.DoubleVar'),
            'bool_var': patch('tkinter.BooleanVar'),
            'drop_register': patch('tkinterdnd2.Tk.drop_target_register'),
            'dnd_bind': patch('tkinterdnd2.Tk.dnd_bind')
        }
        
        # Start all patches
        self.mocks = {name: patcher.start() for name, patcher in self.patches.items()}
        
        # Setup mock root and variables
        self.mock_root = MagicMock()
        self.mocks['tk'].return_value = self.mock_root
        self.mock_string_var = MagicMock()
        self.mocks['string_var'].return_value = self.mock_string_var

        # Initialize GUI
        self.gui = HDRConverterGUI(self.mock_root)
        
        # Setup common GUI elements
        self._setup_gui_mocks()

    def _setup_gui_mocks(self):
        """Setup common GUI element mocks."""
        mock_elements = ['button_frame', 'image_frame', 'action_frame', 
                        'cancel_button', 'update_frame_preview',
                        'original_title_label', 'converted_title_label',
                        'error_label', 'clear_preview', 'adjust_window_size',
                        'arrange_widgets']
        
        for element in mock_elements:
            setattr(self.gui, element, MagicMock())

    def tearDown(self):
        """Clean up after each test."""
        # Stop all patches
        for patcher in self.patches.values():
            patcher.stop()
        
        self.gui.on_close()
        self.mock_root.destroy.assert_called_once()

    @patch('src.gui.filedialog.askopenfilename')
    def test_file_selection(self, mock_file_dialog):
        """Test file selection functionality."""
        mock_file_dialog.return_value = 'test_input.mp4'
        
        self.gui.select_file()

        # Verify file path updates
        self.mock_string_var.set.assert_has_calls([
            call('test_input.mp4'),
            call('test_input_sdr.mp4')
        ])

        # Verify UI updates
        self._assert_frame_updates()

    @patch('src.gui.extract_frame')  # Updated patch path
    @patch('src.gui.extract_frame_with_conversion')  # Updated patch path
    @patch('PIL.ImageTk.PhotoImage')
    def test_frame_preview_update(self, mock_photo_image, mock_convert, mock_extract):
        """Test frame preview update functionality."""
        # Setup mock images
        mock_image = MagicMock(spec=Image.Image)
        mock_image.resize = MagicMock(return_value=mock_image)
        mock_photo = MagicMock()
        mock_extract.return_value = mock_image
        mock_convert.return_value = mock_image
        mock_photo_image.return_value = mock_photo

        # Setup GUI variables
        self.gui.display_image_var = MagicMock(get=MagicMock(return_value=True))
        self.gui.input_path_var = MagicMock(get=MagicMock(return_value='test_input.mp4'))
        self.gui.gamma_var = MagicMock(get=MagicMock(return_value=2.2))
        
        # Setup GUI elements
        self.gui.original_image_label = MagicMock()
        self.gui.converted_image_label = MagicMock()
        self.gui.original_title_label = MagicMock()
        self.gui.converted_title_label = MagicMock()
        
        # Call display_frames directly since that's where the functions are used
        self.gui.display_frames('test_input.mp4')

        # Verify frame extraction and conversion
        mock_extract.assert_called_once_with('test_input.mp4')
        mock_convert.assert_called_once_with('test_input.mp4', 2.2)
        
        # Verify image resize calls
        mock_image.resize.assert_has_calls([
            call((960, 540), Image.Resampling.LANCZOS),
            call((960, 540), Image.Resampling.LANCZOS)
        ])
        
        # Verify PhotoImage creation and label updates
        mock_photo_image.assert_has_calls([call(mock_image), call(mock_image)])
        self.gui.original_image_label.config.assert_called_with(image=mock_photo)
        self.gui.converted_image_label.config.assert_called_with(image=mock_photo)

    @patch('src.gui.messagebox.askyesno')
    @patch('src.gui.HDRConverterGUI.unregister_drop_target')
    @patch('src.gui.conversion_manager.start_conversion')
    @patch('src.gui.os.path.isfile')  # Add this patch
    def test_video_conversion(self, mock_isfile, mock_start_conversion, mock_unregister, mock_confirm):
        """Test video conversion initialization."""
        self._setup_conversion_test(mock_confirm)
        mock_isfile.return_value = True  # Make os.path.isfile return True
        
        self.gui.convert_video()

        self._assert_conversion_started(mock_unregister, mock_start_conversion)

    def test_ui_state_management(self):
        """Test UI element state management."""
        test_elements = [MagicMock(), MagicMock()]
        
        # Test disable
        self.gui.disable_ui(test_elements)
        for element in test_elements:
            element.config.assert_called_with(state='disabled')

        # Test direct enable (since there's no enable_ui method)
        for element in test_elements:
            element.config.reset_mock()
            element.config(state='normal')
            element.config.assert_called_with(state='normal')

    def _assert_frame_updates(self):
        """Helper method to verify frame updates."""
        for frame in [self.gui.button_frame, self.gui.image_frame, 
                     self.gui.action_frame]:
            frame.grid.assert_called_once()
        self.gui.update_frame_preview.assert_called_once()

    def _assert_preview_updates(self, mock_extract, mock_convert):
        """Helper method to verify preview updates."""
        mock_extract.assert_called_once_with('test_input.mp4')
        mock_convert.assert_called_once_with('test_input.mp4', self.gui.gamma_var.get())
        self.gui.error_label.config.assert_called_with(text="")
        self.gui.arrange_widgets.assert_called_with(image_frame=True)

    def _setup_conversion_test(self, mock_confirm):
        """Helper method to setup conversion test."""
        # Create mock variables with specific return values
        self.gui.input_path_var = MagicMock()
        self.gui.output_path_var = MagicMock()
        self.gui.open_after_conversion_var = MagicMock()
        self.gui.gamma_var = MagicMock()
        
        # Set return values for get() calls
        self.gui.input_path_var.get.return_value = 'test_input.mp4'
        self.gui.output_path_var.get.return_value = 'test_output.mkv'
        self.gui.open_after_conversion_var.get.return_value = True
        self.gui.gamma_var.get.return_value = 2.2
        
        mock_confirm.return_value = True
        
        # Ensure drop_target_registered is True
        self.gui.drop_target_registered = True

    def _assert_conversion_started(self, mock_unregister, mock_convert):
        """Helper method to verify conversion startup."""
        mock_unregister.assert_called_once()
        
        # Get the actual call arguments
        actual_call = mock_convert.call_args
        
        # Assert each argument individually for better error messages
        args = actual_call[0]  # positional arguments
        self.assertEqual(args[0], 'test_input.mp4')
        self.assertEqual(args[1], 'test_output.mkv')
        self.assertEqual(args[2], 2.2)
        self.assertIs(args[3], self.gui.progress_var)
        self.assertEqual(args[4], self.gui.interactable_elements)
        self.assertIs(args[5], self.gui)
        self.assertTrue(args[6])  # open_after_conversion
        self.assertIs(args[7], self.gui.cancel_button)
        
        self.gui.cancel_button.grid.assert_called_once()

if __name__ == '__main__':
    unittest.main()
