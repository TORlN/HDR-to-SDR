import os
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
        # quality_var/bitrate_var (IntVar) share this same mock during __init__
        # (see the 'int_var' patch below); _apply_quality_mode() now reads
        # quality_var.get() for range-clamping during create_widgets(), so it
        # needs a real numeric value, not the default MagicMock return.
        self.mock_progress_var.get.return_value = 23
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

        # input_path_var needs its own mock (not the shared catch-all, which
        # every StringVar() call aliases to) so its .get() reflects .set().
        # select_file's licensed path (Pro users route through the batch
        # queue, like drag-and-drop already does) reads it back to decide
        # whether the queue's own load already covered this file.
        input_value = ['']
        self.gui.input_path_var = MagicMock()
        self.gui.input_path_var.set.side_effect = lambda v: input_value.__setitem__(0, v)
        self.gui.input_path_var.get.side_effect = lambda: input_value[0]

        self.gui.select_file()

        # Verify file path updates
        self.gui.input_path_var.set.assert_called_once_with('test_input.mp4')
        self.mock_string_var.set.assert_any_call('test_input_sdr.mp4')

        # Verify UI updates
        self._assert_frame_updates()

    @patch('src.gui.ImageTk.PhotoImage')
    @patch('src.preview.extract_frame_with_conversion')
    @patch('src.preview.extract_frame')
    @patch('src.preview.get_video_properties')
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

        # Preview extraction and rendering are now split so the slow ffmpeg work
        # can run on a worker thread. Exercise both halves directly here; the
        # threading orchestration itself is covered in characterization_test.
        self.gui.original_image = None
        self.gui.last_time_position = None
        time_position = 100.0 / 6  # current_frame_index 1 of (total_frames 5 + 1)

        original, converted = self.gui._extract_preview_images(
            'test_input.mp4', time_position, tonemapper='mobius'
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

    @patch('src.gui.messagebox.askyesno')
    @patch('src.gui.HDRConverterGUI.unregister_drop_target')
    @patch('src.gui.conversion_manager.start_conversion')
    @patch('src.gui.os.path.isfile')
    def test_video_conversion_passes_license_tier(self, mock_isfile,
                                                  mock_start_conversion,
                                                  mock_unregister, mock_confirm):
        """convert_video forwards the license tier so the conversion layer can
        apply the DoVi Pro-passthrough / Free-stereo-downmix audio split."""
        self._setup_conversion_test(mock_confirm)
        mock_isfile.return_value = True

        self.gui.convert_video()

        kwargs = mock_start_conversion.call_args.kwargs
        self.assertIs(kwargs.get('licensed'), True)  # setUp builds licensed=True

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

    def _assert_conversion_started(self, mock_unregister, mock_start_conversion):
        """Helper method to verify conversion startup."""
        mock_unregister.assert_called_once()
        
        actual_call = mock_start_conversion.call_args
        args = actual_call[0]
        self.assertEqual(args[0], 'test_input.mp4')  # input path
        self.assertEqual(args[1], 'test_output.mkv')  # output path
        self.assertEqual(args[2], 2.2)  # gamma
        self.assertIs(args[3], False)  # gpu acceleration
        self.assertIs(args[4], self.gui.progress_var)  # progress var
        self.assertEqual(args[5], self.gui.interactable_elements)  # interactable elements
        self.assertIs(args[6], self.gui)  # gui instance
        self.assertTrue(args[7])  # open after conversion
        self.assertIs(args[8], self.gui.cancel_button)  # cancel button
        
        self.gui.cancel_button.grid.assert_called_once()

def _safe_stop(patcher):
    """Stop a patcher; silently ignore if it was already stopped."""
    try:
        patcher.stop()
    except RuntimeError:
        pass


class TestWindowIcon(unittest.TestCase):
    """_set_window_icon should load icon.png and call root.iconphoto."""

    def _make_gui(self, extra_patches=None):
        mock_root = MagicMock()
        patches = {
            'string_var': patch('src.gui.tk.StringVar', return_value=MagicMock(spec=tk.StringVar, get=MagicMock(return_value=''), set=MagicMock())),
            'double_var': patch('src.gui.tk.DoubleVar', return_value=MagicMock(spec=tk.DoubleVar)),
            'bool_var':   patch('src.gui.tk.BooleanVar', return_value=MagicMock(spec=tk.BooleanVar)),
            'int_var':    patch('src.gui.tk.IntVar', return_value=MagicMock(
                spec=tk.IntVar, get=MagicMock(return_value=23), set=MagicMock())),
        }
        if extra_patches:
            patches.update(extra_patches)
        mocks = {name: p.start() for name, p in patches.items()}
        # Guarantee cleanup even when a test fails before its explicit p.stop() calls.
        # _safe_stop ignores double-stop so explicit calls in tests remain harmless.
        for p in patches.values():
            self.addCleanup(_safe_stop, p)
        gui = HDRConverterGUI(mock_root, licensed=True)
        self.addCleanup(gui.on_close)
        return gui, mock_root, patches, mocks

    @patch('src.gui.os.path.exists', return_value=True)
    def test_sets_icon_when_file_exists(self, _mock_exists):
        gui, mock_root, patches, _ = self._make_gui()

        mock_root.iconbitmap.assert_called_once()
        for p in patches.values():
            p.stop()
        gui.on_close()

    @patch('src.gui.os.path.exists', return_value=False)
    def test_skips_when_file_missing(self, _mock_exists):
        gui, mock_root, patches, _ = self._make_gui()

        mock_root.iconbitmap.assert_not_called()
        for p in patches.values():
            p.stop()
        gui.on_close()

    @patch('src.gui.os.path.exists', return_value=True)
    def test_swallows_exceptions(self, _mock_exists):
        # root.iconbitmap raises; must not propagate
        gui, mock_root, patches, _ = self._make_gui()
        mock_root.iconbitmap.side_effect = Exception("bad icon")

        try:
            gui._set_window_icon()
        except Exception:
            self.fail("_set_window_icon must not raise")

        for p in patches.values():
            p.stop()
        gui.on_close()

    def test_pyinstaller_uses_meipass_dir(self):
        """PyInstaller onedir sets sys._MEIPASS to _internal/; icon.ico lives there."""
        import sys as _sys
        fake_meipass = r'C:\fake_install\HDR_to_SDR_Converter\_internal'
        fake_exe = r'C:\fake_install\HDR_to_SDR_Converter\HDR_to_SDR_Converter.exe'
        with patch.object(_sys, 'frozen', True, create=True), \
             patch.object(_sys, 'executable', fake_exe), \
             patch.object(_sys, '_MEIPASS', fake_meipass, create=True), \
             patch('src.gui.os.path.exists', return_value=True):
            gui, mock_root, patches, _ = self._make_gui()
        call_args = mock_root.iconbitmap.call_args
        self.assertIsNotNone(call_args, "iconbitmap should have been called")
        icon_path = str(call_args[0][0])
        self.assertIn('_internal', icon_path)
        for p in patches.values():
            p.stop()
        gui.on_close()

    def test_nuitka_exe_uses_executable_dir(self):
        """Nuitka does not set sys.frozen; must still use sys.executable dir when exe is not python.exe."""
        import sys as _sys
        # Build an absolute path using os.sep so the path is valid on both Linux and Windows.
        # A Windows-style r'C:\...' string is treated as a relative path on Linux, causing
        # os.path.dirname(os.path.abspath(...)) to return the repo root instead of fake_nuitka.
        fake_exe = os.path.join(os.sep, 'fake_nuitka', 'HDR_to_SDR_Converter.exe')
        with patch('src.gui.os.path.exists', return_value=True), \
             patch.object(_sys, 'executable', fake_exe):
            gui, mock_root, patches, _ = self._make_gui()
        call_args = mock_root.iconbitmap.call_args
        self.assertIsNotNone(call_args, "iconbitmap should have been called")
        icon_path = str(call_args[0][0])
        self.assertIn('fake_nuitka', icon_path)
        for p in patches.values():
            p.stop()
        gui.on_close()

    def test_icon_deferred_via_after(self):
        """Icon must be re-applied via after(0,...) so it takes effect on the visible window."""
        with patch('src.gui.os.path.exists', return_value=True):
            gui, mock_root, patches, _ = self._make_gui()
        after_calls = [c for c in mock_root.after.call_args_list
                       if c[0][0] == 0 and c[0][1] == gui._set_window_icon]
        self.assertTrue(after_calls, "after(0, _set_window_icon) must be scheduled in __init__")
        for p in patches.values():
            p.stop()
        gui.on_close()


class TestBatchCancel(TestCase):
    """Cancelling mid-batch must stop the queue, not advance to the next file."""

    def setUp(self):
        self.mock_root = MagicMock()
        patches = {
            'string_var': patch('src.gui.tk.StringVar', return_value=MagicMock(
                spec=tk.StringVar, get=MagicMock(return_value=''), set=MagicMock())),
            'double_var': patch('src.gui.tk.DoubleVar', return_value=MagicMock(spec=tk.DoubleVar)),
            'bool_var':   patch('src.gui.tk.BooleanVar', return_value=MagicMock(spec=tk.BooleanVar)),
            'int_var':    patch('src.gui.tk.IntVar', return_value=MagicMock(
                spec=tk.IntVar, get=MagicMock(return_value=23), set=MagicMock())),
        }
        self.patches = patches
        self.mocks = {name: p.start() for name, p in patches.items()}
        self.gui = HDRConverterGUI(self.mock_root, licensed=True)
        self.addCleanup(self._teardown)

    def _teardown(self):
        for p in self.patches.values():
            try:
                p.stop()
            except RuntimeError:
                pass
        self.gui.on_close()

    @patch('src.batch.conversion_manager')
    def test_cancel_does_not_advance_queue(self, mock_cm):
        """_on_batch_item_complete must not call _start_next_batch_item when cancelled."""
        mock_cm.cancelled = True
        self.gui.batch_items = [
            {'input': '/a.mkv', 'output': '/a_sdr.mkv', 'format': 'MKV', 'status': 'Converting'},
            {'input': '/b.mkv', 'output': '/b_sdr.mkv', 'format': 'MKV', 'status': 'Pending'},
        ]
        self.gui._current_batch_item = self.gui.batch_items[0]

        with patch.object(self.gui, '_start_next_batch_item') as mock_next:
            self.gui._on_batch_item_complete(success=False)

        mock_next.assert_not_called()
        self.assertEqual(self.gui.batch_items[0]['status'], 'Failed')

    @patch('src.batch.conversion_manager')
    def test_not_cancelled_still_advances_queue(self, mock_cm):
        """Normal completion (not cancelled) must still call _start_next_batch_item."""
        mock_cm.cancelled = False
        self.gui.batch_items = [
            {'input': '/a.mkv', 'output': '/a_sdr.mkv', 'format': 'MKV', 'status': 'Converting'},
            {'input': '/b.mkv', 'output': '/b_sdr.mkv', 'format': 'MKV', 'status': 'Pending'},
        ]
        self.gui._current_batch_item = self.gui.batch_items[0]

        with patch.object(self.gui, '_start_next_batch_item') as mock_next:
            self.gui._on_batch_item_complete(success=True)

        mock_next.assert_called_once()
        self.assertEqual(self.gui.batch_items[0]['status'], 'Done')


class TestShowTooltip(TestCase):
    """show_tooltip must not call bbox('insert') — that raises TclError on ttk.Label."""

    def setUp(self):
        self.mock_root = MagicMock()
        patches = {
            'string_var': patch('src.gui.tk.StringVar', return_value=MagicMock(
                spec=tk.StringVar, get=MagicMock(return_value=''), set=MagicMock())),
            'double_var': patch('src.gui.tk.DoubleVar', return_value=MagicMock(spec=tk.DoubleVar)),
            'bool_var':   patch('src.gui.tk.BooleanVar', return_value=MagicMock(spec=tk.BooleanVar)),
            'int_var':    patch('src.gui.tk.IntVar', return_value=MagicMock(
                spec=tk.IntVar, get=MagicMock(return_value=23), set=MagicMock())),
        }
        self.patches = patches
        self.mocks = {name: p.start() for name, p in patches.items()}
        self.gui = HDRConverterGUI(self.mock_root, licensed=True)
        self.addCleanup(self._teardown)

    def _teardown(self):
        for p in self.patches.values():
            try:
                p.stop()
            except RuntimeError:
                pass
        self.gui.on_close()

    @patch('src.gui.tk.Toplevel')
    def test_does_not_call_bbox(self, mock_toplevel):
        """Position must come from winfo_rootx/y, never from bbox('insert')."""
        mock_widget = MagicMock()
        mock_widget.winfo_rootx.return_value = 100
        mock_widget.winfo_rooty.return_value = 200
        mock_widget.bbox.return_value = (0, 0, 10, 10)  # valid return so test fails on assert, not TypeError
        mock_event = MagicMock()
        mock_event.widget = mock_widget

        self.gui.tooltip = None
        self.gui.show_tooltip(mock_event, "hello")

        mock_widget.bbox.assert_not_called()


class TestBuildInfoTextMaxNits(TestCase):
    """_build_info_text must include Max Nits for HDR content but not for SDR."""

    def _props(self, primaries='bt2020', transfer='smpte2084'):
        return {
            'width': 3840, 'height': 2160,
            'frame_rate': 23.976,
            'codec_name': 'hevc',
            'audio_codec': 'eac3',
            'color_primaries': primaries,
            'color_transfer': transfer,
        }

    def test_hdr_with_maxcll_shows_value(self):
        """HDR video with MaxCLL metadata should show 'Max Nits: 1000' in the strip."""
        text = HDRConverterGUI._build_info_text(self._props(), maxcll=1000.0)
        self.assertIn('Max Nits: 1000', text)

    def test_hdr_without_maxcll_shows_na(self):
        """HDR video with no embedded MaxCLL (None) should show 'Max Nits: N/A'."""
        text = HDRConverterGUI._build_info_text(self._props(), maxcll=None)
        self.assertIn('Max Nits: N/A', text)

    def test_sdr_never_shows_maxcll(self):
        """SDR video should never show Max Nits even if a value is passed."""
        text = HDRConverterGUI._build_info_text(
            self._props(primaries='bt709', transfer='bt709'), maxcll=1000.0)
        self.assertNotIn('Max Nits', text)

    def test_maxcll_integer_display(self):
        """Max Nits value should be shown as an integer (no decimals)."""
        text = HDRConverterGUI._build_info_text(self._props(), maxcll=1000.0)
        self.assertIn('Max Nits: 1000', text)
        self.assertNotIn('1000.0', text)


class TestBuildInfoTextBitrate(TestCase):
    """The info strip shows the probed source bitrate when known, omitted
    entirely when it isn't (matching the existing DoVi-tag omission pattern)."""

    def _props(self, bit_rate=None, total_bit_rate=None):
        props = {
            'width': 3840, 'height': 2160,
            'frame_rate': 23.976,
            'codec_name': 'hevc',
            'audio_codec': 'eac3',
            'color_primaries': 'bt2020',
            'color_transfer': 'smpte2084',
        }
        if bit_rate is not None:
            props['bit_rate'] = bit_rate
        if total_bit_rate is not None:
            props['total_bit_rate'] = total_bit_rate
        return props

    def test_shows_formatted_bitrate_before_audio(self):
        text = HDRConverterGUI._build_info_text(self._props(bit_rate=84_376_000), maxcll=1000.0)
        self.assertIn('Bitrate: 84,376 kbps | Audio: EAC3', text)

    def test_omits_bitrate_segment_when_zero(self):
        text = HDRConverterGUI._build_info_text(self._props(bit_rate=0), maxcll=1000.0)
        self.assertNotIn('Bitrate', text)

    def test_omits_bitrate_segment_when_missing(self):
        text = HDRConverterGUI._build_info_text(self._props(), maxcll=1000.0)
        self.assertNotIn('Bitrate', text)

    def test_estimated_bitrate_shows_tilde_prefix(self):
        """Bitrate derived from format.bit_rate (Matroska with no per-stream
        reading) is video+audio+overhead combined, not exact -- mark it so
        it reads differently from a real per-stream figure."""
        props = self._props(bit_rate=28_424_731)
        props['bit_rate_estimated'] = True
        text = HDRConverterGUI._build_info_text(props, maxcll=1000.0)
        self.assertIn('Bitrate: ~28,424 kbps', text)

    def test_real_bitrate_has_no_tilde_prefix(self):
        text = HDRConverterGUI._build_info_text(
            self._props(bit_rate=84_376_000), maxcll=1000.0)
        self.assertIn('Bitrate: 84,376 kbps', text)
        self.assertNotIn('~', text)

    def test_prefers_total_bit_rate_over_video_only_bit_rate(self):
        """The info strip is what users compare against Windows Explorer's
        Properties -> Details "Total bitrate" (video+audio) -- it must show
        total_bit_rate, not the video-only bit_rate used for the Target
        Bitrate slider's ceiling, whenever both are present."""
        text = HDRConverterGUI._build_info_text(
            self._props(bit_rate=47_358_389, total_bit_rate=47_547_461),
            maxcll=1000.0)
        self.assertIn('Bitrate: 47,547 kbps', text)
        self.assertNotIn('47,358', text)


class TestBuildInfoTextOutputBitDepth(TestCase):
    """The info strip shows "{source}-bit -> {output}-bit" whenever the actual
    resolved output (passed in directly as *bit_depth* -- the live 10/12-bit
    toggle choice above a 10-bit source, or the automatic 8/10-bit choice
    otherwise) differs from the source depth, so the conversion is visible at
    a glance. When they match, it's just "{N}-bit" -- no redundant arrow. An
    unlicensed user whose >10-bit source got capped to 10 (rather than a
    licensed user's own 10-bit toggle choice) gets a "(Pro Only)" suffix,
    since that's specifically the license capping it, not their choice."""

    def _props(self, bit_depth):
        return {
            'width': 3840, 'height': 2160,
            'frame_rate': 23.976,
            'codec_name': 'hevc',
            'audio_codec': 'eac3',
            'color_primaries': 'bt2020',
            'color_transfer': 'smpte2084',
            'bit_depth': bit_depth,
        }

    def test_shows_plain_eight_bit_when_source_matches_output(self):
        text = HDRConverterGUI._build_info_text(
            self._props(8), maxcll=1000.0, bit_depth=8, licensed=True)
        self.assertIn('8-bit', text)
        self.assertNotIn('->', text)
        self.assertNotIn('Pro', text)

    def test_shows_plain_ten_bit_when_source_matches_output(self):
        text = HDRConverterGUI._build_info_text(
            self._props(10), maxcll=1000.0, bit_depth=10, licensed=True)
        self.assertIn('10-bit', text)
        self.assertNotIn('->', text)

    def test_shows_plain_twelve_bit_when_source_matches_output(self):
        text = HDRConverterGUI._build_info_text(
            self._props(12), maxcll=1000.0, bit_depth=12, licensed=True)
        self.assertIn('12-bit', text)
        self.assertNotIn('->', text)
        self.assertNotIn('Pro', text)

    def test_arrow_for_nine_bit_source_rounded_up_to_ten(self):
        text = HDRConverterGUI._build_info_text(
            self._props(9), maxcll=1000.0, bit_depth=10, licensed=True)
        self.assertIn('9-bit -> 10-bit', text)

    def test_licensed_high_bit_depth_source_shows_arrow_without_pro_suffix(self):
        """Defaulting to 10-bit is the user's own toggle position (they could
        pick 12-bit any time), not a license restriction -- no Pro suffix."""
        text = HDRConverterGUI._build_info_text(
            self._props(12), maxcll=1000.0, bit_depth=10, licensed=True)
        self.assertIn('12-bit -> 10-bit', text)
        self.assertNotIn('Pro', text)

    def test_unlicensed_high_bit_depth_source_shows_arrow_with_pro_only_suffix(self):
        text = HDRConverterGUI._build_info_text(
            self._props(12), maxcll=1000.0, bit_depth=10, licensed=False)
        self.assertIn('12-bit -> 10-bit (Pro Only)', text)

    def test_unlicensed_sixteen_bit_source_shows_arrow_with_pro_only_suffix(self):
        text = HDRConverterGUI._build_info_text(
            self._props(16), maxcll=1000.0, bit_depth=10, licensed=False)
        self.assertIn('16-bit -> 10-bit (Pro Only)', text)

    def test_licensed_sixteen_bit_source_at_max_twelve_bit_has_no_pro_suffix(self):
        """Even Pro's ceiling is 12-bit -- a 16-bit source still gets
        downsampled, but that's a codec limit, not a license restriction."""
        text = HDRConverterGUI._build_info_text(
            self._props(16), maxcll=1000.0, bit_depth=12, licensed=True)
        self.assertIn('16-bit -> 12-bit', text)
        self.assertNotIn('Pro', text)

    def test_unlicensed_ten_bit_source_has_no_pro_suffix(self):
        """The Pro suffix is only for sources that could actually use 12-bit."""
        text = HDRConverterGUI._build_info_text(
            self._props(10), maxcll=1000.0, bit_depth=10, licensed=False)
        self.assertNotIn('Pro', text)
        self.assertNotIn('->', text)

    def test_missing_bit_depth_defaults_to_8bit_output(self):
        """Older probes / mocks without a bit_depth key must not crash."""
        props = self._props(8)
        del props['bit_depth']
        text = HDRConverterGUI._build_info_text(props, maxcll=1000.0, bit_depth=8, licensed=True)
        self.assertIn('8-bit', text)
        self.assertNotIn('->', text)


class TestDolbyVisionInfoTextFormatting(TestCase):
    """Dolby Vision detection surfaces as its own '|'-separated segment inside
    the fps/resolution info bar (no separate badge widget), and is omitted
    entirely for non-DoVi sources, including plain HDR10.

    Covers _build_info_text's string formatting only; the info-label update
    flow (_update_info_label against a real widget) is covered end-to-end by
    TestDolbyVisionInfoBarTag in gui_integration_test.py."""

    @staticmethod
    def _props(dovi=True):
        return {
            'width': 3840, 'height': 2160, 'frame_rate': 23.976,
            'codec_name': 'hevc', 'audio_codec': 'truehd',
            'color_primaries': 'bt2020', 'color_transfer': 'smpte2084',
            'bit_depth': 10,
            'is_dolby_vision': dovi, 'dovi_profile': 8 if dovi else None,
        }

    def test_tag_shown_for_dovi_source(self):
        text = HDRConverterGUI._build_info_text(self._props(True), maxcll=1000.0)
        self.assertIn('Dolby Vision', text)

    def test_tag_sits_between_codec_and_hdr_tag(self):
        text = HDRConverterGUI._build_info_text(self._props(True), maxcll=1000.0)
        self.assertIn('HEVC | Dolby Vision | HDR', text)

    def test_no_tag_for_plain_hdr10_source(self):
        text = HDRConverterGUI._build_info_text(self._props(False), maxcll=1000.0)
        self.assertNotIn('Dolby Vision', text)

    def test_no_tag_when_keys_absent(self):
        """Older probes without the DoVi keys must not crash or add the tag."""
        props = self._props(False)
        del props['is_dolby_vision']
        del props['dovi_profile']
        text = HDRConverterGUI._build_info_text(props, maxcll=1000.0)
        self.assertNotIn('Dolby Vision', text)


if __name__ == '__main__':
    unittest.main()
