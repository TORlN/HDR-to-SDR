"""Characterization tests.

These lock in behavior for code paths that are easy to regress silently --
originally written against the responsiveness refactor (preview threading
pool, resize/pane-fitting), which has since substantially landed and is now
covered here as an ongoing regression suite rather than transitional
scaffolding. New characterization tests are welcome for any other code path
worth pinning down this way, not just responsiveness work.
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
    get_maxcll,
    get_video_properties,
    extract_frame_with_conversion,
    FFMPEG_FILTER,
)
from src.gui import HDRConverterGUI, DEFAULT_MIN_SIZE, PREVIEW_SIZE


def _bare_gui():
    """An HDRConverterGUI with __init__ bypassed (no live Tk root needed)."""
    return object.__new__(HDRConverterGUI)


class _FakeScale:
    """Minimal stand-in for ttk.Scale that tracks its range and value, so a
    test can read the knob's fractional position (where it sits on the track)."""

    def __init__(self, frm, to, value):
        self._from, self._to, self._value = float(frm), float(to), float(value)

    def configure(self, **kw):
        if 'from_' in kw:
            self._from = float(kw['from_'])
        if 'to' in kw:
            self._to = float(kw['to'])

    def cget(self, key):
        return {'from': self._from, 'to': self._to}[key]

    def get(self):
        return self._value

    def set(self, value):
        self._value = float(value)

    def fraction(self):
        """The knob's position on the track, 0.0 (left) .. 1.0 (right)."""
        return (self._value - self._from) / (self._to - self._from)

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
        self.assertEqual(list(out.get_flattened_data()), list(img.get_flattened_data()))

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


class TestGetMaxcll(unittest.TestCase):
    """get_maxcll returns MaxCLL (max_content / peak pixel luminance), not MaxFALL."""

    def setUp(self):
        from src.utils import clear_maxfall_cache
        clear_maxfall_cache()

    @patch('src.utils.subprocess.check_output')
    def test_returns_maxcll_not_maxfall(self, mock_out):
        """Returns max_content=1000, not max_average=400."""
        data = {"frames": [{"side_data_list": [
            {"side_data_type": "Content light level metadata",
             "max_content": 1000, "max_average": 400}
        ]}]}
        mock_out.return_value = json.dumps(data).encode('utf-8')
        self.assertEqual(get_maxcll('video.mkv'), 1000.0)

    @patch('src.utils.subprocess.check_output')
    def test_returns_none_when_absent(self, mock_out):
        mock_out.return_value = json.dumps({"frames": []}).encode('utf-8')
        self.assertIsNone(get_maxcll('video.mkv'))


