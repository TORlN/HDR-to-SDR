"""Characterization tests.

These lock in the *current* behavior of code paths that the planned
responsiveness refactor will touch. They are intentionally written to pass
against the code as it exists today, so that any behavior change during
refactoring shows up as a failing test rather than a silent regression.
"""
import sys
import os
import json
import threading
import unittest
from unittest.mock import patch, MagicMock, ANY, call

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from PIL import Image

from src.conversion import ConversionManager
from src.utils import (
    get_maxfall,
    get_video_properties,
    extract_frame_with_conversion,
    FFMPEG_FILTER,
)
from src.gui import HDRConverterGUI, DEFAULT_MIN_SIZE, PREVIEW_SIZE


def _bare_gui():
    """An HDRConverterGUI with __init__ bypassed (no live Tk root needed)."""
    return object.__new__(HDRConverterGUI)

# A minimal valid 1x1 PNG, reused from the existing util tests.
VALID_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
    b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
    b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82'
)

PROPS = {
    "width": 1920, "height": 1080, "bit_rate": 4000000,
    "codec_name": "h264", "frame_rate": 30.0, "audio_codec": "aac",
    "audio_bit_rate": 128000, "duration": 90.0, "subtitle_streams": [],
}


class TestAdjustGamma(unittest.TestCase):
    """adjust_gamma runs on every gamma-slider drag; characterize it as pure."""

    def _gui(self):
        # Bypass __init__ (which needs a live Tk root); adjust_gamma only
        # uses its arguments, not instance state.
        return object.__new__(HDRConverterGUI)

    def test_gamma_one_is_identity(self):
        gui = self._gui()
        img = Image.new('RGB', (2, 2), (100, 150, 200))
        out = gui.adjust_gamma(img, 1.0)
        self.assertEqual(list(out.getdata()), list(img.getdata()))

    def test_gamma_one_short_circuits_to_same_object(self):
        # gamma == 1.0 must skip the LUT pass entirely (returns the input image).
        gui = self._gui()
        img = Image.new('RGB', (2, 2), (10, 20, 30))
        self.assertIs(gui.adjust_gamma(img, 1.0), img)

    def test_gamma_above_one_brightens_midtone(self):
        gui = self._gui()
        img = Image.new('L', (1, 1), 128)
        out = gui.adjust_gamma(img, 2.0)
        self.assertGreater(out.getpixel((0, 0)), 128)

    def test_gamma_below_one_darkens_midtone(self):
        gui = self._gui()
        img = Image.new('L', (1, 1), 128)
        out = gui.adjust_gamma(img, 0.5)
        self.assertLess(out.getpixel((0, 0)), 128)


class TestGetMaxfall(unittest.TestCase):

    def setUp(self):
        # MAXFALL is memoized per path; reset so each test probes fresh.
        from src.utils import clear_maxfall_cache
        clear_maxfall_cache()

    @patch('src.utils.subprocess.check_output')
    def test_reads_max_fall_from_mastering_metadata(self, mock_out):
        data = {"frames": [{"side_data_list": [
            {"side_data_type": "Mastering display metadata", "max_fall": 400}
        ]}]}
        mock_out.return_value = json.dumps(data).encode('utf-8')
        self.assertEqual(get_maxfall('video.mkv'), 400.0)

    @patch('src.utils.subprocess.check_output')
    def test_defaults_to_100_when_absent(self, mock_out):
        mock_out.return_value = json.dumps({"frames": []}).encode('utf-8')
        self.assertEqual(get_maxfall('video.mkv'), 100)


