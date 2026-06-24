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
            'bool_var': patch('src.gui.tk.BooleanVar', return_value=self.mock_bool_var),
            # quality_var is an IntVar; mock it too (the dark theme no longer
            # creates a hidden default root that used to satisfy real Var creation).
            'int_var': patch('src.gui.tk.IntVar', return_value=self.mock_progress_var),
        }

        # Combine all patches
        self.patches = {**tk_patches, **gui_patches}
        
        # Start all patches
        self.mocks = {name: patcher.start() for name, patcher in self.patches.items()}
        
        # Setup mock root
        self.mock_root = MagicMock()
        self.mocks['root'].return_value = self.mock_root

        # Initialize GUI
        self.gui = HDRConverterGUI(self.mock_root, licensed=True)
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

        # Give these their own mocks (the suite shares one mock for all StringVars,
        # which would otherwise interleave format_var.set calls with input/output).
        self.gui.format_var = MagicMock(get=MagicMock(return_value='MKV'))
        self.gui.quality_var = MagicMock(get=MagicMock(return_value=23))
        self.gui.custom_time_var = MagicMock(get=MagicMock(return_value=''))

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

    @patch('src.gui.filedialog.askopenfilename')
    def test_file_selection_webm_redirects_output_to_mkv(self, mock_file_dialog):
        """A WebM input must produce an MKV output path (H.264 can't go in WebM)."""
        mock_file_dialog.return_value = 'clip.webm'

        self.gui.select_file()

        self.mock_string_var.set.assert_has_calls([
            call('clip.webm'),
            call('clip_sdr.mkv')
        ])

    @patch('src.gui.ImageTk.PhotoImage')
    @patch('src.gui.extract_frame_with_conversion')
    @patch('src.gui.extract_frame')
    @patch('src.gui.get_video_properties')
    def test_frame_preview_update(self, mock_get_properties, mock_extract, mock_convert, mock_photo_image):
        """Test frame preview update functionality."""
        # Setup mock video properties
        mock_get_properties.return_value = {'duration': 100.0}
        
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
        self.gui.tonemap_var = MagicMock(get=MagicMock(return_value='Mobius'))  # Add tonemapper mock

        # Setup GUI elements
        self.gui.original_image_label = MagicMock()
        self.gui.converted_image_label = MagicMock()
        self.gui.original_title_label = MagicMock()
        self.gui.converted_title_label = MagicMock()

        # Mock adjust_gamma method
        self.gui.adjust_gamma = MagicMock(return_value=mock_image)

        # Set the filter_var to return a valid filter option
        self.mock_string_var.get.return_value = 'Static'

        # Preview extraction and rendering are now split so the slow ffmpeg work
        # can run on a worker thread. Exercise both halves directly here; the
        # threading orchestration itself is covered in characterization_test.
        self.gui.original_image = None
        self.gui.last_time_position = None
        time_position = 100.0 / 6  # current_frame_index 1 of (total_frames 5 + 1)

        original, converted = self.gui._extract_preview_images(
            'test_input.mp4', time_position, filter_index=0, tonemapper='mobius'
        )

        # Get the actual calls made to mock_extract and mock_convert
        extract_call = mock_extract.call_args
        convert_call = mock_convert.call_args

        # Verify the calls were made
        self.assertEqual(mock_extract.call_count, 1)
        self.assertEqual(mock_convert.call_count, 1)

        # Verify the positional arguments
        self.assertEqual(extract_call[0][0], 'test_input.mp4')
        self.assertEqual(convert_call[0][0], 'test_input.mp4')

        # Verify the time_position with approximate equality
        self.assertAlmostEqual(extract_call[1]['time_position'], 16.666666666666668, places=10)
        self.assertAlmostEqual(convert_call[1]['time_position'], 16.666666666666668, places=10)

        # Verify other convert_call arguments
        self.assertEqual(convert_call[1]['gamma'], 1.0)
        self.assertEqual(convert_call[1]['filter_index'], 0)
        self.assertEqual(convert_call[1]['tonemapper'], 'mobius')

        # Render the extracted frames (main-thread Tk work). Past the first
        # reveal so it uses the live-geometry path (mocked frame -> native size).
        self.gui._window_auto_fitted = True
        self.gui._render_preview_images(original, converted, time_position)

        # Verify adjust_gamma is called with correct gamma value
        self.gui.adjust_gamma.assert_called_once_with(mock_image, 2.2)

        # Verify image resize calls
        mock_image.resize.assert_has_calls([
            call((3840, 2160), Image.LANCZOS),
            call((3840, 2160), Image.LANCZOS)
        ])

        # Verify PhotoImage creation and label updates
        mock_photo_image.assert_has_calls([call(mock_image), call(mock_image)])
        self.gui.original_image_label.config.assert_called_with(image=mock_photo)
        self.gui.converted_image_label.config.assert_called_with(image=mock_photo)

    def test_preview_size_constant_is_4k(self):
        """PREVIEW_SIZE must be 4K so ffmpeg renders at the highest useful resolution."""
        from src.gui import PREVIEW_SIZE
        self.assertEqual(PREVIEW_SIZE, (3840, 2160),
                         f"PREVIEW_SIZE should be (3840, 2160) but got {PREVIEW_SIZE}")

    @patch('src.gui.ImageTk.PhotoImage')
    def test_render_preview_images_correct_size_when_frame_collapsed(self, mock_photo_image):
        """Frame buttons must render at a usable size even when image_frame height
        is below _PREVIEW_HEIGHT_RESERVE (as happens during loading: the spinner is
        the only committed geometry so the frame is short)."""
        from src.gui import _PREVIEW_HEIGHT_RESERVE, _MIN_PANE_W
        # Simulate a wide window whose image_frame height is below the reserve
        # because the loading indicator is still the committed geometry.
        self.gui.image_frame.winfo_width.return_value = 1380
        self.gui.image_frame.winfo_height.return_value = _PREVIEW_HEIGHT_RESERVE - 20  # collapsed

        mock_image = MagicMock(spec=Image.Image)
        mock_image.resize.return_value = mock_image
        mock_photo_image.return_value = MagicMock()
        self.gui.adjust_gamma = MagicMock(return_value=mock_image)
        self.gui.original_image_label = MagicMock()
        self.gui.converted_image_label = MagicMock()
        self.gui.original_title_label = MagicMock()
        self.gui.converted_title_label = MagicMock()

        self.gui._window_auto_fitted = True
        self.gui._render_preview_images(mock_image, mock_image, 10.0)

        # The rendered size must not be degenerate; width ≥ _MIN_PANE_W and
        # height ≥ 100 (not collapsed to a 1×1 sliver).
        resize_calls = mock_image.resize.call_args_list
        self.assertGreater(len(resize_calls), 0)
        used_size = resize_calls[0][0][0]
        self.assertGreaterEqual(used_size[0], _MIN_PANE_W,
                                f"Rendered width {used_size[0]} < _MIN_PANE_W={_MIN_PANE_W}")
        self.assertGreaterEqual(used_size[1], 100,
                                f"Rendered height {used_size[1]} is too small (collapsed frame bug)")

    @patch('src.gui.ImageTk.PhotoImage')
    def test_render_preview_reuses_previous_size_when_frame_height_constrains(self, mock_photo_image):
        """Clicking a frame button while a render is in progress must not shrink
        the images.  image_frame fills the root vertically (weight=1), so its
        winfo_height() during loading equals the full row-1 height (~380 px), NOT
        just the spinner.  That makes avail_h well below the image height and
        _fit_preview_pane would downscale the images.  The fix: reuse the
        previously rendered size (_preview_render_size) instead of recomputing."""
        prev_size = (640, 360)
        self.gui._preview_render_size = prev_size  # set by last successful render

        # Simulate a realistic window: wide, and frame_h is the full row-1 height
        # (much larger than the loading-indicator but still constraining).
        self.gui.image_frame.winfo_width.return_value = 1380
        self.gui.image_frame.winfo_height.return_value = 380  # realistic full height

        mock_image = MagicMock(spec=Image.Image)
        mock_image.resize.return_value = mock_image
        mock_photo_image.return_value = MagicMock()
        self.gui.adjust_gamma = MagicMock(return_value=mock_image)
        self.gui.original_image_label = MagicMock()
        self.gui.converted_image_label = MagicMock()
        self.gui.original_title_label = MagicMock()
        self.gui.converted_title_label = MagicMock()

        self.gui._window_auto_fitted = True
        self.gui._render_preview_images(mock_image, mock_image, 10.0)

        resize_calls = mock_image.resize.call_args_list
        self.assertGreater(len(resize_calls), 0)
        used_size = resize_calls[0][0][0]
        self.assertEqual(used_size, prev_size,
                         f"Expected previous render size {prev_size}, got {used_size}")

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
        self.gui.disable_ui(test_elements)
        for element in test_elements:
            element.config.assert_called_with(state='disabled')

    def _assert_frame_updates(self):
        """Helper method to verify frame updates."""
        for frame in [self.gui.button_frame, self.gui.image_frame, 
                     self.gui.action_frame]:
            frame.grid.assert_called_once()
        self.gui.update_frame_preview.assert_called_once()

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

if __name__ == '__main__':
    unittest.main()