class TestGetMaxcllCaching(unittest.TestCase):
    """HDR metadata is probed once per path and cached (~0.5–1.2 s per probe)."""

    def setUp(self):
        from src.utils import clear_maxfall_cache
        clear_maxfall_cache()

    @patch('src.utils.subprocess.check_output')
    def test_repeated_calls_probe_once(self, mock_out):
        mock_out.return_value = json.dumps(
            {"frames": [{"side_data_list": [
                {"side_data_type": "Content light level metadata",
                 "max_content": 1000, "max_average": 250}
            ]}]}).encode('utf-8')
        first = get_maxcll('a.mkv')
        second = get_maxcll('a.mkv')
        self.assertEqual(first, 1000.0)
        self.assertEqual(second, 1000.0)
        self.assertEqual(mock_out.call_count, 1)  # second call served from cache

    @patch('src.utils.subprocess.check_output')
    def test_distinct_paths_probe_separately(self, mock_out):
        mock_out.return_value = json.dumps({"frames": []}).encode('utf-8')
        get_maxcll('a.mkv')
        get_maxcll('b.mkv')
        self.assertEqual(mock_out.call_count, 2)

    @patch('src.utils.subprocess.check_output')
    def test_clear_cache_forces_reprobe(self, mock_out):
        from src.utils import clear_maxfall_cache
        mock_out.return_value = json.dumps({"frames": []}).encode('utf-8')
        get_maxcll('a.mkv')
        clear_maxfall_cache()
        get_maxcll('a.mkv')
        self.assertEqual(mock_out.call_count, 2)



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
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'

        seen = {}

        def fake_extract(video_path, time_position, tonemapper):
            seen['thread'] = threading.current_thread()
            seen['time_position'] = time_position
            seen['tonemapper'] = tonemapper
            return ('original', 'converted')

        gui._extract_preview_images = fake_extract
        gui._render_preview_images = MagicMock()
        gui._prewarm_other_frames = MagicMock()  # isolate the visible-frame extraction

        with patch('src.preview.get_video_properties', return_value={'duration': 100.0}):
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        # Extraction happened on a worker thread, not the main thread.
        self.assertIsNotNone(seen.get('thread'))
        self.assertIsNot(seen['thread'], threading.main_thread())
        # Tk-owned values were read and forwarded correctly.
        self.assertAlmostEqual(seen['time_position'], 100.0 / 6)
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
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'
        gui._render_preview_images = MagicMock()

        def fake_extract(*a, **k):
            # Simulate a newer request arriving while this worker was extracting.
            gui._preview_generation += 1
            return ('original', 'converted')

        gui._extract_preview_images = fake_extract

        with patch('src.preview.get_video_properties', return_value={'duration': 100.0}):
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
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'
        gui.handle_preview_error = MagicMock()

        # get_video_properties returning None makes the worker raise.
        with patch('src.preview.get_video_properties', return_value=None):
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        gui.root.after.assert_called_once()
        callback = gui.root.after.call_args[0][1]
        callback()
        gui.handle_preview_error.assert_called_once()

    def test_worker_prewarms_other_frames_after_render(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.tonemap_var = MagicMock(); gui.tonemap_var.get.return_value = 'Mobius'
        gui._extract_preview_images = MagicMock(return_value=('o', 'c'))
        gui._render_preview_images = MagicMock()
        gui._prewarm_other_frames = MagicMock()

        with patch('src.preview.get_video_properties', return_value={'duration': 60.0}):
            gui.display_frames('in.mp4')
            gui._preview_thread.join(timeout=5)

        gui._prewarm_other_frames.assert_called_once()
        vp, duration, tm, gen = gui._prewarm_other_frames.call_args[0]
        self.assertEqual((vp, duration, tm), ('in.mp4', 60.0, 'mobius'))


class TestPreviewPrewarm(unittest.TestCase):
    """Non-visible seek frames are pre-extracted in 2 batch ffmpeg calls (not 8 individual ones)."""

    def _gui(self, current=1, total=5, generation=3):
        gui = _bare_gui()
        gui.current_frame_index = current
        gui.total_frames = total
        gui._preview_generation = generation
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        return gui

    @patch('src.preview.extract_frames_with_conversion_batch', return_value=[])
    @patch('src.preview.extract_frames_batch', return_value=[])
    def test_two_batch_calls_for_four_frames(self, mock_orig, mock_conv):
        """Whole prewarm uses exactly 1 original batch call + 1 converted batch call."""
        gui = self._gui(current=1)
        gui._prewarm_other_frames('in.mkv', 60.0, 'reinhard', generation=3)
        self.assertEqual(mock_orig.call_count, 1)
        self.assertEqual(mock_conv.call_count, 1)

    @patch('src.preview.extract_frames_with_conversion_batch', return_value=[])
    @patch('src.preview.extract_frames_batch', return_value=[])
    def test_extracts_every_other_frame_position(self, mock_orig, mock_conv):
        """Batch receives the 4 non-current time positions."""
        gui = self._gui(current=1)
        gui._prewarm_other_frames('in.mkv', 60.0, 'mobius', generation=3)
        positions = mock_orig.call_args[0][1]
        # index/(total+1)*duration for indices 2..5 (index 1 = current, skipped)
        self.assertEqual(sorted(round(t, 3) for t in positions), [20.0, 30.0, 40.0, 50.0])

    @patch('src.preview.extract_frames_with_conversion_batch', return_value=[])
    @patch('src.preview.extract_frames_batch', return_value=[])
    def test_skips_the_currently_displayed_frame(self, mock_orig, mock_conv):
        """Frame at the current index is never included in the batch positions."""
        gui = self._gui(current=3)
        gui._prewarm_other_frames('in.mkv', 60.0, 'reinhard', generation=3)
        positions = mock_orig.call_args[0][1]
        self.assertEqual(len(positions), 4)
        self.assertNotIn(30.0, [round(t, 3) for t in positions])  # 3/6*60 = 30

    @patch('src.preview.extract_frames_with_conversion_batch')
    @patch('src.preview.extract_frames_batch')
    def test_stops_immediately_when_superseded(self, mock_orig, mock_conv):
        """Stale generation → batch functions never called."""
        gui = self._gui()  # _preview_generation=3
        gui._prewarm_other_frames('in.mkv', 60.0, 'mobius', generation=1)
        mock_orig.assert_not_called()
        mock_conv.assert_not_called()

    @patch('src.preview.extract_frames_with_conversion_batch', return_value=[])
    @patch('src.preview.extract_frames_batch')
    def test_original_batch_errors_are_swallowed(self, mock_orig, mock_conv):
        """A batch failure must not propagate out of the background worker."""
        mock_orig.side_effect = RuntimeError('batch decode fail')
        gui = self._gui()
        with patch('src.preview.logging'):
            gui._prewarm_other_frames('in.mkv', 60.0, 'mobius', generation=3)

    @patch('src.preview.extract_frames_with_conversion_batch')
    @patch('src.preview.extract_frames_batch', return_value=[])
    def test_converted_batch_errors_are_swallowed(self, mock_orig, mock_conv):
        mock_conv.side_effect = RuntimeError('tonemap batch fail')
        gui = self._gui()
        with patch('src.preview.logging'):
            gui._prewarm_other_frames('in.mkv', 60.0, 'mobius', generation=3)


class TestPreviewPool(unittest.TestCase):
    """ThreadPoolExecutor coordinates preview batch tasks with a hardware-aware cap."""

    # ── pool worker cap ─────────────────────────────────────────────────────

    def test_pool_worker_cap_is_at_least_one(self):
        from src.gui import _PREVIEW_POOL_WORKERS
        self.assertGreaterEqual(_PREVIEW_POOL_WORKERS, 1)

    def test_pool_worker_cap_formula(self):
        """max(1, cpu_count // 4) — matches the documented formula."""
        from src.gui import _PREVIEW_POOL_WORKERS
        expected = max(1, (os.cpu_count() or 1) // 4)
        self.assertEqual(_PREVIEW_POOL_WORKERS, expected)

    # ── _PreviewFuture ──────────────────────────────────────────────────────

    def test_preview_future_join_blocks_until_done(self):
        from src.gui import _PreviewFuture
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            sentinel = []
            future = pool.submit(lambda: sentinel.append(1))
            _PreviewFuture(future).join(timeout=5)
        self.assertEqual(sentinel, [1])

    def test_preview_future_join_swallows_worker_exception(self):
        from src.gui import _PreviewFuture
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: 1 / 0)
            _PreviewFuture(future).join(timeout=5)  # must not raise

    # ── display_frames uses the pool ────────────────────────────────────────

    def test_display_frames_returns_preview_future(self):
        from src.gui import _PreviewFuture
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.original_image = None
        gui.last_time_position = None
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Reinhard'
        gui._extract_preview_images = MagicMock(return_value=('o', 'c'))
        gui._render_preview_images = MagicMock()
        gui._prewarm_other_frames = MagicMock()

        with patch('src.preview.get_video_properties', return_value={'duration': 30.0}):
            gui.display_frames('v.mp4')

        self.assertIsInstance(gui._preview_thread, _PreviewFuture)

    def test_display_frames_worker_runs_off_main_thread(self):
        """The pool submits the worker to a background thread, not the caller."""
        seen = {}

        def fake_extract(*a, **k):
            seen['thread'] = threading.current_thread()
            return ('o', 'c')

        gui = _bare_gui()
        gui.root = MagicMock()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.original_image = None
        gui.last_time_position = None
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Reinhard'
        gui._extract_preview_images = fake_extract
        gui._render_preview_images = MagicMock()
        gui._prewarm_other_frames = MagicMock()

        with patch('src.preview.get_video_properties', return_value={'duration': 30.0}):
            gui.display_frames('v.mp4')
            gui._preview_thread.join(timeout=5)

        self.assertIsNot(seen['thread'], threading.main_thread())

    # ── _prewarm_other_frames dispatches to pool ────────────────────────────

    def test_prewarm_submits_two_tasks_to_pool(self):
        """`_prewarm_other_frames` submits one original task + one converted task."""
        gui = _bare_gui()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        gui._preview_pool = MagicMock()

        gui._prewarm_other_frames('v.mkv', 60.0, 'reinhard', generation=1)

        self.assertEqual(gui._preview_pool.submit.call_count, 2)
        methods_submitted = [c[0][0] for c in gui._preview_pool.submit.call_args_list]
        self.assertIn(gui._prewarm_batch_originals, methods_submitted)
        self.assertIn(gui._prewarm_batch_converted, methods_submitted)

    def test_prewarm_does_not_submit_when_stale(self):
        gui = _bare_gui()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui._preview_generation = 5
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        gui._preview_pool = MagicMock()

        gui._prewarm_other_frames('v.mkv', 60.0, 'reinhard', generation=1)

        gui._preview_pool.submit.assert_not_called()

    def test_prewarm_does_not_submit_when_all_cached(self):
        """No pool submissions when every frame is already in both caches."""
        gui = _bare_gui()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui._preview_generation = 1
        duration = 60.0
        # Pre-populate both caches for all non-current frames.
        orig = {}
        conv = {}
        for idx in range(2, 6):
            t = round((idx / 6) * duration, 3)
            orig[('v.mkv', t)] = MagicMock()
            conv[('v.mkv', t, 'reinhard')] = MagicMock()
        gui._preview_cache_original = orig
        gui._preview_cache_converted = conv
        gui._preview_pool = MagicMock()

        gui._prewarm_other_frames('v.mkv', duration, 'reinhard', generation=1)

        gui._preview_pool.submit.assert_not_called()

    # ── _prewarm_batch_originals ────────────────────────────────────────────

    @patch('src.preview.extract_frames_batch')
    def test_batch_originals_populates_cache(self, mock_batch):
        img = MagicMock()
        mock_batch.return_value = [img]
        gui = _bare_gui()
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._cache_lock = threading.Lock()

        gui._prewarm_batch_originals('v.mkv', [10.0], generation=1)

        self.assertIn(('v.mkv', 10.0), gui._preview_cache_original)
        self.assertIs(gui._preview_cache_original[('v.mkv', 10.0)], img)

    @patch('src.preview.extract_frames_batch')
    def test_batch_originals_bails_when_stale(self, mock_batch):
        gui = _bare_gui()
        gui._preview_generation = 5

        gui._prewarm_batch_originals('v.mkv', [10.0], generation=1)

        mock_batch.assert_not_called()

    @patch('src.preview.extract_frames_batch')
    def test_batch_originals_swallows_error(self, mock_batch):
        mock_batch.side_effect = RuntimeError('ffmpeg exploded')
        gui = _bare_gui()
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._cache_lock = threading.Lock()
        with patch('src.preview.logging'):
            gui._prewarm_batch_originals('v.mkv', [10.0], generation=1)  # must not raise

    # ── _prewarm_batch_converted ────────────────────────────────────────────

    @patch('src.preview.extract_frames_with_conversion_batch')
    def test_batch_converted_populates_cache(self, mock_batch):
        img = MagicMock()
        mock_batch.return_value = [img]
        gui = _bare_gui()
        gui._preview_generation = 1
        gui._preview_cache_converted = {}
        gui._cache_lock = threading.Lock()

        gui._prewarm_batch_converted('v.mkv', [10.0], 'mobius', generation=1)

        self.assertIn(('v.mkv', 10.0, 'mobius'), gui._preview_cache_converted)
        self.assertIs(gui._preview_cache_converted[('v.mkv', 10.0, 'mobius')], img)

    @patch('src.preview.extract_frames_with_conversion_batch')
    def test_batch_converted_bails_when_stale(self, mock_batch):
        gui = _bare_gui()
        gui._preview_generation = 5

        gui._prewarm_batch_converted('v.mkv', [10.0], 'mobius', generation=1)

        mock_batch.assert_not_called()

    @patch('src.preview.extract_frames_with_conversion_batch')
    def test_batch_converted_swallows_error(self, mock_batch):
        mock_batch.side_effect = RuntimeError('tonemap exploded')
        gui = _bare_gui()
        gui._preview_generation = 1
        gui._preview_cache_converted = {}
        gui._cache_lock = threading.Lock()
        with patch('src.preview.logging'):
            gui._prewarm_batch_converted('v.mkv', [10.0], 'mobius', generation=1)

    # ── on_close shuts down the pool ────────────────────────────────────────

    def test_on_close_shuts_down_pool_when_no_conversion(self):
        from unittest.mock import patch as _patch
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._preview_pool = MagicMock()
        gui.interactable_elements = []
        gui.cancel_button = MagicMock()

        with _patch('src.gui.conversion_manager') as mock_cm, \
             _patch.object(gui, '_save_current_settings'):
            mock_cm.process = None
            gui.on_close()

        gui._preview_pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    def test_on_close_shuts_down_pool_when_conversion_cancelled(self):
        from unittest.mock import patch as _patch
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._preview_pool = MagicMock()
        gui.interactable_elements = []
        gui.cancel_button = MagicMock()

        with _patch('src.gui.conversion_manager') as mock_cm, \
             _patch('src.gui.messagebox') as mock_mb, \
             _patch.object(gui, '_save_current_settings'):
            proc = MagicMock()
            proc.poll.return_value = None
            mock_cm.process = proc
            mock_mb.askokcancel.return_value = True
            gui.on_close()

        gui._preview_pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


class TestGpuOnlyTonemapperPreviewDispatch(unittest.TestCase):
    """BT.2390/Spline have no CPU/zscale equivalent -- preview must route
    them through the real libplacebo/Vulkan path, never approximate via
    zscale, so the CPU and GPU extraction functions must be dispatched on
    based on the selected tonemapper."""

    @patch('src.preview.extract_frame_with_gpu_conversion', return_value='gpu-converted')
    @patch('src.preview.extract_frame_with_conversion')
    @patch('src.preview.extract_frame', return_value='orig')
    def test_single_frame_dispatches_to_gpu_path_for_bt2390(
            self, _extract, mock_cpu_convert, mock_gpu_convert):
        gui = _bare_gui()
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        original, converted = gui._extract_preview_images('in.mp4', 5.0, 'bt.2390')
        self.assertEqual(converted, 'gpu-converted')
        mock_gpu_convert.assert_called_once()
        mock_cpu_convert.assert_not_called()

    @patch('src.preview.extract_frame_with_gpu_conversion')
    @patch('src.preview.extract_frame_with_conversion')
    @patch('src.preview.extract_frame', return_value='orig')
    def test_single_frame_still_uses_cpu_path_for_mobius(
            self, _extract, mock_cpu_convert, mock_gpu_convert):
        gui = _bare_gui()
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        gui._extract_preview_images('in.mp4', 5.0, 'mobius')
        mock_cpu_convert.assert_called_once()
        mock_gpu_convert.assert_not_called()

    @patch('src.preview.extract_frames_with_gpu_conversion_batch', return_value=['g0'])
    @patch('src.preview.extract_frames_with_conversion_batch')
    def test_batch_prewarm_dispatches_to_gpu_path_for_spline(
            self, mock_cpu_batch, mock_gpu_batch):
        gui = _bare_gui()
        gui._preview_generation = 1
        gui._preview_cache_converted = {}
        gui._prewarm_batch_converted('v.mkv', [10.0], 'spline', generation=1)
        mock_gpu_batch.assert_called_once()
        mock_cpu_batch.assert_not_called()
        self.assertIn(('v.mkv', 10.0, 'spline'), gui._preview_cache_converted)

    @patch('src.preview.extract_frames_with_gpu_conversion_batch')
    @patch('src.preview.extract_frames_with_conversion_batch', return_value=['c0'])
    def test_batch_prewarm_still_uses_cpu_path_for_hable(
            self, mock_cpu_batch, mock_gpu_batch):
        gui = _bare_gui()
        gui._preview_generation = 1
        gui._preview_cache_converted = {}
        gui._prewarm_batch_converted('v.mkv', [10.0], 'hable', generation=1)
        mock_cpu_batch.assert_called_once()
        mock_gpu_batch.assert_not_called()


class TestPreviewWorkerThreadRender(unittest.TestCase):
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
        gui._hide_preview_loading = MagicMock()
        gui._reveal_preview = MagicMock()
        # Past the first reveal -> live-geometry path; no image_frame -> native size.
        gui._window_auto_fitted = True

        gui._render_preview_images(mock_img, mock_img, time_position=12.0)

        gui.adjust_gamma.assert_called_once_with(mock_img, 2.2)
        mock_img.resize.assert_has_calls([
            call((3840, 2160), Image.LANCZOS),
            call((3840, 2160), Image.LANCZOS),
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
        mock_cm.is_gpu_acceleration_available.return_value = False

        gui.check_gpu_acceleration()

        gui.gpu_accel_var.set.assert_called_once_with(False)
        mock_mb.showwarning.assert_called_once()

    @patch('src.gui.messagebox')
    @patch('src.gui.conversion_manager')
    def test_check_gpu_acceleration_keeps_when_available(self, mock_cm, mock_mb):
        gui = _bare_gui()
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_var.get.return_value = True
        mock_cm.is_gpu_acceleration_available.return_value = True

        gui.check_gpu_acceleration()

        gui.gpu_accel_var.set.assert_not_called()
        mock_mb.showwarning.assert_not_called()

    def test_handle_file_drop_sets_paths_and_refreshes(self):
        gui = _bare_gui()
        gui.drop_target_registered = True
        # Unlicensed keeps the plain load-only path (licensed drops route
        # through the batch queue instead -- covered separately below).
        gui._licensed = False
        gui.input_path_var = MagicMock()
        gui.output_path_var = MagicMock()
        gui.format_var = MagicMock()
        gui.custom_time_var = MagicMock()
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
        # Unlicensed loads force the MP4 container regardless of input format.
        gui.output_path_var.set.assert_called_once_with('C:/videos/movie_sdr.mp4')
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


class TestOutputFormat(unittest.TestCase):
    """The output container is an explicit user choice (MP4 / MKV / MOV)."""

    def test_output_path_takes_chosen_extension(self):
        self.assertEqual(
            HDRConverterGUI._output_path_with_format('C:/v/movie_sdr.webm', 'MKV'),
            'C:/v/movie_sdr.mkv')
        self.assertEqual(
            HDRConverterGUI._output_path_with_format('C:/v/movie_sdr.mkv', 'MP4'),
            'C:/v/movie_sdr.mp4')

    def test_format_defaults_match_input_container(self):
        self.assertEqual(HDRConverterGUI._format_for_input('a.mp4'), 'MP4')
        self.assertEqual(HDRConverterGUI._format_for_input('a.m4v'), 'MP4')
        self.assertEqual(HDRConverterGUI._format_for_input('a.mov'), 'MOV')
        self.assertEqual(HDRConverterGUI._format_for_input('a.mkv'), 'MKV')

    def test_unsupported_input_containers_default_to_mkv(self):
        # WebM/AVI can't be expressed as MP4/MKV/MOV 1:1 -> safest default is MKV.
        for path in ('a.webm', 'a.avi', 'a.unknown'):
            self.assertEqual(HDRConverterGUI._format_for_input(path), 'MKV')


class TestQualityRange(unittest.TestCase):
    """The Quality slider spans CRF 17-28 on CPU and CQ 15-30 on GPU."""

    def _gui(self, gpu):
        gui = _bare_gui()
        gui.gpu_accel_var = MagicMock(); gui.gpu_accel_var.get.return_value = gpu
        gui.quality_slider = MagicMock()
        # _apply_quality_range reads the slider's current range/value to preserve
        # the knob's position; start from the CPU range with a mid value.
        gui.quality_slider.cget.side_effect = lambda k: {'from': 28.0, 'to': 17.0}[k]
        gui.quality_slider.get.return_value = 23.0
        gui.quality_var = MagicMock()
        return gui

    def test_cpu_range_is_17_to_28(self):
        gui = self._gui(gpu=False)
        gui.quality_var.get.return_value = 23
        gui._apply_quality_range()
        # from_=worst(28, left=smaller file), to=best(17, right=better quality)
        gui.quality_slider.configure.assert_called_once_with(from_=28, to=17)

    def test_gpu_range_is_15_to_30(self):
        gui = self._gui(gpu=True)
        gui.quality_var.get.return_value = 23
        gui._apply_quality_range()
        gui.quality_slider.configure.assert_called_once_with(from_=30, to=15)

    def test_value_is_clamped_into_the_active_range(self):
        gui = self._gui(gpu=False)        # CPU range 17..28
        gui.quality_slider.get.return_value = 14   # below CPU minimum (better than best)
        gui._apply_quality_range()
        gui.quality_var.set.assert_called_once_with(17)

    def test_knob_position_held_across_gpu_toggle(self):
        # The same value sits at different spots in the CRF vs CQ ranges, so
        # re-ranging on toggle must remap to keep the knob from visibly sliding.
        gui = _bare_gui()
        store = {'v': 23}
        gui.quality_var = MagicMock()
        gui.quality_var.get.side_effect = lambda: store['v']
        gui.quality_var.set.side_effect = lambda x: store.__setitem__('v', x)
        gui.gpu_accel_var = MagicMock(); gui.gpu_accel_var.get.return_value = False
        gui.quality_slider = _FakeScale(28, 17, 23)  # CPU range, knob at 23

        frac_cpu = gui.quality_slider.fraction()
        gui.gpu_accel_var.get.return_value = True     # check GPU acceleration
        gui._apply_quality_range()
        self.assertAlmostEqual(frac_cpu, gui.quality_slider.fraction(), places=2)

        gui.gpu_accel_var.get.return_value = False     # uncheck it again
        gui._apply_quality_range()
        self.assertAlmostEqual(frac_cpu, gui.quality_slider.fraction(), places=2)


class TestTimestampParsing(unittest.TestCase):
    """_parse_timestamp turns an HH:MM:SS / MM:SS / SS string into seconds."""

    def test_parses_seconds_only(self):
        self.assertAlmostEqual(HDRConverterGUI._parse_timestamp('90'), 90.0)

    def test_parses_minutes_seconds(self):
        self.assertAlmostEqual(HDRConverterGUI._parse_timestamp('1:30'), 90.0)

    def test_parses_hours_minutes_seconds(self):
        self.assertAlmostEqual(HDRConverterGUI._parse_timestamp('0:01:30'), 90.0)
        self.assertAlmostEqual(HDRConverterGUI._parse_timestamp('1:00:00'), 3600.0)

    def test_parses_fractional_seconds(self):
        self.assertAlmostEqual(HDRConverterGUI._parse_timestamp('0:00:01.5'), 1.5)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            HDRConverterGUI._parse_timestamp('   ')

    def test_rejects_nonnumeric(self):
        with self.assertRaises(ValueError):
            HDRConverterGUI._parse_timestamp('abc')

    def test_rejects_too_many_parts(self):
        with self.assertRaises(ValueError):
            HDRConverterGUI._parse_timestamp('1:2:3:4')

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            HDRConverterGUI._parse_timestamp('-5')


class TestPreviewTimePosition(unittest.TestCase):
    """_preview_time_position picks the frame-index slot unless a custom seek is set."""

    def test_uses_frame_index_by_default(self):
        gui = _bare_gui()
        gui.current_frame_index = 2
        gui.total_frames = 5
        gui.custom_time_position = None
        self.assertAlmostEqual(gui._preview_time_position(60.0), 2 / 6 * 60)

    def test_custom_time_overrides_frame_index(self):
        gui = _bare_gui()
        gui.current_frame_index = 2
        gui.total_frames = 5
        gui.custom_time_position = 12.5
        self.assertAlmostEqual(gui._preview_time_position(60.0), 12.5)

    def test_custom_time_clamped_to_duration(self):
        gui = _bare_gui()
        gui.current_frame_index = 1
        gui.total_frames = 5
        gui.custom_time_position = 999.0
        self.assertAlmostEqual(gui._preview_time_position(60.0), 60.0)

    def test_missing_attribute_falls_back_to_frame_index(self):
        # Bare instances created before custom-seek existed must still work.
        gui = _bare_gui()
        gui.current_frame_index = 3
        gui.total_frames = 5
        self.assertAlmostEqual(gui._preview_time_position(60.0), 3 / 6 * 60)


class TestCustomSeek(unittest.TestCase):
    """The custom-seek entry previews an arbitrary timestamp."""

    def _gui(self):
        gui = _bare_gui()
        gui.custom_time_var = MagicMock()
        gui.error_label = MagicMock()
        gui.highlight_frame_button = MagicMock()
        gui.update_frame_preview = MagicMock()
        gui.original_image = 'stale'
        gui.converted_image_base = 'stale'
        return gui

    def test_valid_timestamp_sets_position_and_refreshes(self):
        gui = self._gui()
        gui.custom_time_var.get.return_value = '1:30'
        gui.on_custom_seek()
        self.assertAlmostEqual(gui.custom_time_position, 90.0)
        self.assertIsNone(gui.original_image)   # cache invalidated
        self.assertIsNone(gui.converted_image_base)
        gui.highlight_frame_button.assert_called_once_with(0)  # no button selected
        gui.update_frame_preview.assert_called_once()

    def test_invalid_timestamp_shows_error_and_does_not_refresh(self):
        gui = self._gui()
        gui.custom_time_var.get.return_value = 'not a time'
        gui.on_custom_seek()
        gui.update_frame_preview.assert_not_called()
        gui.error_label.config.assert_called_once()  # error surfaced
        self.assertFalse(hasattr(gui, 'custom_time_position'))

    def test_frame_button_click_clears_custom_seek(self):
        gui = _bare_gui()
        gui.custom_time_position = 42.0
        gui.original_image = None
        gui.converted_image_base = None
        gui.highlight_frame_button = MagicMock()
        gui.update_frame_preview = MagicMock()
        gui.on_frame_button_click(2)
        self.assertIsNone(gui.custom_time_position)  # back to frame-index mode


class TestDropPathParsing(unittest.TestCase):
    """_parse_drop_paths splits a tkdnd drop payload into individual file paths."""

    def test_single_unbraced(self):
        self.assertEqual(HDRConverterGUI._parse_drop_paths('C:/a.mkv'), ['C:/a.mkv'])

    def test_braced_with_spaces(self):
        self.assertEqual(HDRConverterGUI._parse_drop_paths('{C:/my movie.mkv}'),
                         ['C:/my movie.mkv'])

    def test_multiple_mixed(self):
        self.assertEqual(
            HDRConverterGUI._parse_drop_paths('{C:/a b.mkv} C:/c.mkv'),
            ['C:/a b.mkv', 'C:/c.mkv'])

    def test_empty_payload(self):
        self.assertEqual(HDRConverterGUI._parse_drop_paths('   '), [])


class TestBatchQueue(unittest.TestCase):
    """The batch queue holds multiple files with per-file status."""

    def _gui(self):
        gui = _bare_gui()
        gui.batch_items = []
        gui.batch_listbox = MagicMock()
        gui._refresh_batch_list = MagicMock()
        return gui

    def test_add_batch_files_builds_items_with_output_paths(self):
        gui = self._gui()
        gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mkv'])
        self.assertEqual(len(gui.batch_items), 2)
        self.assertEqual(gui.batch_items[0]['input'], 'C:/v/a.mp4')
        self.assertEqual(gui.batch_items[0]['output'], 'C:/v/a_sdr.mp4')
        self.assertEqual(gui.batch_items[1]['output'], 'C:/v/b_sdr.mkv')
        self.assertTrue(all(it['status'] == 'Pending' for it in gui.batch_items))
        gui._refresh_batch_list.assert_called_once()

    def test_add_batch_files_skips_empty(self):
        gui = self._gui()
        gui.add_batch_files(['', 'C:/v/a.mp4'])
        self.assertEqual(len(gui.batch_items), 1)

    def test_clear_batch_queue_empties(self):
        gui = self._gui()
        gui.batch_items = [{'input': 'a.mkv', 'status': 'Pending'}]
        gui.clear_batch_queue()
        self.assertEqual(gui.batch_items, [])
        gui._refresh_batch_list.assert_called_once()

    def test_remove_selected_removes_by_index(self):
        gui = self._gui()
        gui.batch_items = [{'input': 'a'}, {'input': 'b'}, {'input': 'c'}]
        gui.batch_listbox.curselection.return_value = (1,)
        gui.remove_selected_batch_item()
        self.assertEqual([it['input'] for it in gui.batch_items], ['a', 'c'])

    def test_refresh_list_shows_status_icons(self):
        gui = _bare_gui()
        gui.batch_listbox = MagicMock()
        gui.batch_items = [
            {'input': 'C:/v/a.mp4', 'status': 'Pending'},
            {'input': 'C:/v/b.mp4', 'status': 'Done'},
        ]
        gui._refresh_batch_list()
        gui.batch_listbox.delete.assert_called_once()
        inserts = [c[0][1] for c in gui.batch_listbox.insert.call_args_list]
        self.assertIn('a.mp4', inserts[0])
        self.assertIn(HDRConverterGUI._STATUS_ICONS['Pending'], inserts[0])
        self.assertIn('b.mp4', inserts[1])
        self.assertIn(HDRConverterGUI._STATUS_ICONS['Done'], inserts[1])

    def test_add_files_loads_top_file_into_preview_when_input_empty(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = ''
        gui._load_input_file = MagicMock()
        gui.add_batch_files(['C:/v/a.mp4', 'C:/v/b.mkv'])
        gui._load_input_file.assert_called_once_with('C:/v/a.mp4')

    def test_add_files_does_not_clobber_already_loaded_input(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'already.mkv'
        gui._load_input_file = MagicMock()
        gui.add_batch_files(['C:/v/a.mp4'])
        gui._load_input_file.assert_not_called()

    def test_multi_file_drop_adds_to_batch(self):
        gui = _bare_gui()
        gui.drop_target_registered = True
        gui.add_batch_files = MagicMock()
        event = MagicMock()
        event.data = '{C:/a.mkv} {C:/b.mkv}'
        gui.handle_file_drop(event)
        gui.add_batch_files.assert_called_once_with(['C:/a.mkv', 'C:/b.mkv'])

    def test_single_drop_licensed_routes_through_queue(self):
        gui = _bare_gui()
        gui.drop_target_registered = True
        gui._licensed = True
        gui.add_batch_files = MagicMock()
        gui._load_input_file = MagicMock()
        # already previewing the dropped file -> queued but not re-loaded
        gui.input_path_var = MagicMock()
        gui.input_path_var.get.return_value = 'C:/a.mkv'
        event = MagicMock()
        event.data = '{C:/a.mkv}'
        gui.handle_file_drop(event)
        gui.add_batch_files.assert_called_once_with(['C:/a.mkv'])
        gui._load_input_file.assert_not_called()

    def test_add_files_skips_paths_already_queued(self):
        gui = self._gui()
        gui.batch_items = [{'input': 'a.mkv', 'status': 'Pending'}]
        gui.input_path_var = MagicMock()
        gui.input_path_var.get.return_value = 'a.mkv'
        gui.add_batch_files(['a.mkv', 'C:/v/b.mp4'])
        self.assertEqual([it['input'] for it in gui.batch_items],
                         ['a.mkv', 'C:/v/b.mp4'])

    def test_batch_item_for_current_input_matches_loaded_file(self):
        gui = self._gui()
        gui.batch_items = [{'input': 'a.mkv'}, {'input': 'b.mkv'}]
        gui.input_path_var = MagicMock()
        gui.input_path_var.get.return_value = 'b.mkv'
        self.assertIs(gui._batch_item_for_current_input(), gui.batch_items[1])

    def test_batch_item_for_current_input_none_when_not_queued(self):
        gui = self._gui()
        gui.batch_items = [{'input': 'a.mkv'}]
        gui.input_path_var = MagicMock()
        gui.input_path_var.get.return_value = 'other.mkv'
        self.assertIsNone(gui._batch_item_for_current_input())

    # --- Removing/clearing re-syncs the preview with the queue (issue 3) ---

    def test_remove_shown_file_loads_new_top_into_preview(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._load_input_file = MagicMock()
        gui._unload_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}, {'input': 'b.mkv'}]
        gui.batch_listbox.curselection.return_value = (0,)  # remove the shown file
        gui.remove_selected_batch_item()
        gui._load_input_file.assert_called_once_with('b.mkv')  # new top shown
        gui._unload_input_file.assert_not_called()

    def test_remove_last_shown_file_unloads_preview(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._load_input_file = MagicMock()
        gui._unload_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}]
        gui.batch_listbox.curselection.return_value = (0,)
        gui.remove_selected_batch_item()
        gui._unload_input_file.assert_called_once()  # queue empty -> preview cleared
        gui._load_input_file.assert_not_called()

    def test_remove_other_file_leaves_preview(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._load_input_file = MagicMock()
        gui._unload_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}, {'input': 'b.mkv'}]
        gui.batch_listbox.curselection.return_value = (1,)  # remove b; a still shown
        gui.remove_selected_batch_item()
        gui._load_input_file.assert_not_called()
        gui._unload_input_file.assert_not_called()

    def test_clear_unloads_preview_when_shown_file_was_queued(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._unload_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}, {'input': 'b.mkv'}]
        gui.clear_batch_queue()
        gui._unload_input_file.assert_called_once()

    def test_clear_leaves_manually_selected_preview(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'manual.mkv'
        gui._unload_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}]
        gui.clear_batch_queue()
        gui._unload_input_file.assert_not_called()  # don't clobber a manual selection

    def test_unload_clears_input_and_hides_preview(self):
        gui = _bare_gui()
        gui.input_path_var = MagicMock()
        gui.output_path_var = MagicMock()
        gui.info_label = MagicMock()
        gui.image_frame = MagicMock()
        gui.custom_time_var = MagicMock()
        gui.update_frame_preview = MagicMock()
        gui._reset_preview_cache = MagicMock()
        gui._unload_input_file()
        gui.input_path_var.set.assert_called_once_with('')
        gui.output_path_var.set.assert_called_once_with('')
        gui.image_frame.grid_remove.assert_called_once()
        gui.update_frame_preview.assert_called_once()

    # --- Clicking a queue entry previews that file (selection -> preview) ---

    def test_click_other_queue_item_loads_it_into_preview(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._load_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}, {'input': 'b.mkv'}]
        gui.batch_listbox.curselection.return_value = (1,)  # click the second file
        gui.on_batch_item_select()
        gui._load_input_file.assert_called_once_with('b.mkv')

    def test_click_already_shown_item_does_not_reload(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._load_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}, {'input': 'b.mkv'}]
        gui.batch_listbox.curselection.return_value = (0,)  # the file already on screen
        gui.on_batch_item_select()
        gui._load_input_file.assert_not_called()  # no spinner flash for the shown file

    def test_click_with_no_selection_is_a_noop(self):
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui._load_input_file = MagicMock()
        gui.batch_items = [{'input': 'a.mkv'}]
        gui.batch_listbox.curselection.return_value = ()  # selection cleared
        gui.on_batch_item_select()
        gui._load_input_file.assert_not_called()