class TestGetMaxfallCaching(unittest.TestCase):
    """MAXFALL is static mastering-display metadata; probing it costs ~0.5-1.2s,
    so it must be computed once per path and reused across previews."""

    def setUp(self):
        from src.utils import clear_maxfall_cache
        clear_maxfall_cache()

    @patch('src.utils.subprocess.check_output')
    def test_repeated_calls_probe_once(self, mock_out):
        mock_out.return_value = json.dumps(
            {"frames": [{"side_data_list": [
                {"side_data_type": "Mastering display metadata", "max_fall": 250}
            ]}]}).encode('utf-8')
        first = get_maxfall('a.mkv')
        second = get_maxfall('a.mkv')
        self.assertEqual(first, 250.0)
        self.assertEqual(second, 250.0)
        self.assertEqual(mock_out.call_count, 1)  # second call served from cache

    @patch('src.utils.subprocess.check_output')
    def test_distinct_paths_probe_separately(self, mock_out):
        mock_out.return_value = json.dumps({"frames": []}).encode('utf-8')
        get_maxfall('a.mkv')
        get_maxfall('b.mkv')
        self.assertEqual(mock_out.call_count, 2)

    @patch('src.utils.subprocess.check_output')
    def test_clear_cache_forces_reprobe(self, mock_out):
        from src.utils import clear_maxfall_cache
        mock_out.return_value = json.dumps({"frames": []}).encode('utf-8')
        get_maxfall('a.mkv')
        clear_maxfall_cache()
        get_maxfall('a.mkv')
        self.assertEqual(mock_out.call_count, 2)


class TestGetVideoPropertiesFailure(unittest.TestCase):

    @patch('src.utils.subprocess.Popen')
    def test_returns_none_when_ffprobe_fails(self, mock_popen):
        proc = mock_popen.return_value
        proc.communicate.return_value = (b'', b'boom')
        proc.returncode = 1
        self.assertIsNone(get_video_properties('missing.mp4'))


class TestExtractFrameStaticFilter(unittest.TestCase):

    @patch('src.utils.get_maxfall')
    @patch('src.utils.run_ffmpeg_command', return_value=VALID_PNG)
    @patch('src.utils.get_video_properties', return_value=PROPS)
    def test_static_filter_skips_maxfall_and_omits_npl(self, mock_props, mock_run, mock_maxfall):
        img = extract_frame_with_conversion(
            'in.mp4', gamma=2.2, filter_index=0, tonemapper='hable'
        )
        self.assertIsInstance(img, Image.Image)
        # Static filter must not probe MAXFALL.
        mock_maxfall.assert_not_called()
        args = mock_run.call_args[0][0]
        vf = args[args.index('-vf') + 1]
        self.assertIn('tonemap=hable', vf)
        self.assertIn('scale=iw:ih', vf)
        self.assertNotIn('npl', vf)


class TestMonitorProgress(unittest.TestCase):

    def _gui(self):
        gui = MagicMock()
        # Run scheduled Tk callbacks immediately and synchronously.
        gui.root.after = MagicMock(side_effect=lambda delay, func, *a: func())
        return gui

    @patch('src.conversion.messagebox')
    def test_progress_is_parsed_and_pushed_to_progress_var(self, _mb):
        manager = ConversionManager()
        manager.use_gpu = False
        manager.cancelled = False
        proc = MagicMock()
        proc.stderr = iter(['frame=1 time=00:00:45.00 bitrate=1'])
        proc.returncode = 0
        manager.process = proc
        manager.handle_completion = MagicMock()

        progress_var = MagicMock()
        gui = self._gui()

        manager.monitor_progress(
            progress_var, duration=90.0, gui_instance=gui,
            interactable_elements=[], cancel_button=MagicMock(),
            output_path='out.mkv', open_after_conversion=False, gamma=1.0,
        )

        # 45s of 90s == 50%.
        progress_var.set.assert_any_call(50.0)
        manager.handle_completion.assert_called_once()

    @patch('src.conversion.messagebox')
    def test_gpu_error_triggers_cpu_retry(self, _mb):
        manager = ConversionManager()
        manager.use_gpu = True
        manager.cancelled = False
        proc = MagicMock()
        proc.stderr = iter(['Cannot load nvcuda.dll', 'cuda failure'])
        proc.returncode = 1
        manager.process = proc
        manager.start_conversion = MagicMock()

        gui = self._gui()
        gui.input_path_var.get.return_value = 'in.mp4'
        gui.output_path_var.get.return_value = 'out.mkv'
        gui.filter_var.get.return_value = 'Static'

        manager.monitor_progress(
            MagicMock(), duration=90.0, gui_instance=gui,
            interactable_elements=[], cancel_button=MagicMock(),
            output_path='out.mkv', open_after_conversion=False, gamma=1.0,
            tonemapper='hable',
        )

        manager.start_conversion.assert_called_once()
        self.assertIs(manager.start_conversion.call_args.kwargs['use_gpu'], False)
        # The retry preserves the user's tonemapper (was previously lost).
        self.assertEqual(manager.start_conversion.call_args.kwargs['tonemapper'], 'hable')
        gui.gpu_accel_var.set.assert_called_once_with(False)

    @patch('src.conversion.messagebox')
    def test_cancelled_run_does_not_retry_on_gpu_error(self, _mb):
        manager = ConversionManager()
        manager.use_gpu = True
        manager.cancelled = True  # user cancelled
        proc = MagicMock()
        proc.stderr = iter(['cuda failure'])
        proc.returncode = 1
        manager.process = proc
        manager.start_conversion = MagicMock()
        manager.handle_completion = MagicMock()

        gui = self._gui()

        manager.monitor_progress(
            MagicMock(), duration=90.0, gui_instance=gui,
            interactable_elements=[], cancel_button=MagicMock(),
            output_path='out.mkv', open_after_conversion=False, gamma=1.0,
        )

        manager.start_conversion.assert_not_called()
        manager.handle_completion.assert_called_once()


