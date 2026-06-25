"""Performance metric tests.

Two complementary kinds:

1. Structural guards (deterministic, no wall-clock): they assert the *number* of
   expensive operations (ffmpeg/ffprobe subprocess spawns, window resizes, output
   resolution). These encode the snappiness invariants and never flake.

2. Timing budgets (best-of-N, generous ceilings): they measure cheap hot-path
   operations and print a `[perf]` report, so a genuine regression trips them
   while normal jitter does not. The printed numbers double as a perf audit.

A real-ffmpeg extraction audit runs only when a sample video is present (skips on
CI), reporting the slow decode-bound path with a loose catastrophe ceiling.
"""
import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from PIL import Image

from src.gui import HDRConverterGUI, PREVIEW_SIZE, INITIAL_PANE_SIZE
from src.utils import (
    extract_frame, extract_frame_with_conversion, FFMPEG_EXECUTABLE,
    clear_maxfall_cache,
)


def _png_bytes():
    import io
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (1, 2, 3)).save(buf, format='PNG')
    return buf.getvalue()

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _bare_gui():
    return object.__new__(HDRConverterGUI)


def _best_ms(fn, runs=7):
    """Best-of-N wall time in ms (best run = least scheduler/jitter noise)."""
    best = float('inf')
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best * 1000.0


def _first_sample():
    for name in ('video.mkv', 'drag multi bo6.mp4'):
        path = os.path.join(PROJECT_ROOT, name)
        if os.path.exists(path):
            return path
    return None


_SAMPLE = _first_sample()
_FFMPEG_OK = bool(FFMPEG_EXECUTABLE) and os.path.exists(FFMPEG_EXECUTABLE)


class TestSnappinessGuards(unittest.TestCase):
    """Deterministic invariants: the interactions that must stay subprocess-free."""

    @patch('src.gui.ImageTk.PhotoImage')
    @patch('src.gui.get_video_properties')
    @patch('src.gui.extract_frame_with_conversion')
    @patch('src.gui.extract_frame')
    def test_gamma_change_spawns_no_ffmpeg(self, mock_extract, mock_convert,
                                           mock_props, _mock_photo):
        gui = _bare_gui()
        gui.display_image_var = MagicMock()
        gui.display_image_var.get.return_value = True
        gui.gamma_var = MagicMock()
        gui.gamma_var.get.return_value = 1.8
        gui._converted_preview_base = Image.new('RGB', PREVIEW_SIZE, (10, 20, 30))
        gui.converted_image_label = MagicMock()

        gui.on_gamma_change()

        # The whole point of the gamma fast path: zero ffmpeg/ffprobe work.
        mock_extract.assert_not_called()
        mock_convert.assert_not_called()
        mock_props.assert_not_called()

    @patch('src.utils.run_ffmpeg_command')
    @patch('src.utils.get_video_properties', return_value={'duration': 100, 'width': 1920, 'height': 1080})
    @patch('src.utils._probe_hdr_metadata', return_value={'maxcll': 200.0, 'maxfall': None, 'mastering_peak': None})
    def test_maxfall_not_probed_during_preview_extraction(self, mock_probe, _mock_props, mock_run):
        # extract_frame_with_conversion uses npl=100 (hardcoded SDR reference white),
        # so HDR metadata probing is never needed during preview extraction.
        clear_maxfall_cache()
        self.addCleanup(clear_maxfall_cache)
        mock_run.return_value = _png_bytes()
        for t, tonemap in [(10.0, 'mobius'), (20.0, 'hable'), (30.0, 'reinhard')]:
            extract_frame_with_conversion('clip.mkv', gamma=1.0, tonemapper=tonemap, time_position=t,
                                          width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1])
        self.assertEqual(mock_probe.call_count, 0)

    @patch('src.gui.ImageTk.PhotoImage')
    def test_gamma_change_does_not_resize_window(self, _mock_photo):
        gui = _bare_gui()
        gui.display_image_var = MagicMock()
        gui.display_image_var.get.return_value = True
        gui.gamma_var = MagicMock()
        gui.gamma_var.get.return_value = 1.5
        gui._converted_preview_base = Image.new('RGB', PREVIEW_SIZE, (10, 20, 30))
        gui.converted_image_label = MagicMock()
        gui.adjust_window_size = MagicMock()

        gui.on_gamma_change()

        gui.adjust_window_size.assert_not_called()  # no geometry thrash per tick

    @patch('src.gui.ImageTk.PhotoImage')
    def test_render_caches_converted_base_at_preview_size(self, _mock_photo):
        # The cached base must be downsized to preview size so gamma stays cheap.
        gui = _bare_gui()
        gui.original_image_label = MagicMock()
        gui.converted_image_label = MagicMock()
        gui.gamma_var = MagicMock()
        gui.gamma_var.get.return_value = 1.0
        gui.adjust_gamma = lambda img, g: img
        gui.adjust_window_size = MagicMock()
        gui._hide_preview_loading = MagicMock()
        gui._reveal_preview = MagicMock()
        gui._window_auto_fitted = True  # live-geometry path -> native PREVIEW_SIZE
        full_res = Image.new('RGB', (3840, 1632), (10, 20, 30))

        gui._render_preview_images(full_res, full_res, 5.0)

        self.assertEqual(gui._converted_preview_base.size, PREVIEW_SIZE)

    @patch('src.gui.extract_frame_with_conversion', return_value='c')
    @patch('src.gui.extract_frame', return_value='o')
    def test_preview_extraction_requests_reduced_resolution(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 'mobius')
        self.assertEqual(
            (mock_extract.call_args.kwargs['width'], mock_extract.call_args.kwargs['height']),
            PREVIEW_SIZE)
        self.assertEqual(
            (mock_convert.call_args.kwargs['width'], mock_convert.call_args.kwargs['height']),
            PREVIEW_SIZE)

    @patch('src.gui.extract_frame_with_conversion', return_value='c')
    @patch('src.gui.extract_frame', return_value='o')
    def test_revisiting_cached_frame_spawns_no_ffmpeg(self, mock_extract, mock_convert):
        gui = _bare_gui()
        gui._extract_preview_images('in.mp4', 5.0, 'reinhard')  # populate cache
        mock_extract.reset_mock()
        mock_convert.reset_mock()
        gui._extract_preview_images('in.mp4', 5.0, 'reinhard')  # revisit
        mock_extract.assert_not_called()
        mock_convert.assert_not_called()