class TestBatchProcessing(unittest.TestCase):
    """The queue converts files sequentially, advancing on each completion."""

    def _gui(self):
        gui = _bare_gui()
        gui.batch_items = []
        gui._refresh_batch_list = MagicMock()
        gui.gamma_var = MagicMock(); gui.gamma_var.get.return_value = 1.0
        gui.gpu_accel_var = MagicMock(); gui.gpu_accel_var.get.return_value = False
        gui.tonemap_var = MagicMock(); gui.tonemap_var.get.return_value = 'Mobius'
        gui.quality_var = MagicMock(); gui.quality_var.get.return_value = 20
        gui.quality_mode_var = MagicMock(); gui.quality_mode_var.get.return_value = 'Constant Quality'
        gui.bitrate_var = MagicMock(); gui.bitrate_var.get.return_value = 8000
        gui._source_bit_depth = 8
        gui._licensed = True
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.cancel_button = MagicMock()
        gui.drop_target_registered = True
        gui.unregister_drop_target = MagicMock()
        gui.register_drop_target = MagicMock()
        gui._load_input_file = MagicMock()  # preview follows the converting file
        return gui

    def _item(self, name, status='Pending'):
        return {'input': f'{name}.mkv', 'output': f'{name}_sdr.mkv',
                'format': 'MKV', 'status': status}

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_starts_first_pending_item(self, _isfile, mock_cm):
        gui = self._gui()
        gui.batch_items = [self._item('a'), self._item('b')]
        gui.start_batch()
        self.assertEqual(gui.batch_items[0]['status'], 'Converting')
        mock_cm.start_conversion.assert_called_once()
        kwargs = mock_cm.start_conversion.call_args.kwargs
        self.assertEqual(kwargs['on_complete'], gui._on_batch_item_complete)
        self.assertEqual(kwargs['quality'], 20)
        gui.unregister_drop_target.assert_called_once()  # like a single-file convert

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_forwards_bitrate_mode_and_value(self, _isfile, mock_cm):
        gui = self._gui()
        gui.quality_mode_var.get.return_value = 'Target Bitrate'
        gui.bitrate_var.get.return_value = 30000
        gui.batch_items = [self._item('a')]
        gui.start_batch()
        kwargs = mock_cm.start_conversion.call_args.kwargs
        self.assertEqual(kwargs['quality_mode'], 'bitrate')
        self.assertEqual(kwargs['quality'], 30000)

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_selects_ten_bit_for_high_bit_depth_source(self, _isfile, mock_cm):
        gui = self._gui()
        gui._source_bit_depth = 10
        gui.batch_items = [self._item('a')]
        gui.start_batch()
        kwargs = mock_cm.start_conversion.call_args.kwargs
        self.assertEqual(kwargs['bit_depth'], 10)

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_stays_8bit_for_8bit_source(self, _isfile, mock_cm):
        gui = self._gui()
        gui._source_bit_depth = 8
        gui.batch_items = [self._item('a')]
        gui.start_batch()
        kwargs = mock_cm.start_conversion.call_args.kwargs
        self.assertEqual(kwargs['bit_depth'], 8)

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_selects_twelve_bit_when_toggle_set(self, _isfile, mock_cm):
        gui = self._gui()
        gui._source_bit_depth = 12
        gui.bit_depth_var = MagicMock()
        gui.bit_depth_var.get.return_value = '12-bit'
        gui.batch_items = [self._item('a')]
        gui.start_batch()
        kwargs = mock_cm.start_conversion.call_args.kwargs
        self.assertEqual(kwargs['bit_depth'], 12)

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_requeues_a_fully_processed_queue(self, _isfile, mock_cm):
        # Re-running a queue whose items are all Done/Failed must requeue them and
        # actually convert again -- not jump straight to the completion summary.
        gui = self._gui()
        gui.batch_items = [self._item('a', 'Done'), self._item('b', 'Failed')]
        gui.start_batch()
        self.assertEqual(gui.batch_items[0]['status'], 'Converting')
        self.assertEqual(gui.batch_items[1]['status'], 'Pending')
        mock_cm.start_conversion.assert_called_once()

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_keeps_done_items_when_pending_remain(self, _isfile, mock_cm):
        # A partially-run queue (some Done, some Pending) must resume the pending
        # work without re-running the items already finished.
        gui = self._gui()
        gui.batch_items = [self._item('a', 'Done'), self._item('b', 'Pending')]
        gui.start_batch()
        self.assertEqual(gui.batch_items[0]['status'], 'Done')       # untouched
        self.assertEqual(gui.batch_items[1]['status'], 'Converting')
        mock_cm.start_conversion.assert_called_once()

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_complete_advances_to_next_item(self, _isfile, mock_cm):
        mock_cm.cancelled = False
        gui = self._gui()
        gui.batch_items = [self._item('a', 'Converting'), self._item('b')]
        gui._current_batch_item = gui.batch_items[0]
        gui._on_batch_item_complete(True)
        self.assertEqual(gui.batch_items[0]['status'], 'Done')
        self.assertEqual(gui.batch_items[1]['status'], 'Converting')
        mock_cm.start_conversion.assert_called_once()  # next item kicked off

    @patch('src.batch.messagebox')
    @patch('src.batch.conversion_manager')
    def test_complete_finishes_when_no_pending_left(self, mock_cm, mock_mb):
        mock_cm.cancelled = False
        gui = self._gui()
        gui.batch_items = [self._item('a', 'Converting')]
        gui._current_batch_item = gui.batch_items[0]
        gui._on_batch_item_complete(False)
        self.assertEqual(gui.batch_items[0]['status'], 'Failed')
        mock_cm.start_conversion.assert_not_called()  # queue exhausted
        mock_mb.showinfo.assert_called_once()          # single summary dialog
        gui.cancel_button.grid_remove.assert_called_once()
        gui.register_drop_target.assert_called_once()

    @patch('src.batch.messagebox')
    @patch('src.batch.conversion_manager')
    def test_missing_input_is_failed_and_skipped(self, mock_cm, mock_mb):
        gui = self._gui()
        gui.batch_items = [self._item('gone'), self._item('b')]
        with patch('src.gui.os.path.isfile', side_effect=lambda p: 'b.mkv' in p):
            gui.start_batch()
        self.assertEqual(gui.batch_items[0]['status'], 'Failed')
        self.assertEqual(gui.batch_items[1]['status'], 'Converting')
        mock_cm.start_conversion.assert_called_once()

    @patch('src.gui.conversion_manager')
    def test_convert_video_runs_batch_when_queue_nonempty(self, mock_cm):
        gui = self._gui()
        gui.batch_items = [self._item('a')]
        gui.start_batch = MagicMock()
        gui.convert_video()
        gui.start_batch.assert_called_once()
        mock_cm.start_conversion.assert_not_called()  # single-file path skipped

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_loads_converting_file_into_preview(self, _isfile, mock_cm):
        # The preview should switch to whichever file is currently converting.
        gui = self._gui()
        gui.batch_items = [self._item('a'), self._item('b')]
        gui.start_batch()
        gui._load_input_file.assert_called_once_with('a.mkv')

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_does_not_reload_already_previewed_file(self, _isfile, mock_cm):
        # The top file was already loaded when added; starting the batch must not
        # re-render it (no spinner flash on the file already on screen).
        gui = self._gui()
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'a.mkv'
        gui.batch_items = [self._item('a'), self._item('b')]
        gui.start_batch()
        gui._load_input_file.assert_not_called()
        mock_cm.start_conversion.assert_called_once()  # conversion still starts

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_advance_loads_next_file_into_preview(self, _isfile, mock_cm):
        mock_cm.cancelled = False
        gui = self._gui()
        gui.batch_items = [self._item('a', 'Converting'), self._item('b')]
        gui._current_batch_item = gui.batch_items[0]
        gui._on_batch_item_complete(True)
        gui._load_input_file.assert_called_once_with('b.mkv')  # moved on to file b

    @patch('src.batch.messagebox')
    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_finished_batch_does_not_reload_preview(self, _isfile, mock_cm, _mb):
        gui = self._gui()
        gui.batch_items = [self._item('a', 'Converting')]
        gui._current_batch_item = gui.batch_items[0]
        gui._on_batch_item_complete(True)
        gui._load_input_file.assert_not_called()  # nothing left to preview

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_conversion_raising_marks_item_failed_and_continues(
            self, _isfile, mock_cm):
        mock_cm.start_conversion.side_effect = [
            ValueError("spline requires GPU tonemapping; this item's "
                       "settings force CPU processing — change the "
                       "tonemapper or output bit depth."),
            None,
        ]
        gui = self._gui()
        gui.batch_items = [self._item('a'), self._item('b')]
        gui.start_batch()
        self.assertEqual(gui.batch_items[0]['status'], 'Failed')
        self.assertEqual(gui.batch_items[1]['status'], 'Converting')
        self.assertEqual(mock_cm.start_conversion.call_count, 2)