class TestPreviewWorkerThread(unittest.TestCase):
    """The preview now extracts frames off the Tk main thread (responsiveness fix)."""

    def test_extraction_runs_off_main_thread_then_schedules_render(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.original_image = None
        gui.last_time_position = None
        gui.filter_options = ['Static', 'Dynamic']
        gui.filter_var = MagicMock()
        gui.filter_var.get.return_value = 'Static'
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'

        seen = {}

        def fake_extract(video_path, time_position, filter_index, tonemapper):
            seen['thread'] = threading.current_thread()
            seen['time_position'] = time_position
            seen['filter_index'] = filter_index
            seen['tonemapper'] = tonemapper
            return ('original', 'converted')

        gui._extract_preview_images = fake_extract
        gui._render_preview_images = MagicMock()

        with patch('src.gui.get_video_properties', return_value={'duration': 100.0}):
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        # Extraction happened on a worker thread, not the main thread.
        self.assertIsNotNone(seen.get('thread'))
        self.assertIsNot(seen['thread'], threading.main_thread())
        # Tk-owned values were read and forwarded correctly.
        self.assertAlmostEqual(seen['time_position'], 100.0 / 6)
        self.assertEqual(seen['filter_index'], 0)
        self.assertEqual(seen['tonemapper'], 'mobius')

        # Rendering is marshalled back onto the main thread via root.after(0, ...).
        gui.root.after.assert_called_once()
        delay, callback = gui.root.after.call_args[0][0], gui.root.after.call_args[0][1]
        self.assertEqual(delay, 0)
        callback()
        # 4th arg is the debounce generation token.
        gui._render_preview_images.assert_called_once_with('original', 'converted', ANY, ANY)

    def test_superseded_worker_does_not_schedule_render(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.original_image = None
        gui.last_time_position = None
        gui.filter_options = ['Static', 'Dynamic']
        gui.filter_var = MagicMock()
        gui.filter_var.get.return_value = 'Static'
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'
        gui._render_preview_images = MagicMock()

        def fake_extract(*a, **k):
            # Simulate a newer request arriving while this worker was extracting.
            gui._preview_generation += 1
            return ('original', 'converted')

        gui._extract_preview_images = fake_extract

        with patch('src.gui.get_video_properties', return_value={'duration': 100.0}):
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        # This worker is now stale, so it must not schedule a render.
        gui.root.after.assert_not_called()
        gui._render_preview_images.assert_not_called()

    def test_render_drops_stale_generation(self):
        gui = _bare_gui()
        gui._preview_generation = 5  # newer requests already happened
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui.adjust_window_size = MagicMock()
        gui.gamma_var = MagicMock()
        gui.gamma_var.get.return_value = 1.0
        gui.adjust_gamma = MagicMock()

        gui._render_preview_images('o', 'c', 1.0, generation=4)

        gui.original_image_label.config.assert_not_called()
        gui.converted_image_label.config.assert_not_called()
        gui.adjust_window_size.assert_not_called()

    def test_schedule_on_main_swallows_teardown_error(self):
        import tkinter as tk
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.root.after.side_effect = tk.TclError("application has been destroyed")
        # Must not raise even though the root is gone.
        gui._schedule_on_main(lambda: None)

    def test_extraction_failure_is_surfaced_on_main_thread(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.original_image = None
        gui.last_time_position = None
        gui.filter_options = ['Static', 'Dynamic']
        gui.filter_var = MagicMock()
        gui.filter_var.get.return_value = 'Static'
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'
        gui.handle_preview_error = MagicMock()

        # get_video_properties returning None makes the worker raise.
        with patch('src.gui.get_video_properties', return_value=None):
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        gui.root.after.assert_called_once()
        callback = gui.root.after.call_args[0][1]
        callback()
        gui.handle_preview_error.assert_called_once()

    @patch('src.gui.ImageTk.PhotoImage')
    def test_render_updates_labels_and_caches(self, mock_photo):
        gui = _bare_gui()
        mock_img = MagicMock(spec=Image.Image)
        mock_img.resize.return_value = mock_img
        photo = MagicMock()
        mock_photo.return_value = photo
        gui.gamma_var = MagicMock()
        gui.gamma_var.get.return_value = 2.2
        gui.adjust_gamma = MagicMock(return_value=mock_img)
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui.adjust_window_size = MagicMock()

        gui._render_preview_images(mock_img, mock_img, time_position=12.0)

        gui.adjust_gamma.assert_called_once_with(mock_img, 2.2)
        mock_img.resize.assert_has_calls([
            call((960, 540), Image.LANCZOS),
            call((960, 540), Image.LANCZOS),
        ])
        self.assertEqual(mock_photo.call_count, 2)
        gui.original_image_label.config.assert_called_with(image=photo)
        gui.converted_image_label.config.assert_called_with(image=photo)
        self.assertIs(gui.original_image, mock_img)
        self.assertIs(gui.converted_image_base, mock_img)
        self.assertEqual(gui.last_time_position, 12.0)
        gui.adjust_window_size.assert_called_once()


class TestGuiInteractions(unittest.TestCase):
    """Pure-logic GUI paths exercised without a live Tk root."""

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_check_gpu_acceleration_resets_when_unavailable(self, mock_cm, mock_mb):
        gui = _bare_gui()
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_var.get.return_value = True
        mock_cm.is_gpu_available.return_value = False

        gui.check_gpu_acceleration()

        gui.gpu_accel_var.set.assert_called_once_with(False)
        mock_mb.showwarning.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_check_gpu_acceleration_keeps_when_available(self, mock_cm, mock_mb):
        gui = _bare_gui()
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_var.get.return_value = True
        mock_cm.is_gpu_available.return_value = True

        gui.check_gpu_acceleration()

        gui.gpu_accel_var.set.assert_not_called()
        mock_mb.showwarning.assert_not_called()

    def test_handle_file_drop_sets_paths_and_refreshes(self):
        gui = _bare_gui()
        gui.drop_target_registered = True
        gui.input_path_var = MagicMock()
        gui.output_path_var = MagicMock()
        gui.original_image = 'stale'
        gui.converted_image_base = 'stale'
        gui.button_frame = MagicMock()
        gui.image_frame = MagicMock()
        gui.action_frame = MagicMock()
        gui.update_frame_preview = MagicMock()
        gui.highlight_frame_button = MagicMock()

        event = MagicMock()
        event.data = '{C:/videos/movie.mkv}'  # tkdnd wraps paths in braces
        gui.handle_file_drop(event)

        gui.input_path_var.set.assert_called_once_with('C:/videos/movie.mkv')
        gui.output_path_var.set.assert_called_once_with('C:/videos/movie_sdr.mkv')
        self.assertIsNone(gui.original_image)
        self.assertIsNone(gui.converted_image_base)
        gui.update_frame_preview.assert_called_once()
        gui.highlight_frame_button.assert_called_once_with(1)

    def test_clear_preview_resets_cache_and_minsize(self):
        gui = _bare_gui()
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui.original_image = 'x'
        gui.converted_image_base = 'y'
        gui.root = MagicMock()

        gui.clear_preview()

        gui.original_image_label.config.assert_called_with(image='')
        gui.converted_image_label.config.assert_called_with(image='')
        self.assertIsNone(gui.original_image)
        self.assertIsNone(gui.converted_image_base)
        gui.root.minsize.assert_called_once_with(*DEFAULT_MIN_SIZE)

    def test_frame_button_click_resets_cache_and_updates(self):
        gui = _bare_gui()
        gui.original_image = 'x'
        gui.converted_image_base = 'y'
        gui.highlight_frame_button = MagicMock()
        gui.update_frame_preview = MagicMock()

        gui.on_frame_button_click(3)

        self.assertEqual(gui.current_frame_index, 3)
        self.assertIsNone(gui.original_image)
        self.assertIsNone(gui.converted_image_base)
        gui.highlight_frame_button.assert_called_once_with(3)
        gui.update_frame_preview.assert_called_once()


class TestSupportedOutputPath(unittest.TestCase):
    """WebM output is redirected to MKV (H.264 can't live in WebM)."""

    def test_webm_redirects_to_mkv(self):
        self.assertEqual(
            HDRConverterGUI._supported_output_path('C:/v/movie_sdr.webm'),
            'C:/v/movie_sdr.mkv')

    def test_webm_redirect_is_case_insensitive(self):
        self.assertEqual(
            HDRConverterGUI._supported_output_path('movie.WEBM'), 'movie.mkv')

    def test_supported_extensions_pass_through(self):
        for path in ('a.mkv', 'a.mp4', 'a.m4v', 'a.mov', 'a.avi'):
            self.assertEqual(HDRConverterGUI._supported_output_path(path), path)


class TestPreviewExtractionCache(unittest.TestCase):
    """Extracted frames are cached by (path, time, filter, tonemapper) so
    revisiting a frame/filter/tonemapper combo never re-runs ffmpeg."""

    @patch('src.gui.extract_frame_with_conversion', return_value='conv')
    @patch('src.gui.extract_frame', return_value='orig')
    def test_repeated_combo_is_a_cache_hit(self, mock_extract, mock_convert):
        gui = _bare_gui()
        first = gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
        second = gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
        self.assertEqual(mock_extract.call_count, 1)   # original cached
        self.assertEqual(mock_convert.call_count, 1)   # converted cached
        self.assertEqual(first, second)

    @patch('src.gui.extract_frame_with_conversion', side_effect=['c0', 'c1'])
    @patch('src.gui.extract_frame', return_value='orig')
    def test_same_frame_new_filter_reuses_original(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
        gui._extract_preview_images('in.mp4', 5.0, 1, 'reinhard')
        self.assertEqual(mock_extract.call_count, 1)   # original shared across filters
        self.assertEqual(mock_convert.call_count, 2)   # converted differs per filter

    @patch('src.gui.extract_frame_with_conversion', side_effect=['a', 'b'])
    @patch('src.gui.extract_frame', return_value='orig')
    def test_different_tonemapper_is_a_cache_miss(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 1, 'mobius')
        gui._extract_preview_images('in.mp4', 5.0, 1, 'hable')
        self.assertEqual(mock_convert.call_count, 2)

    @patch('src.gui.extract_frame_with_conversion', return_value='c')
    @patch('src.gui.extract_frame', return_value='o')
    def test_new_frame_position_reextracts_original(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
        gui._extract_preview_images('in.mp4', 9.0, 0, 'reinhard')
        self.assertEqual(mock_extract.call_count, 2)

    def test_reset_clears_the_cache(self):
        gui = _bare_gui()
        with patch('src.gui.extract_frame', return_value='o'), \
             patch('src.gui.extract_frame_with_conversion', return_value='c'):
            gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
        gui._reset_preview_cache()
        with patch('src.gui.extract_frame', return_value='o2') as me, \
             patch('src.gui.extract_frame_with_conversion', return_value='c2') as mc:
            gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
            me.assert_called_once()   # re-extracted after reset
            mc.assert_called_once()

    def test_cache_is_bounded(self):
        gui = _bare_gui()
        with patch('src.gui.extract_frame', return_value='o'), \
             patch('src.gui.extract_frame_with_conversion', return_value='c'):
            for i in range(HDRConverterGUI._PREVIEW_CACHE_MAX + 20):
                gui._extract_preview_images('in.mp4', float(i), 0, 'reinhard')
        self.assertLessEqual(len(gui._preview_cache_converted),
                             HDRConverterGUI._PREVIEW_CACHE_MAX)


class TestPreviewPerformance(unittest.TestCase):
    """Snappiness: gamma changes avoid ffmpeg; extraction targets preview size;
    duration is probed once per file."""

    def test_gamma_change_reuses_cached_base_without_reextracting(self):
        gui = _bare_gui()
        gui.display_image_var = MagicMock()
        gui.display_image_var.get.return_value = True
        gui._converted_preview_base = MagicMock(spec=Image.Image)
        gui._apply_gamma_to_preview = MagicMock()
        gui.update_frame_preview = MagicMock()

        gui.on_gamma_change()

        gui._apply_gamma_to_preview.assert_called_once()
        gui.update_frame_preview.assert_not_called()  # no ffmpeg re-extraction

    def test_gamma_change_falls_back_to_full_update_without_cached_base(self):
        gui = _bare_gui()
        gui.display_image_var = MagicMock()
        gui.display_image_var.get.return_value = True
        gui._converted_preview_base = None
        gui._apply_gamma_to_preview = MagicMock()
        gui.update_frame_preview = MagicMock()

        gui.on_gamma_change()

        gui.update_frame_preview.assert_called_once()
        gui._apply_gamma_to_preview.assert_not_called()

    def test_apply_gamma_adjusts_the_small_cached_base(self):
        gui = _bare_gui()
        base = MagicMock(spec=Image.Image)
        gui._converted_preview_base = base
        gui.gamma_var = MagicMock()
        gui.gamma_var.get.return_value = 1.5
        gui.adjust_gamma = MagicMock(return_value=MagicMock())
        gui.converted_image_label = MagicMock()

        with patch('src.gui.ImageTk.PhotoImage'):
            gui._apply_gamma_to_preview()

        gui.adjust_gamma.assert_called_once_with(base, 1.5)
        gui.converted_image_label.config.assert_called_once()

    @patch('src.gui.extract_frame_with_conversion', return_value='converted')
    @patch('src.gui.extract_frame', return_value='original')
    def test_extraction_targets_preview_resolution(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 0, 'reinhard')
        self.assertEqual(mock_extract.call_args.kwargs.get('width'), PREVIEW_SIZE[0])
        self.assertEqual(mock_extract.call_args.kwargs.get('height'), PREVIEW_SIZE[1])
        self.assertEqual(mock_convert.call_args.kwargs.get('width'), PREVIEW_SIZE[0])
        self.assertEqual(mock_convert.call_args.kwargs.get('height'), PREVIEW_SIZE[1])

    def test_duration_probed_once_per_file(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.original_image = None
        gui.last_time_position = None
        gui.filter_options = ['Static', 'Dynamic']
        gui.filter_var = MagicMock()
        gui.filter_var.get.return_value = 'Static'
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'
        gui._extract_preview_images = MagicMock(return_value=('o', 'c'))
        gui._render_preview_images = MagicMock()

        with patch('src.gui.get_video_properties',
                   return_value={'duration': 100.0}) as mock_props:
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        # Second preview of the same file reuses the cached duration.
        self.assertEqual(mock_props.call_count, 1)


class TestFfmpegAvailabilityGuard(unittest.TestCase):
    """The GUI surfaces a missing ffmpeg on startup (init no longer dialogs)."""

    # NB: gui.py imports the binaries via `from utils import ...` (bare name on
    # sys.path), which is a *different* module object than `src.utils`, so the
    # patch target must be `utils.FFMPEG_EXECUTABLE`.
    @patch('src.gui.messagebox')
    @patch('utils.FFMPEG_EXECUTABLE', None)
    def test_warns_when_executable_missing(self, mock_mb):
        gui = _bare_gui()
        self.assertFalse(gui.check_ffmpeg_available())
        mock_mb.showerror.assert_called_once()

    @patch('src.gui.messagebox')
    def test_ok_when_executable_present(self, mock_mb):
        gui = _bare_gui()
        self.assertTrue(gui.check_ffmpeg_available())
        mock_mb.showerror.assert_not_called()


class TestConvertVideoBranches(unittest.TestCase):
    """The validation/branch logic in convert_video (bare instance, no widgets)."""

    def _gui(self):
        gui = _bare_gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'in.mkv'
        gui.output_path_var = MagicMock(); gui.output_path_var.get.return_value = 'out.mkv'
        gui.gamma_var = MagicMock(); gui.gamma_var.get.return_value = 1.0
        gui.gpu_accel_var = MagicMock(); gui.gpu_accel_var.get.return_value = False
        gui.filter_options = ['Static', 'Dynamic']
        gui.filter_var = MagicMock(); gui.filter_var.get.return_value = 'Static'
        gui.tonemap_var = MagicMock(); gui.tonemap_var.get.return_value = 'Mobius'
        return gui

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    def test_empty_paths_warn_and_abort(self, mock_mb, mock_cm):
        gui = self._gui()
        gui.input_path_var.get.return_value = ''   # nothing selected
        gui.convert_video()
        mock_mb.showwarning.assert_called_once_with(
            "Warning", "Please select both an input file and specify an output file.")
        mock_mb.showerror.assert_not_called()      # not a misleading "not found"
        mock_cm.start_conversion.assert_not_called()

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.isfile', return_value=False)
    def test_input_not_found_aborts(self, _isfile, mock_mb, mock_cm):
        gui = self._gui()
        gui.convert_video()
        mock_mb.showerror.assert_called_once()
        mock_cm.start_conversion.assert_not_called()

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=True)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_overwrite_declined_aborts(self, _isfile, _exists, mock_mb, mock_cm):
        mock_mb.askyesno.return_value = False
        gui = self._gui()
        gui.convert_video()
        mock_cm.start_conversion.assert_not_called()

    @patch('src.gui.messagebox')
    def test_unexpected_error_is_caught_and_reported(self, mock_mb):
        gui = self._gui()
        gui.filter_var.get.return_value = 'Nonexistent'  # .index() raises ValueError
        gui.convert_video()  # must not propagate
        mock_mb.showerror.assert_called_once()

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_webm_output_redirects_with_notice_then_starts(self, _isfile, _exists, mock_mb, mock_cm):
        gui = self._gui()
        gui.output_path_var.get.return_value = 'out.webm'
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        mock_mb.showinfo.assert_called_once()           # format-changed notice
        gui.output_path_var.set.assert_called_with('out.mkv')
        mock_cm.start_conversion.assert_called_once()


class TestGuiErrorAndResizePaths(unittest.TestCase):

    def test_handle_preview_error_reports_and_clears(self):
        gui = _bare_gui()
        gui.error_label = MagicMock()
        gui.clear_preview = MagicMock()
        gui.original_title_label = MagicMock()
        gui.converted_title_label = MagicMock()
        gui.handle_preview_error(ValueError('boom'))
        gui.error_label.config.assert_called_once()
        gui.clear_preview.assert_called_once()

    @patch('src.gui.messagebox')
    def test_handle_file_drop_reports_exception(self, mock_mb):
        gui = _bare_gui()
        gui.drop_target_registered = True
        gui.input_path_var = MagicMock()
        gui.input_path_var.set.side_effect = RuntimeError('bad path')
        event = MagicMock()
        event.data = '{C:/x.mkv}'
        gui.handle_file_drop(event)  # must not raise
        mock_mb.showerror.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_check_gpu_acceleration_reports_exception(self, mock_cm, mock_mb):
        gui = _bare_gui()
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_var.get.return_value = True
        mock_cm.is_gpu_available.side_effect = RuntimeError('nope')
        gui.check_gpu_acceleration()
        gui.gpu_accel_var.set.assert_called_once_with(False)
        mock_mb.showerror.assert_called_once()

    @patch('src.gui.ImageTk.PhotoImage')
    def test_resize_images_rescales_present_frames(self, _mock_photo):
        gui = _bare_gui()
        img = MagicMock(spec=Image.Image)
        img.resize.return_value = img
        gui.original_image = img
        gui.converted_image_base = img
        gui.gamma_var = MagicMock(); gui.gamma_var.get.return_value = 1.5
        gui.adjust_gamma = MagicMock(return_value=img)
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui.resize_images(1000, 800)
        self.assertEqual(img.resize.call_count, 2)
        gui.original_image_label.config.assert_called_once()
        gui.converted_image_label.config.assert_called_once()

    def test_apply_gamma_is_noop_without_cached_base(self):
        gui = _bare_gui()
        gui._converted_preview_base = None
        gui.converted_image_label = MagicMock()
        gui._apply_gamma_to_preview()  # must not raise / touch the label
        gui.converted_image_label.config.assert_not_called()

    def test_update_frame_preview_display_on_renders(self):
        gui = _bare_gui()
        gui.display_image_var = MagicMock(); gui.display_image_var.get.return_value = True
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'in.mkv'
        gui.display_frames = MagicMock()
        gui.error_label = MagicMock()
        gui.original_title_label = MagicMock()
        gui.converted_title_label = MagicMock()
        gui.button_container = MagicMock()
        gui.arrange_widgets = MagicMock()
        gui.filter_combobox = MagicMock()
        gui.tonemap_combobox = MagicMock()

        gui.update_frame_preview()

        gui.display_frames.assert_called_once_with('in.mkv')
        gui.arrange_widgets.assert_called_once_with(image_frame=True)

    def test_adjust_window_size_shrinks_when_exceeding_screen(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.root.winfo_screenwidth.return_value = 1920
        gui.root.winfo_screenheight.return_value = 1080
        gui.root.winfo_width.side_effect = [5000, 1800]   # oversize, then fixed
        gui.root.winfo_height.side_effect = [3000, 1000]
        gui.resize_images = MagicMock()
        gui.adjust_window_size()
        gui.resize_images.assert_called_once()
        gui.root.minsize.assert_called_with(1800, 1000)


class TestGuiLifecycle(unittest.TestCase):
    """Drop-target toggling, cancellation delegation, and window close."""

    def test_drop_target_register_unregister_cycle_is_idempotent(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.drop_target_registered = False

        gui.register_drop_target()
        self.assertTrue(gui.drop_target_registered)
        gui.root.drop_target_register.assert_called_once()
        gui.root.dnd_bind.assert_called_once()

        # Registering again is a no-op.
        gui.root.drop_target_register.reset_mock()
        gui.register_drop_target()
        gui.root.drop_target_register.assert_not_called()

        gui.unregister_drop_target()
        self.assertFalse(gui.drop_target_registered)
        gui.root.drop_target_unregister.assert_called_once()

        # Unregistering again is a no-op.
        gui.root.drop_target_unregister.reset_mock()
        gui.unregister_drop_target()
        gui.root.drop_target_unregister.assert_not_called()

    @patch('src.gui.conversion_manager')
    def test_cancel_conversion_delegates_to_manager(self, mock_cm):
        gui = _bare_gui()
        gui.interactable_elements = ['element']
        gui.cancel_button = MagicMock()

        gui.cancel_conversion()

        mock_cm.cancel_conversion.assert_called_once_with(
            gui, ['element'], gui.cancel_button)

    @patch('src.gui.conversion_manager')
    def test_on_close_destroys_when_idle(self, mock_cm):
        gui = _bare_gui()
        gui.root = MagicMock()
        mock_cm.process = None

        gui.on_close()

        gui.root.destroy.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_on_close_cancels_active_conversion_when_confirmed(self, mock_cm, mock_mb):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.interactable_elements = []
        gui.cancel_button = MagicMock()
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        mock_cm.process = proc
        mock_mb.askokcancel.return_value = True

        gui.on_close()

        mock_cm.cancel_conversion.assert_called_once_with(gui, [], gui.cancel_button)
        gui.root.destroy.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_on_close_keeps_window_when_declined(self, mock_cm, mock_mb):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.interactable_elements = []
        gui.cancel_button = MagicMock()
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        mock_cm.process = proc
        mock_mb.askokcancel.return_value = False

        gui.on_close()

        mock_cm.cancel_conversion.assert_not_called()
        gui.root.destroy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
