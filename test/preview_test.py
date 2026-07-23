"""Tests for the preview-pane LUT mechanism (see
docs/superpowers/specs/2026-07-22-lut-color-pipeline-design.md and the LUT
color pipeline implementation plan, Task 5). Originally built behind a
temporary dev-only preview toggle; now driven by the permanent, user-facing
"Accurate GPU Color" export setting (gui.py's lut_export_var) instead, so
the preview always reflects what real GPU export will actually produce."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from PIL import Image
from preview import _HDRPreviewMixin


class _FakeGui(_HDRPreviewMixin):
    """Bare instance exposing just what _extract_preview_images touches."""
    def __init__(self):
        pass


_VALID_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
    b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
    b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82'
)


class TestPreviewLutToggle(unittest.TestCase):

    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_toggling_lut_only_recomputes_converted_frame(self, mock_extract_frame, mock_extract_conv):
        gui = _FakeGui()
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_extract_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)
        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=False)

        # Original (HDR) frame extracted only once -- its cache key doesn't
        # include lut_enabled, so the second call must reuse the cached frame.
        self.assertEqual(mock_extract_frame.call_count, 1)
        # Converted (SDR) frame extracted twice -- once per distinct lut_enabled state.
        self.assertEqual(mock_extract_conv.call_count, 2)
        self.assertTrue(mock_extract_conv.call_args_list[0].kwargs['lut_enabled'])   # first call: lut_enabled=True
        self.assertFalse(mock_extract_conv.call_args_list[1].kwargs['lut_enabled'])  # second call: lut_enabled=False

    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_same_lut_state_reuses_converted_cache(self, mock_extract_frame, mock_extract_conv):
        gui = _FakeGui()
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_extract_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)
        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)

        self.assertEqual(mock_extract_conv.call_count, 1)

    @patch('preview.extract_frames_with_conversion_batch')
    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_prewarmed_converted_cache_is_reachable_by_real_lookup(
            self, mock_extract_frame, mock_extract_conv, mock_batch):
        """_prewarm_batch_converted writes under the same 4-tuple key that
        _extract_preview_images reads from -- a converted frame it pre-warms
        must be servable as a cache hit by the real lookup path (same
        video_path/time/tonemapper/lut_enabled), not silently re-extracted.

        Before the fix, _prewarm_batch_converted wrote under the old 3-tuple
        (video_path, round(t, 3), tonemapper) key while _extract_preview_images
        always reads via the 4-tuple (..., lut_enabled) key -- tuples of
        different lengths never compare equal, so this assertion would have
        failed: mock_extract_conv.call_count would be 1 (a cache miss forcing
        re-extraction) instead of 0.
        """
        gui = _FakeGui()
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_batch.return_value = [Image.open(__import__('io').BytesIO(_VALID_PNG))]

        gui._prewarm_batch_converted('v.mp4', [1.0], 'reinhard', 1, lut_enabled=True)
        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)

        mock_extract_conv.assert_not_called()

    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frames_with_gpu_conversion_batch')
    @patch('preview.extract_frame')
    def test_gpu_only_prewarm_does_not_poison_lut_off_lookup(
            self, mock_extract_frame, mock_gpu_batch, mock_gpu_single):
        """extract_frames_with_gpu_conversion_batch (the GPU-only-tonemapper
        batch path) has no lut_enabled parameter and always produces
        lut_enabled=True content. If the toggle is OFF when prewarm runs,
        that always-on content must NOT be servable as a cache hit by a real
        lookup for lut_enabled=False -- it must be a clean miss that falls
        through to a genuine single-frame GPU extraction honoring
        lut_enabled=False. Before the fix, the prewarm stored its always-True
        content under the caller's (False) key, so the real lookup would hit
        the cache and silently serve wrong (LUT-on) content.
        """
        gui = _FakeGui()
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_gpu_batch.return_value = [Image.open(__import__('io').BytesIO(_VALID_PNG))]
        mock_gpu_single.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        # Prewarm runs while the toggle is OFF.
        gui._prewarm_batch_converted('v.mp4', [1.0], 'bt.2390', 1, lut_enabled=False)
        # The real lookup, also for lut_enabled=False, must not reuse the
        # always-True prewarmed content -- it must call the real single-frame
        # GPU extractor to get genuinely LUT-off content.
        gui._extract_preview_images('v.mp4', 1.0, 'bt.2390', lut_enabled=False)

        mock_gpu_single.assert_called_once()
        self.assertFalse(mock_gpu_single.call_args.kwargs['lut_enabled'])

    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frames_with_gpu_conversion_batch')
    @patch('preview.extract_frame')
    def test_gpu_only_prewarm_still_hits_cache_when_toggle_on(
            self, mock_extract_frame, mock_gpu_batch, mock_gpu_single):
        """Regression guard: the toggle-ON (default) case must still be a
        genuine cache hit -- prewarm speedup must not regress for the
        default state while fixing the toggle-off poisoning case above."""
        gui = _FakeGui()
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_gpu_batch.return_value = [Image.open(__import__('io').BytesIO(_VALID_PNG))]

        gui._prewarm_batch_converted('v.mp4', [1.0], 'bt.2390', 1, lut_enabled=True)
        gui._extract_preview_images('v.mp4', 1.0, 'bt.2390', lut_enabled=True)

        mock_gpu_single.assert_not_called()


class TestDisplayFramesReadsLutExportVar(unittest.TestCase):
    """display_frames must read the permanent lut_export_var (the "Accurate
    GPU Color" checkbox), not the old temporary lut_preview_var -- that
    variable no longer exists on HDRConverterGUI. Only matters for GPU-only
    tonemappers (bt.2390/spline) though -- see TestEffectiveLutEnabled: for
    every other tonemapper it's forced off regardless of the checkbox."""

    def _gui(self, lut_enabled: bool, tonemapper: str = 'bt.2390'):
        gui = _FakeGui()
        gui.tonemap_var = MagicMock(); gui.tonemap_var.get.return_value = tonemapper
        gui.lut_export_var = MagicMock(); gui.lut_export_var.get.return_value = lut_enabled
        gui._preview_pool = MagicMock()
        gui._preview_pool.submit.side_effect = lambda fn, *a, **k: fn(*a, **k)
        gui._schedule_on_main = lambda cb: cb()
        gui._get_duration = MagicMock(return_value=10.0)
        gui._preview_time_position = MagicMock(return_value=1.0)
        gui._render_preview_images = MagicMock()
        gui._prewarm_other_frames = MagicMock()
        return gui

    @patch('preview._HDRPreviewMixin._extract_preview_images')
    def test_toggle_on_is_forwarded(self, mock_extract):
        mock_extract.return_value = (MagicMock(), MagicMock())
        gui = self._gui(lut_enabled=True)
        gui.display_frames('v.mp4')
        self.assertTrue(mock_extract.call_args.args[-1])

    @patch('preview._HDRPreviewMixin._extract_preview_images')
    def test_toggle_off_is_forwarded(self, mock_extract):
        mock_extract.return_value = (MagicMock(), MagicMock())
        gui = self._gui(lut_enabled=False)
        gui.display_frames('v.mp4')
        self.assertFalse(mock_extract.call_args.args[-1])

    @patch('preview._HDRPreviewMixin._extract_preview_images')
    def test_defaults_true_when_lut_export_var_missing(self, mock_extract):
        """Bare test doubles (characterization_test.py's _bare_gui()) built via
        object.__new__ may not set lut_export_var -- must default to the same
        True the real BooleanVar always starts at, not raise."""
        mock_extract.return_value = (MagicMock(), MagicMock())
        gui = self._gui(lut_enabled=True)
        del gui.lut_export_var
        gui.display_frames('v.mp4')
        self.assertTrue(mock_extract.call_args.args[-1])

    @patch('preview._HDRPreviewMixin._extract_preview_images')
    def test_non_gpu_only_tonemapper_forces_lut_off_even_when_checked(self, mock_extract):
        """Reinhard/Mobius/Hable produce the same colors either way, so the
        checkbox's effect is forced off for them regardless of its checked
        state -- only bt.2390/spline actually vary with it."""
        mock_extract.return_value = (MagicMock(), MagicMock())
        gui = self._gui(lut_enabled=True, tonemapper='reinhard')
        gui.display_frames('v.mp4')
        self.assertFalse(mock_extract.call_args.args[-1])


class TestEffectiveLutEnabled(unittest.TestCase):
    """_effective_lut_enabled is the single place that decides what
    preview/export actually use, as opposed to lut_export_var's raw checked
    state -- see gui.py's _apply_lut_export_availability for the matching
    checkbox-greying logic."""

    def _gui(self, lut_enabled: bool):
        gui = _FakeGui()
        gui.lut_export_var = MagicMock(); gui.lut_export_var.get.return_value = lut_enabled
        return gui

    def test_gpu_only_tonemapper_passes_through_checkbox(self):
        gui = self._gui(lut_enabled=True)
        self.assertTrue(gui._effective_lut_enabled('bt.2390'))
        gui2 = self._gui(lut_enabled=False)
        self.assertFalse(gui2._effective_lut_enabled('spline'))

    def test_non_gpu_only_tonemapper_always_off(self):
        gui = self._gui(lut_enabled=True)
        for tm in ('reinhard', 'mobius', 'hable'):
            self.assertFalse(gui._effective_lut_enabled(tm))

    def test_missing_lut_export_var_defaults_true_for_gpu_only_tonemapper(self):
        gui = _FakeGui()
        self.assertTrue(gui._effective_lut_enabled('bt.2390'))


if __name__ == '__main__':
    unittest.main()
