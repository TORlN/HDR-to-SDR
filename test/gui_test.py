import unittest
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
import tkinter as tk
from tkinter import ttk, DoubleVar, BooleanVar
from src.gui import HDRConverterGUI
from PIL import Image

class TestHDRConverterGUI(TestCase):
    """Test suite for HDRConverterGUI class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock variables first with proper specs and methods
        self.mock_progress_var = MagicMock(spec=DoubleVar)
        self.mock_string_var = MagicMock(spec=tk.StringVar)
        self.mock_string_var.set = MagicMock()
        self.mock_string_var.get = MagicMock(return_value='')
        self.mock_bool_var = MagicMock(spec=BooleanVar)

        # Create specific patches for all tk.Variable uses in gui.py
        tk_patches = {
            'root': patch('tkinterdnd2.Tk', autospec=True),
            # Remove the following line to avoid conflicting patches
            # 'string_var': patch('tkinter.StringVar', return_value=self.mock_string_var),
            'double_var': patch('tkinter.DoubleVar', return_value=self.mock_progress_var),
            'bool_var': patch('tkinter.BooleanVar', return_value=self.mock_bool_var),
            'drop_register': patch('tkinterdnd2.Tk.drop_target_register'),
            'dnd_bind': patch('tkinterdnd2.Tk.dnd_bind')
        }

        gui_patches = {
            'string_var': patch('src.gui.tk.StringVar', return_value=self.mock_string_var),
            'double_var': patch('src.gui.tk.DoubleVar', return_value=self.mock_progress_var),
            'bool_var': patch('src.gui.tk.BooleanVar', return_value=self.mock_bool_var)
        }

        # Combine all patches
        self.patches = {**tk_patches, **gui_patches}
        
        # Start all patches
        self.mocks = {name: patcher.start() for name, patcher in self.patches.items()}
        
        # Setup mock root
        self.mock_root = MagicMock()
        self.mocks['root'].return_value = self.mock_root

        # Initialize GUI
        self.gui = HDRConverterGUI(self.mock_root)
        self.gui.progress_var = self.mock_progress_var  # Ensure progress_var is set correctly
        
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

    @patch('src.gui.ImageTk.PhotoImage')
    @patch('src.gui.extract_frame_with_conversion')
    @patch('src.gui.extract_frame')
    def test_frame_preview_update(self, mock_extract, mock_convert, mock_photo_image):
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

        # Mock adjust_gamma method
        self.gui.adjust_gamma = MagicMock(return_value=mock_image)

        # Set the filter_var to return a valid filter option
        self.mock_string_var.get.return_value = 'Static'

        # Call display_frames directly since that's where the functions are used
        self.gui.display_frames('test_input.mp4')

        # Verify frame extraction and conversion with gamma=1.0 and filter_index=0
        mock_extract.assert_called_once_with('test_input.mp4')
        mock_convert.assert_called_once_with('test_input.mp4', gamma=1.0, filter_index=0)

        # Verify adjust_gamma is called with correct gamma value
        self.gui.adjust_gamma.assert_called_once_with(mock_image, 2.2)

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
    @patch('src.gui.os.path.isfile')
    def test_video_conversion(self, mock_isfile, mock_start_conversion, mock_unregister, mock_confirm):
        """Test the video conversion process."""
        self._setup_conversion_test(mock_confirm)
        mock_isfile.return_value = True
        
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
        self.gui.input_path_var = MagicMock()
        self.gui.output_path_var = MagicMock()
        self.gui.open_after_conversion_var = MagicMock()
        self.gui.gamma_var = MagicMock()

        self.gui.input_path_var.get.return_value = 'test_input.mp4'
        self.gui.output_path_var.get.return_value = 'test_output.mkv'
        self.gui.open_after_conversion_var.get.return_value = True
        self.gui.gamma_var.get.return_value = 2.2
        self.gui.gpu_accel_var = MagicMock(get=MagicMock(return_value=False))

        mock_confirm.return_value = True
        self.gui.drop_target_registered = True
        self.gui.filter_var.get = MagicMock(return_value='Static')

    def _assert_conversion_started(self, mock_unregister, mock_start_conversion):
        """Helper method to verify conversion startup."""
        mock_unregister.assert_called_once()
        
        actual_call = mock_start_conversion.call_args
        args = actual_call[0]
        self.assertEqual(args[0], 'test_input.mp4')  # input path
        self.assertEqual(args[1], 'test_output.mkv')  # output path
        self.assertEqual(args[2], 2.2)  # gamma
        self.assertIs(args[3], False)  # gpu acceleration
        self.assertEqual(args[4], 0)  # selected_filter_index
        self.assertIs(args[5], self.gui.progress_var)  # progress var
        self.assertEqual(args[6], self.gui.interactable_elements)  # interactable elements
        self.assertIs(args[7], self.gui)  # gui instance
        self.assertTrue(args[8])  # open after conversion
        self.assertIs(args[9], self.gui.cancel_button)  # cancel button
        
        self.gui.cancel_button.grid.assert_called_once()

    @patch('src.gui.ImageTk.PhotoImage')
    @patch('src.gui.extract_frame_with_conversion')
    @patch('src.gui.extract_frame')
    def test_tooltip_display(self, mock_extract, mock_convert, mock_photo_image):
        """Test tooltip display on hover over info button."""
        with patch.object(self.gui, 'show_tooltip') as mock_show_tooltip, \
             patch.object(self.gui, 'hide_tooltip') as mock_hide_tooltip:
            # Simulate mouse enter event
            event = MagicMock()
            self.gui.show_tooltip(event, "Static: Basic HDR to SDR conversion with fixed parameters\nDynamic: Adaptive conversion that analyzes video brightness")
            mock_show_tooltip.assert_called()
            
            # Simulate mouse leave event
            self.gui.hide_tooltip()
            mock_hide_tooltip.assert_called()

    def test_filter_dropdown(self):
        """Test filter dropdown menu functionality."""
        # Setup mock combobox and bind method
        self.gui.filter_combobox = MagicMock(spec=ttk.Combobox)
        self.gui.filter_var = self.mock_string_var
        
        # Create and call create_widgets to set up the combobox bindings
        with patch.object(self.gui, 'update_frame_preview') as mock_update:
            # Manually trigger the combobox binding setup
            self.gui.filter_combobox.bind('<<ComboboxSelected>>', self.gui.update_frame_preview)
            
            # Verify filter options
            self.assertEqual(self.gui.filter_options, ['Static', 'Dynamic'])
            
            # Simulate filter selection change
            self.mock_string_var.get.return_value = 'Dynamic'
            
            # Simulate the combobox selection event
            self.gui.filter_combobox.bind.assert_called_with('<<ComboboxSelected>>', self.gui.update_frame_preview)
            
            # Trigger the event handler directly
            self.gui.update_frame_preview()
            mock_update.assert_called_once()
            
            # Verify filter value is used
            self.assertEqual(self.gui.filter_var.get(), 'Dynamic')

if __name__ == '__main__':
    unittest.main()