class TestPreviewExtractionCache(unittest.TestCase):
    """Extracted frames are cached by (path, time, tonemapper) so
    revisiting a frame/tonemapper combo never re-runs ffmpeg."""

    @patch('src.preview.extract_frame_with_conversion', return_value='conv')
    @patch('src.preview.extract_frame', return_value='orig')
    def test_repeated_combo_is_a_cache_hit(self, mock_extract, mock_convert):
        gui = _bare_gui()
        first = gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
        second = gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
        self.assertEqual(mock_extract.call_count, 1)   # original cached
        self.assertEqual(mock_convert.call_count, 1)   # converted cached
        self.assertEqual(first, second)

    @patch('src.preview.extract_frame_with_conversion', side_effect=['c0', 'c1'])
    @patch('src.preview.extract_frame', return_value='orig')
    def test_same_frame_new_tonemapper_reuses_original(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
        gui._extract_preview_images('in.mp4', 5.0, 'mobius')
        self.assertEqual(mock_extract.call_count, 1)   # original shared across tonemappers
        self.assertEqual(mock_convert.call_count, 2)   # converted differs per tonemapper

    @patch('src.preview.extract_frame_with_conversion', side_effect=['a', 'b'])
    @patch('src.preview.extract_frame', return_value='orig')
    def test_different_tonemapper_is_a_cache_miss(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 'mobius')
        gui._extract_preview_images('in.mp4', 5.0, 'hable')
        self.assertEqual(mock_convert.call_count, 2)

    @patch('src.preview.extract_frame_with_conversion', return_value='c')
    @patch('src.preview.extract_frame', return_value='o')
    def test_new_frame_position_reextracts_original(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
        gui._extract_preview_images('in.mp4', 9.0, 'reinhard')
        self.assertEqual(mock_extract.call_count, 2)

    def test_reset_clears_the_cache(self):
        gui = _bare_gui()
        with patch('src.preview.extract_frame', return_value='o'), \
             patch('src.preview.extract_frame_with_conversion', return_value='c'):
            gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
        gui._reset_preview_cache()
        with patch('src.preview.extract_frame', return_value='o2') as me, \
             patch('src.preview.extract_frame_with_conversion', return_value='c2') as mc:
            gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
            me.assert_called_once()   # re-extracted after reset
            mc.assert_called_once()

    def test_cache_is_bounded(self):
        gui = _bare_gui()
        with patch('src.preview.extract_frame', return_value='o'), \
             patch('src.preview.extract_frame_with_conversion', return_value='c'):
            for i in range(HDRConverterGUI._PREVIEW_CACHE_MAX + 20):
                gui._extract_preview_images('in.mp4', float(i), 'reinhard')
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

    @patch('src.preview.extract_frame_with_conversion', return_value='converted')
    @patch('src.preview.extract_frame', return_value='original')
    def test_extraction_targets_preview_resolution(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 'reinhard')
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
        gui.tonemap_var = MagicMock()
        gui.tonemap_var.get.return_value = 'Mobius'
        gui._extract_preview_images = MagicMock(return_value=('o', 'c'))
        gui._render_preview_images = MagicMock()

        with patch('src.preview.get_video_properties',
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
        gui.tonemap_var = MagicMock(); gui.tonemap_var.get.return_value = 'Mobius'
        gui.quality_var = MagicMock(); gui.quality_var.get.return_value = 21
        gui.quality_mode_var = MagicMock(); gui.quality_mode_var.get.return_value = 'Constant Quality'
        gui.bitrate_var = MagicMock(); gui.bitrate_var.get.return_value = 8000
        gui.format_var = MagicMock(); gui.format_var.get.return_value = 'MKV'
        gui._source_bit_depth = 8
        gui._licensed = True
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

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_output_container_governs_extension(self, _isfile, _exists, mock_mb, mock_cm):
        # The format dropdown now sets the container; no silent WebM notice.
        gui = self._gui()
        gui.output_path_var.get.return_value = 'out.webm'
        gui.format_var.get.return_value = 'MKV'
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        mock_mb.showinfo.assert_not_called()             # no surprise dialog
        gui.output_path_var.set.assert_called_with('out.mkv')
        mock_cm.start_conversion.assert_called_once()

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_convert_passes_quality_and_container(self, _isfile, _exists, mock_mb, mock_cm):
        gui = self._gui()
        gui.quality_var.get.return_value = 19
        gui.format_var.get.return_value = 'MP4'
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        _, kwargs = mock_cm.start_conversion.call_args
        self.assertEqual(kwargs['quality'], 19)
        gui.output_path_var.set.assert_called_with('out.mp4')  # MP4 container forced

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_convert_passes_bitrate_value_and_mode(self, _isfile, _exists, mock_mb, mock_cm):
        gui = self._gui()
        gui.quality_mode_var.get.return_value = 'Target Bitrate'
        gui.bitrate_var.get.return_value = 42000
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        _, kwargs = mock_cm.start_conversion.call_args
        self.assertEqual(kwargs['quality_mode'], 'bitrate')
        self.assertEqual(kwargs['quality'], 42000)

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_convert_selects_ten_bit_for_high_bit_depth_source_by_default(
            self, _isfile, _exists, _mock_mb, mock_cm):
        """Above 10-bit, without an explicit toggle choice, the default is
        10-bit (the toggle itself defaults to '10-bit' each time it appears)."""
        gui = self._gui()
        gui._source_bit_depth = 12
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        _, kwargs = mock_cm.start_conversion.call_args
        self.assertEqual(kwargs['bit_depth'], 10)

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_convert_selects_twelve_bit_when_toggle_set(
            self, _isfile, _exists, _mock_mb, mock_cm):
        gui = self._gui()
        gui._source_bit_depth = 12
        gui.bit_depth_var = MagicMock()
        gui.bit_depth_var.get.return_value = '12-bit'
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        _, kwargs = mock_cm.start_conversion.call_args
        self.assertEqual(kwargs['bit_depth'], 12)

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_convert_stays_8bit_for_8bit_source(
            self, _isfile, _exists, _mock_mb, mock_cm):
        gui = self._gui()
        gui._source_bit_depth = 8
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        _, kwargs = mock_cm.start_conversion.call_args
        self.assertEqual(kwargs['bit_depth'], 8)

    @patch('src.gui.conversion_manager')
    @patch('src.gui.messagebox')
    @patch('src.gui.os.path.exists', return_value=False)
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_convert_caps_at_ten_bit_when_unlicensed_even_with_high_bit_depth_source(
            self, _isfile, _exists, _mock_mb, mock_cm):
        """Free tier still gets 10-bit for a high-bit-depth source -- only
        12-bit (toggle disabled in that case) is Pro-gated."""
        gui = self._gui()
        gui._licensed = False
        gui._source_bit_depth = 12
        gui.drop_target_registered = False
        gui.cancel_button = MagicMock()
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False

        gui.convert_video()

        _, kwargs = mock_cm.start_conversion.call_args
        self.assertEqual(kwargs['bit_depth'], 10)


class TestGuiErrorAndResizePaths(unittest.TestCase):

    def test_handle_preview_error_reports_and_clears(self):
        gui = _bare_gui()
        gui.error_label = MagicMock()
        gui.clear_preview = MagicMock()
        gui.original_title_label = MagicMock()
        gui.converted_title_label = MagicMock()
        gui.button_container = MagicMock()
        gui._hide_preview_loading = MagicMock()
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
        mock_cm.is_gpu_acceleration_available.side_effect = RuntimeError('nope')
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
        gui.tonemap_combobox = MagicMock()
        gui._show_preview_loading = MagicMock()

        gui.update_frame_preview()

        gui.display_frames.assert_called_once_with('in.mkv')
        gui.arrange_widgets.assert_called_once_with(image_frame=True)

    def test_adjust_window_size_shrinks_when_exceeding_screen(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._window_auto_fitted = False
        gui.root.winfo_screenwidth.return_value = 1920
        gui.root.winfo_screenheight.return_value = 1080
        gui.root.winfo_width.return_value = 5000   # oversize -> trigger image shrink
        gui.root.winfo_height.return_value = 3000
        gui.resize_images = MagicMock()
        gui.adjust_window_size()
        gui.resize_images.assert_called_once_with(1820, 980)  # screen minus margin
        # minsize stays small so the user can always drag below the previews
        gui.root.minsize.assert_called_with(*DEFAULT_MIN_SIZE)

    def test_adjust_window_size_only_auto_fits_once(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._window_auto_fitted = True  # already fitted on a prior preview
        gui.adjust_window_size()
        gui.root.geometry.assert_not_called()  # don't yank a user-resized window
        gui.root.minsize.assert_called_once_with(*DEFAULT_MIN_SIZE)

    def test_adjust_window_size_skips_geometry_when_maximized(self):
        # Regression: fullscreen-first then load file caused geometry("") to
        # un-maximize the window, leaving previews stuck at INITIAL_PANE_SIZE.
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._window_auto_fitted = False
        gui.root.wm_state.return_value = 'zoomed'
        gui._rescale_preview_to_window = MagicMock()
        gui.adjust_window_size()
        gui.root.geometry.assert_not_called()
        self.assertTrue(gui._window_auto_fitted)
        gui._rescale_preview_to_window.assert_called_once()


class TestResponsivePreview(unittest.TestCase):
    """Previews scale to the window (issue 1) with a debounced resize."""

    def test_fit_pane_caps_at_source_size(self):
        # Plenty of room -> never upscale past the native preview size.
        self.assertEqual(
            HDRConverterGUI._fit_preview_pane(5000, 5000), PREVIEW_SIZE)

    def test_fit_pane_scales_to_width(self):
        w, h = HDRConverterGUI._fit_preview_pane(600, 5000)
        self.assertEqual(w, 600)
        self.assertEqual(h, round(600 * PREVIEW_SIZE[1] / PREVIEW_SIZE[0]))  # 16:9

    def test_fit_pane_is_height_limited(self):
        w, h = HDRConverterGUI._fit_preview_pane(900, 300)
        self.assertEqual(h, 300)
        self.assertEqual(w, round(300 * PREVIEW_SIZE[0] / PREVIEW_SIZE[1]))

    def test_fit_pane_clamps_to_minimum_width(self):
        from src.gui import _MIN_PANE_W
        w, _h = HDRConverterGUI._fit_preview_pane(10, 5000)
        self.assertEqual(w, _MIN_PANE_W)  # don't shrink a pane to nothing

    @patch('src.gui.ImageTk.PhotoImage')
    def test_render_at_size_resizes_both_panes(self, _mock_photo):
        gui = _bare_gui()
        img = MagicMock(spec=Image.Image)
        img.resize.return_value = img
        gui.original_image = img
        gui.converted_image_base = img
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui._apply_gamma_to_preview = MagicMock()
        gui._render_preview_at_size((400, 225))
        img.resize.assert_any_call((400, 225), Image.LANCZOS)
        gui.original_image_label.config.assert_called_once()
        # SDR base re-cached at the new size, then gamma applied on top.
        gui._apply_gamma_to_preview.assert_called_once()

    def test_render_at_size_noop_without_frame(self):
        gui = _bare_gui()
        gui.original_image = None
        gui.original_image_label = MagicMock()
        gui._render_preview_at_size((400, 225))
        gui.original_image_label.config.assert_not_called()

    def test_configure_ignores_child_widget_events(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.original_image = MagicMock()
        event = MagicMock(); event.widget = MagicMock()  # a child, not the root
        gui._on_window_configure(event)
        gui.root.after.assert_not_called()

    def test_configure_noop_without_preview(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.original_image = None
        event = MagicMock(); event.widget = gui.root
        gui._on_window_configure(event)
        gui.root.after.assert_not_called()

    def test_configure_debounces_pending_rescale(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.root.after.return_value = 'job2'
        gui.original_image = MagicMock()
        gui._resize_job = 'job1'
        event = MagicMock(); event.widget = gui.root
        gui._on_window_configure(event)
        gui.root.after_cancel.assert_called_once_with('job1')  # cancel the stale one
        gui.root.after.assert_called_once()
        self.assertEqual(gui._resize_job, 'job2')

    def test_target_size_from_live_frame_geometry(self):
        gui = _bare_gui()
        gui.image_frame = MagicMock()
        gui.image_frame.winfo_width.return_value = 1360
        gui.image_frame.winfo_height.return_value = 800
        w, h = gui._preview_target_size()
        # (1360 - reserve)/2 per pane, capped at source, 16:9.
        self.assertLessEqual(w, PREVIEW_SIZE[0])
        self.assertEqual(h, round(w * PREVIEW_SIZE[1] / PREVIEW_SIZE[0]))

    def test_target_size_falls_back_before_layout(self):
        gui = _bare_gui()
        gui.image_frame = MagicMock()
        gui.image_frame.winfo_width.return_value = 1  # not laid out yet
        self.assertEqual(gui._preview_target_size(), PREVIEW_SIZE)

    def test_rescale_renders_at_target_size(self):
        gui = _bare_gui()
        gui.original_image = MagicMock()
        gui._preview_target_size = MagicMock(return_value=(320, 180))
        gui._render_preview_at_size = MagicMock()
        gui._resize_job = 'job'
        gui._rescale_preview_to_window()
        gui._render_preview_at_size.assert_called_once_with((320, 180))
        self.assertIsNone(gui._resize_job)

    def test_initial_preview_size_is_generous(self):
        # Issue 1: the first preview should open noticeably larger than the
        # minimum pane, so freshly added files aren't tiny thumbnails.
        from src.gui import INITIAL_PANE_SIZE, _MIN_PANE_W
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.root.winfo_screenwidth.return_value = 3840  # plenty of room
        self.assertEqual(gui._initial_preview_size(), INITIAL_PANE_SIZE)
        self.assertGreater(INITIAL_PANE_SIZE[0], _MIN_PANE_W)

    def test_initial_preview_size_capped_to_screen(self):
        from src.gui import INITIAL_PANE_SIZE
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.root.winfo_screenwidth.return_value = 800  # narrow screen
        w, h = gui._initial_preview_size()
        self.assertLess(w, INITIAL_PANE_SIZE[0])  # capped down so two panes fit
        self.assertEqual(h, round(w * PREVIEW_SIZE[1] / PREVIEW_SIZE[0]))  # 16:9

    def test_initial_preview_size_defaults_without_real_screen(self):
        from src.gui import INITIAL_PANE_SIZE
        gui = _bare_gui()
        gui.root = MagicMock()  # winfo_screenwidth returns a MagicMock, not int
        self.assertEqual(gui._initial_preview_size(), INITIAL_PANE_SIZE)

    def test_first_reveal_renders_at_initial_size(self):
        gui = _bare_gui()
        gui._window_auto_fitted = False
        gui._initial_preview_size = MagicMock(return_value=(640, 360))
        gui._preview_target_size = MagicMock(return_value=(200, 113))
        gui._render_preview_at_size = MagicMock()
        gui._hide_preview_loading = MagicMock()
        gui._reveal_preview = MagicMock()
        gui.adjust_window_size = MagicMock()
        gui._render_preview_images(MagicMock(), MagicMock(), time_position=5.0)
        gui._render_preview_at_size.assert_called_once_with((640, 360))
        gui._preview_target_size.assert_not_called()

    def test_later_reveal_renders_at_target_size(self):
        gui = _bare_gui()
        gui._window_auto_fitted = True
        gui._initial_preview_size = MagicMock()
        gui._preview_target_size = MagicMock(return_value=(200, 113))
        gui._render_preview_at_size = MagicMock()
        gui._hide_preview_loading = MagicMock()
        gui._reveal_preview = MagicMock()
        gui.adjust_window_size = MagicMock()
        gui._render_preview_images(MagicMock(), MagicMock(), time_position=5.0)
        gui._render_preview_at_size.assert_called_once_with((200, 113))
        gui._initial_preview_size.assert_not_called()


class TestMinWindowSize(unittest.TestCase):
    """Min window size is derived from the controls so they can't be clipped (issue 3)."""

    @staticmethod
    def _frame(w, h):
        f = MagicMock()
        f.winfo_reqwidth.return_value = w
        f.winfo_reqheight.return_value = h
        return f

    def test_compute_min_from_chrome(self):
        from src.gui import _MIN_SIZE_MARGIN
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.control_frame = self._frame(700, 200)
        gui.batch_frame = self._frame(500, 120)
        gui.action_frame = self._frame(400, 60)
        w, h = gui._compute_min_window_size()
        # width = widest chrome frame, height = the three stacked (+ margin)
        self.assertEqual(w, 700 + _MIN_SIZE_MARGIN[0])
        self.assertEqual(h, 200 + 120 + 60 + _MIN_SIZE_MARGIN[1])

    def test_compute_min_floors_at_default(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.control_frame = self._frame(100, 30)
        gui.batch_frame = self._frame(80, 20)
        gui.action_frame = self._frame(60, 10)
        self.assertEqual(gui._compute_min_window_size(), DEFAULT_MIN_SIZE)

    def test_compute_min_falls_back_on_mocked_geometry(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.control_frame = MagicMock()  # winfo_reqwidth -> MagicMock, not int
        gui.batch_frame = MagicMock()
        gui.action_frame = MagicMock()
        self.assertEqual(gui._compute_min_window_size(), DEFAULT_MIN_SIZE)

    def test_apply_min_uses_computed_size(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._min_window_size = (777, 321)
        gui._apply_min_window_size()
        gui.root.minsize.assert_called_once_with(777, 321)

    def test_apply_min_defaults_before_layout(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui._apply_min_window_size()
        gui.root.minsize.assert_called_once_with(*DEFAULT_MIN_SIZE)


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

    @patch('src.gui.save_settings')
    def test_save_current_settings_includes_quality(self, mock_save):
        gui = _bare_gui()
        for name, val in [('gamma_var', 1.0),
                          ('tonemap_var', 'Mobius'), ('gpu_accel_var', False),
                          ('open_after_conversion_var', False), ('display_image_var', True),
                          ('quality_var', 21), ('format_var', 'MKV'),
                          ('quality_mode_var', 'Constant Quality'), ('bitrate_var', 15000)]:
            m = MagicMock(); m.get.return_value = val
            setattr(gui, name, m)
        gui._save_current_settings()
        self.assertEqual(mock_save.call_args[0][0]['quality'], 21)
        self.assertEqual(mock_save.call_args[0][0]['filetype'], 'MKV')
        self.assertEqual(mock_save.call_args[0][0]['quality_mode'], 'cq')
        self.assertEqual(mock_save.call_args[0][0]['quality_bitrate_kbps'], 15000)

    @patch('src.gui.save_settings')
    def test_save_current_settings_persists_bitrate_mode_choice(self, mock_save):
        gui = _bare_gui()
        for name, val in [('gamma_var', 1.0),
                          ('tonemap_var', 'Mobius'), ('gpu_accel_var', False),
                          ('open_after_conversion_var', False), ('display_image_var', True),
                          ('quality_var', 21), ('format_var', 'MKV'),
                          ('quality_mode_var', 'Target Bitrate'), ('bitrate_var', 42000)]:
            m = MagicMock(); m.get.return_value = val
            setattr(gui, name, m)
        gui._save_current_settings()
        self.assertEqual(mock_save.call_args[0][0]['quality_mode'], 'bitrate')

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


class TestGammaSliderJump(unittest.TestCase):
    """Clicking the slider trough must move the knob to the click, not nudge it."""

    def _slider(self, element, width=200, lo=0.1, hi=3.0):
        slider = MagicMock()
        slider.identify.return_value = element
        slider.winfo_width.return_value = width
        slider.cget.side_effect = lambda k: {'from': lo, 'to': hi}[k]
        return slider

    def test_trough_click_jumps_to_position(self):
        gui = _bare_gui()
        gui.gamma_slider = self._slider('Horizontal.Scale.trough')
        event = MagicMock(); event.x = 100  # halfway across width 200

        result = gui._gamma_slider_jump(event)

        gui.gamma_slider.set.assert_called_once()
        value = gui.gamma_slider.set.call_args[0][0]
        self.assertAlmostEqual(value, 0.1 + 0.5 * (3.0 - 0.1), places=4)  # 1.55
        self.assertEqual(result, 'break')  # suppress the default page-jump

    def test_click_on_knob_falls_through_to_drag(self):
        gui = _bare_gui()
        gui.gamma_slider = self._slider('Horizontal.Scale.slider')
        event = MagicMock(); event.x = 100

        result = gui._gamma_slider_jump(event)

        gui.gamma_slider.set.assert_not_called()  # let the native drag handle it
        self.assertIsNone(result)

    def test_click_is_clamped_within_range(self):
        gui = _bare_gui()
        gui.gamma_slider = self._slider('trough')
        event = MagicMock(); event.x = 500  # past the right edge

        gui._gamma_slider_jump(event)

        self.assertAlmostEqual(gui.gamma_slider.set.call_args[0][0], 3.0, places=4)


class TestQualitySliderJump(unittest.TestCase):
    """Clicking the quality trough snaps the knob to the nearest whole step at
    the cursor, instead of the default page-jump that slides on click-and-hold."""

    def _slider(self, element, width=200, frm=28, to=17):
        slider = MagicMock()
        slider.identify.return_value = element
        slider.winfo_width.return_value = width
        slider.cget.side_effect = lambda k: {'from': frm, 'to': to}[k]
        return slider

    def test_trough_click_snaps_to_nearest_step(self):
        gui = _bare_gui()
        gui.quality_slider = self._slider('Horizontal.Scale.trough')
        event = MagicMock(); event.x = 40  # 20% across width 200

        result = gui._quality_slider_jump(event)

        value = gui.quality_slider.set.call_args[0][0]
        self.assertEqual(value, round(28 + 0.2 * (17 - 28)))  # 25.8 -> 26
        self.assertEqual(value, int(value))                   # snapped to a whole step
        self.assertEqual(result, 'break')                     # suppress the page-jump

    def test_click_on_knob_falls_through_to_drag(self):
        gui = _bare_gui()
        gui.quality_slider = self._slider('Horizontal.Scale.slider')
        event = MagicMock(); event.x = 40

        result = gui._quality_slider_jump(event)

        gui.quality_slider.set.assert_not_called()  # let the native drag handle it
        self.assertIsNone(result)

    def test_click_is_clamped_within_range(self):
        gui = _bare_gui()
        gui.quality_slider = self._slider('trough')
        event = MagicMock(); event.x = 500  # past the right edge -> best end

        gui._quality_slider_jump(event)

        self.assertEqual(gui.quality_slider.set.call_args[0][0], 17)


class TestPreviewLoadingIndicator(unittest.TestCase):
    """A spinner shows while frames extract; titles/buttons hide until ready."""

    def _loading_widgets(self, gui):
        for attr in ('original_title_label', 'converted_title_label',
                     'button_container', 'original_image_label',
                     'converted_image_label', 'loading_frame', 'loading_bar'):
            setattr(gui, attr, MagicMock())

    def test_show_loading_hides_titles_buttons_and_starts_spinner(self):
        gui = _bare_gui()
        self._loading_widgets(gui)

        gui._show_preview_loading()

        gui.original_title_label.grid_remove.assert_called_once()
        gui.converted_title_label.grid_remove.assert_called_once()
        gui.button_container.grid_remove.assert_called_once()
        gui.loading_frame.grid.assert_called_once()
        gui.loading_bar.start.assert_called_once()

    def test_reveal_shows_titles_buttons_and_images(self):
        gui = _bare_gui()
        self._loading_widgets(gui)

        gui._reveal_preview()

        gui.original_title_label.grid.assert_called_once()
        gui.converted_title_label.grid.assert_called_once()
        gui.button_container.grid.assert_called_once()
        gui.original_image_label.grid.assert_called_once()
        gui.converted_image_label.grid.assert_called_once()

    def test_hide_loading_stops_spinner(self):
        gui = _bare_gui()
        self._loading_widgets(gui)

        gui._hide_preview_loading()

        gui.loading_bar.stop.assert_called_once()
        gui.loading_frame.grid_remove.assert_called_once()

    def test_render_hides_spinner_then_reveals(self):
        gui = _bare_gui()
        img = MagicMock(spec=Image.Image)
        img.resize.return_value = img
        gui.gamma_var = MagicMock(); gui.gamma_var.get.return_value = 1.0
        gui.adjust_gamma = MagicMock(return_value=img)
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui.adjust_window_size = MagicMock()
        gui._hide_preview_loading = MagicMock()
        gui._reveal_preview = MagicMock()

        with patch('src.gui.ImageTk.PhotoImage'):
            gui._render_preview_images(img, img, time_position=5.0)

        gui._hide_preview_loading.assert_called_once()
        gui._reveal_preview.assert_called_once()

    def test_stale_render_does_not_touch_loading(self):
        # A superseded worker (older generation) must not flip the spinner off.
        gui = _bare_gui()
        gui._preview_generation = 7
        gui._hide_preview_loading = MagicMock()
        gui._reveal_preview = MagicMock()

        gui._render_preview_images('o', 'c', 1.0, generation=4)

        gui._hide_preview_loading.assert_not_called()
        gui._reveal_preview.assert_not_called()

    def test_update_frame_preview_shows_loading_before_extraction(self):
        gui = _bare_gui()
        gui.display_image_var = MagicMock(); gui.display_image_var.get.return_value = True
        gui.input_path_var = MagicMock(); gui.input_path_var.get.return_value = 'in.mkv'
        gui.error_label = MagicMock()
        gui.arrange_widgets = MagicMock()
        gui.tonemap_combobox = MagicMock()
        gui._show_preview_loading = MagicMock()
        gui.display_frames = MagicMock()

        gui.update_frame_preview()

        gui._show_preview_loading.assert_called_once()
        gui.display_frames.assert_called_once_with('in.mkv')

    def test_handle_preview_error_hides_loading(self):
        gui = _bare_gui()
        gui.error_label = MagicMock()
        gui.clear_preview = MagicMock()
        gui.original_title_label = MagicMock()
        gui.converted_title_label = MagicMock()
        gui.button_container = MagicMock()
        gui._hide_preview_loading = MagicMock()

        gui.handle_preview_error(ValueError('boom'))

        gui._hide_preview_loading.assert_called_once()


# ── Coverage gap fill-ins ──────────────────────────────────────────────────────

class TestBuildInfoText(unittest.TestCase):
    """_build_info_text formats video metadata into a compact one-liner."""

    def test_hdr_label_from_bt2020_primaries(self):
        text = HDRConverterGUI._build_info_text({
            'width': 3840, 'height': 2160, 'frame_rate': 23.976,
            'codec_name': 'hevc', 'audio_codec': 'dts',
            'color_primaries': 'bt2020', 'color_transfer': '',
        })
        self.assertIn('HDR', text)
        self.assertIn('3840×2160', text)
        self.assertIn('HEVC', text)

    def test_hdr_label_from_smpte2084_transfer(self):
        text = HDRConverterGUI._build_info_text({
            'width': 1920, 'height': 1080, 'frame_rate': 24.0,
            'codec_name': 'hevc', 'audio_codec': 'aac',
            'color_primaries': '', 'color_transfer': 'smpte2084',
        })
        self.assertIn('HDR', text)

    def test_sdr_label_for_standard_primaries(self):
        text = HDRConverterGUI._build_info_text({
            'width': 1920, 'height': 1080, 'frame_rate': 30.0,
            'codec_name': 'h264', 'audio_codec': 'aac',
            'color_primaries': 'bt709', 'color_transfer': 'bt709',
        })
        self.assertIn('SDR', text)

    def test_missing_fields_show_question_marks(self):
        text = HDRConverterGUI._build_info_text({})
        self.assertIn('?×?', text)
        self.assertIn('? fps', text)


class TestUpdateInfoLabel(unittest.TestCase):
    """_update_info_label reads metadata and updates the info strip."""

    def test_shows_text_when_props_available(self):
        gui = _bare_gui()
        gui.info_label = MagicMock()
        props = {
            'width': 1920, 'height': 1080, 'frame_rate': 24.0,
            'codec_name': 'hevc', 'audio_codec': 'aac',
            'color_primaries': 'bt2020', 'color_transfer': '',
        }
        with patch('src.gui.get_video_properties', return_value=props), \
             patch('src.gui.get_maxcll', return_value=400.0):
            gui._update_info_label('clip.mkv')
        gui.info_label.config.assert_called_once()
        gui.info_label.grid.assert_called_once()

    def test_hides_label_when_probe_fails(self):
        gui = _bare_gui()
        gui.info_label = MagicMock()
        with patch('src.gui.get_video_properties', return_value=None):
            gui._update_info_label('bad.mkv')
        gui.info_label.grid_remove.assert_called_once()

    def test_stores_source_bit_depth_for_later_auto_ten_bit_decision(self):
        """The probed bit depth is remembered so convert_video/start_batch can
        pick the output bit depth automatically without re-probing the file."""
        gui = _bare_gui()
        gui.info_label = MagicMock()
        props = {
            'width': 3840, 'height': 2160, 'frame_rate': 23.976,
            'codec_name': 'hevc', 'audio_codec': 'truehd',
            'color_primaries': 'bt2020', 'color_transfer': 'smpte2084',
            'bit_depth': 12,
        }
        with patch('src.gui.get_video_properties', return_value=props), \
             patch('src.gui.get_maxcll', return_value=400.0):
            gui._update_info_label('clip.mkv')
        self.assertEqual(gui._source_bit_depth, 12)

    def test_source_bit_depth_defaults_to_8_when_probe_fails(self):
        gui = _bare_gui()
        gui.info_label = MagicMock()
        with patch('src.gui.get_video_properties', return_value=None):
            gui._update_info_label('bad.mkv')
        self.assertEqual(gui._source_bit_depth, 8)


class TestHandleFileDropGuards(unittest.TestCase):
    """handle_file_drop early-exit conditions."""

    def test_ignores_drop_when_not_registered(self):
        gui = _bare_gui()
        gui.drop_target_registered = False
        event = MagicMock(); event.data = '{C:/a.mkv}'
        gui.handle_file_drop(event)  # must not raise

    def test_ignores_empty_data(self):
        gui = _bare_gui()
        gui.drop_target_registered = True
        event = MagicMock(); event.data = ''
        gui.handle_file_drop(event)  # no paths → no-op

    def test_multi_drop_unlicensed_shows_pro_prompt(self):
        gui = _bare_gui()
        gui.drop_target_registered = True
        gui._licensed = False
        event = MagicMock(); event.data = 'C:/a.mkv C:/b.mkv'
        with patch('src.gui.messagebox.showinfo') as mock_info:
            gui.handle_file_drop(event)
        mock_info.assert_called_once()
        self.assertIn('Pro', mock_info.call_args[0][1])


class TestApplyLicenseStateUnlicensed(unittest.TestCase):
    """_apply_license_state(False) disables Pro widgets and forces MP4."""

    def _gui(self, output_path=''):
        gui = _bare_gui()
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_checkbutton = MagicMock()
        gui.quality_slider = MagicMock()
        gui.quality_mode_combobox = MagicMock()
        gui._apply_quality_range = MagicMock()
        gui.format_var = MagicMock()
        gui.format_combobox = MagicMock()
        gui.output_path_var = MagicMock()
        gui.output_path_var.get.return_value = output_path
        gui._output_path_with_format = MagicMock(return_value='out_sdr.mp4')
        gui.custom_time_entry = MagicMock()
        gui.custom_seek_button = MagicMock()
        gui.add_files_button = MagicMock()
        gui.remove_batch_button = MagicMock()
        gui.clear_batch_button = MagicMock()
        gui._rebuild_interactable_elements = MagicMock()
        gui._pro_banner = MagicMock()
        return gui

    def test_gpu_not_disabled_when_unlicensed(self):
        # GPU acceleration is free; unlicensed state must not force it off.
        gui = self._gui()
        gui._apply_license_state(False)
        gui.gpu_accel_var.set.assert_not_called()

    def test_resets_format_to_mp4(self):
        gui = self._gui()
        gui._apply_license_state(False)
        gui.format_var.set.assert_called_with('MP4')

    def test_rewrites_output_path_when_set(self):
        gui = self._gui(output_path='C:/foo/clip_sdr.mkv')
        gui._apply_license_state(False)
        gui.output_path_var.set.assert_called_once()

    def test_shows_pro_banner(self):
        gui = self._gui()
        gui._apply_license_state(False)
        gui._pro_banner.grid.assert_called_once()


class TestArrangeWidgets(unittest.TestCase):
    """arrange_widgets places the buttons and progress bar correctly."""

    def _gui(self):
        gui = _bare_gui()
        gui.button_frame = MagicMock()
        gui.progress_bar = MagicMock()
        gui.open_after_conversion_checkbutton = MagicMock()
        gui.convert_button = MagicMock()
        gui.cancel_button = MagicMock()
        return gui

    def test_image_frame_true_uses_row_2(self):
        gui = self._gui()
        gui.arrange_widgets(image_frame=True)
        self.assertEqual(gui.button_frame.grid.call_args.kwargs['row'], 2)
        gui.cancel_button.grid_remove.assert_called_once()

    def test_image_frame_false_uses_row_5(self):
        gui = self._gui()
        gui.arrange_widgets(image_frame=False)
        self.assertEqual(gui.button_frame.grid.call_args.kwargs['row'], 5)


class TestUpdateFramePreviewElseBranch(unittest.TestCase):
    """update_frame_preview clears the screen when display is off or no input."""

    def _gui(self, display=False, input_path=''):
        gui = _bare_gui()
        gui.display_image_var = MagicMock()
        gui.display_image_var.get.return_value = display
        gui.input_path_var = MagicMock()
        gui.input_path_var.get.return_value = input_path
        gui.clear_preview = MagicMock()
        gui._hide_preview_loading = MagicMock()
        gui._show_preview_loading = MagicMock()
        gui.original_title_label = MagicMock()
        gui.converted_title_label = MagicMock()
        gui.button_container = MagicMock()
        gui.arrange_widgets = MagicMock()
        gui.tonemap_combobox = MagicMock()
        return gui

    def test_display_off_clears_preview(self):
        gui = self._gui(display=False, input_path='clip.mkv')
        gui.update_frame_preview()
        gui.clear_preview.assert_called_once()
        gui.arrange_widgets.assert_called_once_with(image_frame=False)

    def test_empty_input_path_clears_preview(self):
        gui = self._gui(display=True, input_path='')
        gui.update_frame_preview()
        gui.clear_preview.assert_called_once()

    def test_display_frames_exception_calls_handle_preview_error(self):
        gui = self._gui(display=True, input_path='clip.mkv')
        gui.error_label = MagicMock()
        gui.display_frames = MagicMock(side_effect=RuntimeError('ffmpeg died'))
        gui.handle_preview_error = MagicMock()
        gui.update_frame_preview()
        gui.handle_preview_error.assert_called_once()


class TestResizeImages(unittest.TestCase):
    """resize_images scales both preview panes from cached full-res images."""

    def test_both_panes_scaled(self):
        gui = _bare_gui()
        gui.original_image = Image.new('RGB', (1920, 1080), (100, 120, 140))
        gui.converted_image_base = Image.new('RGB', (1920, 1080), (80, 100, 120))
        gui.gamma_var = MagicMock(); gui.gamma_var.get.return_value = 1.0
        gui.adjust_gamma = lambda img, g: img
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        with patch('src.gui.ImageTk.PhotoImage'):
            gui.resize_images(400, 300)
        gui.original_image_label.config.assert_called_once()
        gui.converted_image_label.config.assert_called_once()


class TestMiscCoverageGaps(unittest.TestCase):
    """Small individual line gaps."""

    def test_on_quality_change_snaps_float_to_int(self):
        gui = _bare_gui()
        gui.quality_var = MagicMock()
        gui.quality_mode_var = MagicMock()
        gui.quality_mode_var.get.return_value = 'Constant Quality'
        gui._on_quality_change('22.7')
        gui.quality_var.set.assert_called_once_with(22)

    def test_on_quality_change_snaps_to_500kbps_steps_in_bitrate_mode(self):
        gui = _bare_gui()
        gui.bitrate_var = MagicMock()
        gui.quality_mode_var = MagicMock()
        gui.quality_mode_var.get.return_value = 'Target Bitrate'
        gui._on_quality_change('12234.0')
        gui.bitrate_var.set.assert_called_once_with(12000)

    def test_hide_tooltip_destroys_existing_tooltip(self):
        gui = _bare_gui()
        mock_tip = MagicMock()
        gui.tooltip = mock_tip
        gui.hide_tooltip()
        mock_tip.destroy.assert_called_once()
        self.assertIsNone(gui.tooltip)

    def test_min_window_size_fallback_on_exception(self):
        gui = _bare_gui()
        gui.root = MagicMock()
        gui.root.update_idletasks.side_effect = RuntimeError('no display')
        result = gui._compute_min_window_size()
        self.assertEqual(result, DEFAULT_MIN_SIZE)

    def test_on_format_change_rewrites_extension(self):
        gui = _bare_gui()
        gui.output_path_var = MagicMock()
        gui.output_path_var.get.return_value = 'C:/foo/bar_sdr.mkv'
        gui.format_var = MagicMock(); gui.format_var.get.return_value = 'MP4'
        gui._output_path_with_format = MagicMock(return_value='C:/foo/bar_sdr.mp4')
        gui.format_combobox = MagicMock()
        gui._on_format_change()
        gui.output_path_var.set.assert_called_once_with('C:/foo/bar_sdr.mp4')
        gui.format_combobox.selection_clear.assert_called_once()

    def test_start_batch_returns_false_when_queue_empty(self):
        gui = _bare_gui()
        gui.batch_items = []
        self.assertFalse(gui.start_batch())

    def test_refresh_batch_list_no_op_without_listbox(self):
        gui = _bare_gui()
        gui._refresh_batch_list()  # must not raise

    def test_on_batch_item_select_no_op_without_listbox(self):
        gui = _bare_gui()
        gui.on_batch_item_select()  # must not raise

    def test_jump_slider_to_click_zero_width_is_no_op(self):
        gui = _bare_gui()
        slider = MagicMock()
        slider.identify.return_value = 'trough'  # not the knob
        slider.winfo_width.return_value = 0
        event = MagicMock(); event.x = 50
        gui._jump_slider_to_click(slider, event)
        slider.set.assert_not_called()


class TestUtilsStartupinfoNonWindows(unittest.TestCase):
    """_startupinfo returns (None, 0) on non-Windows."""

    def test_non_windows_returns_none_zero(self):
        from src.utils import _startupinfo
        with patch('sys.platform', 'linux'):
            si, flags = _startupinfo()
        self.assertIsNone(si)
        self.assertEqual(flags, 0)


class TestExtractFrameErrorPaths(unittest.TestCase):
    """extract_frame handles missing properties and bad image output."""

    @patch('src.utils.get_video_properties', return_value=None)
    def test_raises_value_error_when_properties_missing(self, _):
        from src.utils import extract_frame
        with self.assertRaises(ValueError):
            extract_frame('nonexistent.mp4')

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=b'not-an-image')
    def test_raises_runtime_error_on_bad_image_bytes(self, _run, _props):
        from src.utils import extract_frame
        with self.assertRaises(RuntimeError):
            extract_frame('clip.mp4')


class TestExtractAndConvertFrameErrorPaths(unittest.TestCase):
    """extract_frame_with_conversion handles missing properties and bad output."""

    @patch('src.utils.get_video_properties', return_value=None)
    def test_raises_value_error_when_properties_missing(self, _):
        from src.utils import extract_frame_with_conversion
        with self.assertRaises(ValueError):
            extract_frame_with_conversion('none.mp4', gamma=1.0)

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=b'not-an-image')
    def test_raises_runtime_error_on_bad_image_bytes(self, _run, _props):
        from src.utils import extract_frame_with_conversion
        with self.assertRaises(RuntimeError):
            extract_frame_with_conversion('clip.mp4', gamma=1.0)


class TestConversionManagerInternals(unittest.TestCase):
    """Private helpers on ConversionManager that were uncovered."""

    def test_monitor_progress_returns_early_when_proc_is_none(self):
        m = ConversionManager()
        m.process = None
        m.cancelled = False
        m.monitor_progress(
            MagicMock(), duration=90.0, gui_instance=MagicMock(),
            interactable_elements=[], cancel_button=MagicMock(),
            output_path='out.mkv', open_after_conversion=False, gamma=1.0,
        )

    def test_nvidia_present_true_when_smi_exits_zero(self):
        m = ConversionManager()
        with patch('subprocess.run') as mock_run, \
             patch.object(m, '_startupinfo', return_value=(None, 0)):
            mock_run.return_value = MagicMock(returncode=0)
            self.assertTrue(m._nvidia_present())

    def test_nvidia_present_false_when_smi_not_found(self):
        m = ConversionManager()
        with patch('subprocess.run', side_effect=FileNotFoundError):
            self.assertFalse(m._nvidia_present())

    def test_list_encoders_returns_empty_on_os_error(self):
        m = ConversionManager()
        with patch('subprocess.Popen', side_effect=OSError):
            self.assertEqual(m._list_encoders(), '')

    def test_list_encoders_returns_lowercase_stdout(self):
        m = ConversionManager()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ('H264_NVENC H264_AMF\n', '')
        mock_proc.returncode = 0
        with patch('subprocess.Popen', return_value=mock_proc), \
             patch.object(m, '_startupinfo', return_value=(None, 0)):
            result = m._list_encoders()
        self.assertIn('h264_nvenc', result)


class TestBatchPassesLicenseTier(unittest.TestCase):
    """Queue runs forward the license tier to the conversion layer — the
    Dolby Vision Pro-passthrough / Free-downmix audio split depends on it."""

    def _gui(self, licensed):
        gui = _bare_gui()
        gui._licensed = licensed
        gui.batch_items = [{'input': 'a.mkv', 'output': 'a_sdr.mkv',
                            'format': 'MKV', 'status': 'Pending'}]
        gui._refresh_batch_list = MagicMock()
        gui.gamma_var = MagicMock(); gui.gamma_var.get.return_value = 1.0
        gui.gpu_accel_var = MagicMock(); gui.gpu_accel_var.get.return_value = False
        gui.tonemap_var = MagicMock(); gui.tonemap_var.get.return_value = 'Mobius'
        gui.quality_var = MagicMock(); gui.quality_var.get.return_value = 20
        gui.quality_mode_var = MagicMock(); gui.quality_mode_var.get.return_value = 'Constant Quality'
        gui.bitrate_var = MagicMock(); gui.bitrate_var.get.return_value = 8000
        gui._source_bit_depth = 8
        gui.open_after_conversion_var = MagicMock()
        gui.open_after_conversion_var.get.return_value = False
        gui.progress_var = MagicMock()
        gui.interactable_elements = []
        gui.cancel_button = MagicMock()
        gui.drop_target_registered = False
        gui._load_input_file = MagicMock()
        return gui

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_forwards_pro_license(self, _isfile, mock_cm):
        gui = self._gui(licensed=True)
        gui.start_batch()
        self.assertIs(
            mock_cm.start_conversion.call_args.kwargs.get('licensed'), True)

    @patch('src.batch.conversion_manager')
    @patch('src.gui.os.path.isfile', return_value=True)
    def test_start_batch_forwards_free_license(self, _isfile, mock_cm):
        gui = self._gui(licensed=False)
        gui.start_batch()
        self.assertIs(
            mock_cm.start_conversion.call_args.kwargs.get('licensed'), False)


class TestSyncQualityDisplay(unittest.TestCase):
    """quality_display_var mirrors whichever backing var is active: a plain
    int string in Constant Quality mode, or a formatted 'N,NNN kbps' string
    in Target Bitrate mode."""

    def _gui(self):
        gui = _bare_gui()
        gui.quality_var = MagicMock(); gui.quality_var.get.return_value = 23
        gui.bitrate_var = MagicMock(); gui.bitrate_var.get.return_value = 12000
        gui.quality_mode_var = MagicMock()
        gui.quality_display_var = MagicMock()
        return gui

    def test_constant_quality_shows_plain_int(self):
        gui = self._gui()
        gui.quality_mode_var.get.return_value = 'Constant Quality'
        gui._sync_quality_display()
        gui.quality_display_var.set.assert_called_once_with('23')

    def test_target_bitrate_shows_formatted_kbps(self):
        gui = self._gui()
        gui.quality_mode_var.get.return_value = 'Target Bitrate'
        gui._sync_quality_display()
        gui.quality_display_var.set.assert_called_once_with('12,000 kbps')


class TestApplyQualityMode(unittest.TestCase):
    """_apply_quality_mode reconfigures the shared quality slider for whichever
    mode is selected: Constant Quality (existing CRF/CQ ranges, direct restore
    when just switched into) or Target Bitrate (1,000 kbps floor, source
    bitrate ceiling, 50%-seeded on first engagement)."""

    def _gui(self, mode='Constant Quality', gpu=False, cached_bit_rate=None):
        gui = _bare_gui()
        gui.quality_mode_var = MagicMock(); gui.quality_mode_var.get.return_value = mode
        gui.gpu_accel_var = MagicMock(); gui.gpu_accel_var.get.return_value = gpu
        gui.quality_var = MagicMock(); gui.quality_var.get.return_value = 23
        gui.bitrate_var = MagicMock(); gui.bitrate_var.get.return_value = 8000
        gui.quality_slider = MagicMock()
        gui.quality_slider.cget.side_effect = lambda k: {'from': 28.0, 'to': 17.0}[k]
        gui.quality_slider.get.return_value = 23.0
        gui._bitrate_seeded = True
        if cached_bit_rate is not None:
            gui._cached_props = {'bit_rate': cached_bit_rate}
        return gui

    # ── source bitrate helpers ──────────────────────────────────────────

    def test_source_bitrate_kbps_converts_bps_to_kbps(self):
        gui = self._gui(cached_bit_rate=84_376_000)
        self.assertEqual(gui._source_bitrate_kbps(), 84376)

    def test_source_bitrate_kbps_falls_back_when_zero(self):
        gui = self._gui(cached_bit_rate=0)
        self.assertEqual(gui._source_bitrate_kbps(), 8000)

    def test_source_bitrate_kbps_falls_back_when_unprobed(self):
        gui = self._gui()  # no _cached_props set at all
        self.assertEqual(gui._source_bitrate_kbps(), 8000)

    def test_bitrate_ceiling_rounds_to_nearest_500(self):
        gui = self._gui(cached_bit_rate=84_376_000)  # 84,376 kbps
        self.assertEqual(gui._bitrate_ceiling_kbps(), 84500)

    def test_bitrate_ceiling_never_below_floor(self):
        gui = self._gui(cached_bit_rate=500_000)  # 500 kbps, below the 1,000 floor
        self.assertEqual(gui._bitrate_ceiling_kbps(), 1000)

    # ── mode switch: Constant Quality -> Target Bitrate ─────────────────

    def test_switching_to_bitrate_mode_sets_range_and_restores_value(self):
        gui = self._gui(mode='Target Bitrate', cached_bit_rate=40_000_000)  # 40,000 kbps
        gui.bitrate_var.get.return_value = 15000
        gui._apply_quality_mode()
        gui.quality_slider.configure.assert_called_once_with(from_=1000, to=40000)
        gui.quality_slider.set.assert_called_once_with(15000)

    def test_bitrate_value_clamped_into_new_ceiling(self):
        # A previously-saved bitrate (e.g. from a much higher-bitrate file)
        # must be clamped down to the new file's lower ceiling.
        gui = self._gui(mode='Target Bitrate', cached_bit_rate=5_000_000)  # 5,000 kbps
        gui.bitrate_var.get.return_value = 50000
        gui._apply_quality_mode()
        gui.quality_slider.set.assert_called_once_with(5000)
        gui.bitrate_var.set.assert_called_with(5000)

    def test_first_engagement_seeds_fifty_percent_of_source(self):
        gui = self._gui(mode='Target Bitrate', cached_bit_rate=40_000_000)  # 40,000 kbps
        gui._bitrate_seeded = False
        gui._apply_quality_mode()
        gui.bitrate_var.set.assert_any_call(20000)  # 50% of 40,000, already a 500-multiple
        self.assertTrue(gui._bitrate_seeded)

    def test_seeding_only_happens_once(self):
        gui = self._gui(mode='Target Bitrate', cached_bit_rate=40_000_000)
        gui._bitrate_seeded = False
        gui._apply_quality_mode()
        gui.bitrate_var.set.reset_mock()
        gui.bitrate_var.get.return_value = 20000
        gui._apply_quality_mode()  # e.g. a second file probe -- must not reseed
        # A reseed would call bitrate_var.set twice here (once to seed 50% of
        # the source, once for the clamp passthrough) -- both landing on the
        # same 20000 value coincidentally, so only the call *count* (not the
        # value) can distinguish "seeded once" from "reseeded every call".
        gui.bitrate_var.set.assert_called_once_with(20000)  # clamp passthrough only, no fresh seed

    # ── mode switch: Target Bitrate -> Constant Quality ─────────────────

    def test_switching_back_to_cq_restores_directly_not_fractionally(self):
        # Slider is currently ranged for Target Bitrate (1,000-84,500); switching
        # back to Constant Quality must NOT fractionally remap that position --
        # it must restore quality_var's own value directly.
        gui = self._gui(mode='Constant Quality', gpu=False)
        gui.quality_slider.cget.side_effect = lambda k: {'from': 1000.0, 'to': 84500.0}[k]
        gui.quality_slider.get.return_value = 15000.0
        gui.quality_var.get.return_value = 19
        gui._apply_quality_mode()
        gui.quality_slider.configure.assert_called_once_with(from_=28, to=17)
        gui.quality_slider.set.assert_called_once_with(19)

    def test_switching_back_to_cq_uses_gpu_range_when_gpu_on(self):
        gui = self._gui(mode='Constant Quality', gpu=True)
        gui.quality_slider.cget.side_effect = lambda k: {'from': 1000.0, 'to': 84500.0}[k]
        gui.quality_slider.get.return_value = 15000.0
        gui.quality_var.get.return_value = 19
        gui._apply_quality_mode()
        gui.quality_slider.configure.assert_called_once_with(from_=30, to=15)

    def test_switching_back_to_cq_clamps_out_of_range_value(self):
        gui = self._gui(mode='Constant Quality', gpu=False)
        gui.quality_slider.cget.side_effect = lambda k: {'from': 1000.0, 'to': 84500.0}[k]
        gui.quality_var.get.return_value = 5  # better than CRF's best (17)
        gui._apply_quality_mode()
        gui.quality_slider.set.assert_called_once_with(17)
        gui.quality_var.set.assert_called_once_with(17)

    # ── GPU toggle while already in a mode (not a mode switch) ──────────

    def test_gpu_toggle_while_in_cq_mode_uses_existing_fractional_remap(self):
        gui = self._gui(mode='Constant Quality', gpu=False)
        gui._apply_quality_mode()  # first call: establishes 'Constant Quality' as active
        gui.quality_slider.configure.reset_mock()
        gui.quality_slider.set.reset_mock()
        gui.quality_slider.cget.side_effect = lambda k: {'from': 28.0, 'to': 17.0}[k]
        gui.quality_slider.get.return_value = 23.0
        gui.gpu_accel_var.get.return_value = True  # GPU toggled on, mode unchanged
        gui._apply_quality_mode()
        # _apply_quality_range's fractional-preserve math: fraction=(23-28)/(17-28)
        gui.quality_slider.configure.assert_called_once_with(from_=30, to=15)

    def test_gpu_toggle_while_in_bitrate_mode_is_a_noop(self):
        gui = self._gui(mode='Target Bitrate', gpu=False, cached_bit_rate=40_000_000)
        gui.bitrate_var.get.return_value = 20000
        gui._apply_quality_mode()  # establishes Target Bitrate as active, seeds if needed
        gui.quality_slider.configure.reset_mock()
        gui.quality_slider.set.reset_mock()
        gui.gpu_accel_var.get.return_value = True  # GPU toggled -- must not change bounds
        gui._apply_quality_mode()
        gui.quality_slider.configure.assert_called_once_with(from_=1000, to=40000)
        gui.quality_slider.set.assert_called_once_with(20000)

    def test_update_info_label_reapplies_bitrate_range_for_new_file(self):
        gui = self._gui(mode='Target Bitrate')
        gui.quality_slider = MagicMock()
        gui._bitrate_seeded = True
        gui.bitrate_var.get.return_value = 8000
        with patch('src.gui.get_video_properties',
                    return_value={'bit_rate': 40_000_000, 'bit_depth': 8}), \
             patch('src.gui.get_maxcll', return_value=None):
            gui.info_label = MagicMock()
            gui._update_bit_depth_choice = MagicMock()
            gui._refresh_info_label_text = MagicMock()
            gui._update_info_label('clip.mkv')
        gui.quality_slider.configure.assert_called_once_with(from_=1000, to=40000)


if __name__ == '__main__':
    unittest.main()