class TestTimingBudgets(unittest.TestCase):
    """Cheap hot-path operations measured with generous ceilings + a [perf] report."""

    def test_gamma_pass_on_preview_frame_is_cheap(self):
        gui = _bare_gui()
        img = Image.new('RGB', PREVIEW_SIZE, (120, 90, 60))
        ms = _best_ms(lambda: gui.adjust_gamma(img, 1.8))
        print(f"\n[perf] adjust_gamma {PREVIEW_SIZE[0]}x{PREVIEW_SIZE[1]}: {ms:.2f} ms")
        self.assertLess(ms, 60.0)  # ~0.6ms observed; 60ms = huge CI headroom

    def test_identity_gamma_is_essentially_free(self):
        gui = _bare_gui()
        big = Image.new('RGB', (3840, 1632), (120, 90, 60))
        ms = _best_ms(lambda: gui.adjust_gamma(big, 1.0))
        print(f"\n[perf] adjust_gamma identity (4K, short-circuit): {ms:.3f} ms")
        self.assertLess(ms, 5.0)  # gamma==1.0 returns the same object

    def test_working_on_preview_size_beats_full_resolution(self):
        # Documents why rescaling the display pane from the cached 4K base is
        # cheaper than running gamma on the full 4K base itself: the pane is
        # materially smaller so per-pixel ops are much faster.
        gui = _bare_gui()
        small = Image.new('RGB', INITIAL_PANE_SIZE, (120, 90, 60))
        big = Image.new('RGB', PREVIEW_SIZE, (120, 90, 60))
        ms_small = _best_ms(lambda: gui.adjust_gamma(small, 1.8))
        ms_big = _best_ms(lambda: gui.adjust_gamma(big, 1.8))
        print(f"\n[perf] adjust_gamma pane={ms_small:.2f}ms 4K={ms_big:.2f}ms "
              f"(~{ms_big / max(ms_small, 1e-3):.0f}x)")
        self.assertLess(ms_small, ms_big)


@unittest.skipUnless(_SAMPLE and _FFMPEG_OK, "no sample video / ffmpeg for the extraction audit")
class TestExtractionPerfAudit(unittest.TestCase):
    """Real-ffmpeg decode-bound path: report timings, guard against catastrophes."""

    def test_report_preview_extraction_times(self):
        e_original = _best_ms(
            lambda: extract_frame(_SAMPLE, time_position=2.0,
                                  width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1]),
            runs=2)
        e_dynamic = _best_ms(
            lambda: extract_frame_with_conversion(
                _SAMPLE, gamma=1.0, tonemapper='mobius', time_position=2.0,
                width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1]),
            runs=2)
        print(f"\n[perf] real preview extract ({os.path.basename(_SAMPLE)}): "
              f"original={e_original:.0f}ms dynamic-convert={e_dynamic:.0f}ms")
        # Loose ceiling: catches a catastrophic regression, tolerant of slow CI/disk.
        self.assertLess(e_dynamic, 15000)

    def test_report_maxfall_cache_cold_vs_warm(self):
        # Repeated preview extractions: cache avoids repeated ffmpeg for same frames.
        clear_maxfall_cache()
        self.addCleanup(clear_maxfall_cache)

        def extract(t):
            extract_frame_with_conversion(
                _SAMPLE, gamma=1.0, tonemapper='mobius', time_position=t,
                width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1])

        start = time.perf_counter()
        extract(3.0)
        cold = (time.perf_counter() - start) * 1000.0
        warm = _best_ms(lambda: extract(4.0), runs=2)
        print(f"\n[perf] extraction timing ({os.path.basename(_SAMPLE)}): "
              f"first={cold:.0f}ms repeat={warm:.0f}ms")


if __name__ == '__main__':
    unittest.main()
