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
    def test_gpu_only_prewarm_hits_cache_when_toggle_off_too(
            self, mock_extract_frame, mock_gpu_batch, mock_gpu_single):
        """extract_frames_with_gpu_conversion_batch now threads lut_enabled
        through to every extract_frame_with_gpu_conversion call, so a
        toggle-off prewarm produces genuinely toggle-off content -- it's a
        legitimate cache HIT for the matching real lookup, not a poisoned one.
        (Previously the batch fn ignored lut_enabled entirely and always
        produced True content, which forced a workaround: storing prewarmed
        GPU-only-tonemapper content under the True key regardless of the
        caller's request, so a False lookup fell through to a real miss
        instead of silently serving wrong content. That workaround is gone
        now that the root cause -- the batch fn silently ignoring
        lut_enabled -- is fixed.)
        """
        gui = _FakeGui()
        gui._preview_generation = 1
        gui._preview_cache_original = {}
        gui._preview_cache_converted = {}
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_gpu_batch.return_value = [Image.open(__import__('io').BytesIO(_VALID_PNG))]
        mock_gpu_single.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._prewarm_batch_converted('v.mp4', [1.0], 'bt.2390', 1, lut_enabled=False)
        gui._extract_preview_images('v.mp4', 1.0, 'bt.2390', lut_enabled=False)

        mock_gpu_single.assert_not_called()
        mock_gpu_batch.assert_called_once_with(
            'v.mp4', [1.0], 1.0, 'bt.2390', 3840, 2160, lut_enabled=False)

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


class TestPreviewUsesGpuTonemapWhenActive(unittest.TestCase):
    """Preview must route CPU-capable tonemappers (Reinhard/Mobius/Hable)
    through the real GPU/libplacebo path whenever GPU acceleration is
    actually active -- matching what real export will do -- not just the
    GPU-only tonemappers (BT.2390/Spline).

    Before this fix, preview always used the CPU zscale tonemap for these
    three regardless of the GPU accel toggle: only the LUT stage differed
    between "Accurate GPU Color" on/off, so toggling it appeared to prove the
    CPU-only tonemappers rendered identically on GPU -- it never actually
    exercised the GPU tonemap algorithm at all.
    """

    def _gui(self, gpu_accel: bool):
        gui = _FakeGui()
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_var.get.return_value = gpu_accel
        return gui

    @patch('preview.vulkan_libplacebo_available', return_value=True)
    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_cpu_capable_tonemapper_uses_gpu_extraction_when_gpu_active(
            self, mock_extract_frame, mock_cpu_conv, mock_gpu_conv, _mock_probe):
        gui = self._gui(gpu_accel=True)
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_gpu_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)

        mock_gpu_conv.assert_called_once()
        mock_cpu_conv.assert_not_called()

    @patch('preview.vulkan_libplacebo_available', return_value=True)
    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_cpu_capable_tonemapper_uses_cpu_extraction_when_gpu_off(
            self, mock_extract_frame, mock_cpu_conv, mock_gpu_conv, _mock_probe):
        gui = self._gui(gpu_accel=False)
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_cpu_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)

        mock_cpu_conv.assert_called_once()
        mock_gpu_conv.assert_not_called()

    @patch('preview.vulkan_libplacebo_available', return_value=False)
    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_cpu_capable_tonemapper_falls_back_to_cpu_when_libplacebo_unavailable(
            self, mock_extract_frame, mock_cpu_conv, mock_gpu_conv, _mock_probe):
        """GPU toggle on but this machine can't actually run libplacebo --
        must fall back to CPU extraction, matching what real export does
        (construct_ffmpeg_command's use_libplacebo is also gated on the probe)."""
        gui = self._gui(gpu_accel=True)
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_cpu_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)

        mock_cpu_conv.assert_called_once()
        mock_gpu_conv.assert_not_called()

    @patch('preview.vulkan_libplacebo_available', return_value=False)
    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frame')
    def test_gpu_only_tonemapper_still_uses_gpu_extraction_regardless_of_toggle(
            self, mock_extract_frame, mock_gpu_conv, _mock_probe):
        """BT.2390/Spline have no CPU implementation at all -- unconditional,
        regardless of the GPU accel toggle or the libplacebo probe result."""
        gui = self._gui(gpu_accel=False)
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_gpu_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui._extract_preview_images('v.mp4', 1.0, 'bt.2390', lut_enabled=True)

        mock_gpu_conv.assert_called_once()

    @patch('preview.vulkan_libplacebo_available', return_value=True)
    @patch('preview.extract_frame_with_gpu_conversion')
    @patch('preview.extract_frame_with_conversion')
    @patch('preview.extract_frame')
    def test_toggling_gpu_accel_invalidates_cache_for_same_tonemapper(
            self, mock_extract_frame, mock_cpu_conv, mock_gpu_conv, _mock_probe):
        """The same tonemapper name renders differently via CPU zscale vs GPU
        libplacebo -- toggling 'Use GPU' must not silently reuse the other
        path's cached frame."""
        mock_extract_frame.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_gpu_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))
        mock_cpu_conv.return_value = Image.open(__import__('io').BytesIO(_VALID_PNG))

        gui = self._gui(gpu_accel=True)
        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)
        gui.gpu_accel_var.get.return_value = False
        gui._extract_preview_images('v.mp4', 1.0, 'reinhard', lut_enabled=True)

        mock_gpu_conv.assert_called_once()
        mock_cpu_conv.assert_called_once()


class TestDisplayFramesReadsLutExportVar(unittest.TestCase):
    """display_frames must read the permanent lut_export_var (the "Accurate
    GPU Color" checkbox), not the old temporary lut_preview_var -- that
    variable no longer exists on HDRConverterGUI. Applies uniformly
    regardless of tonemapper -- see TestEffectiveLutEnabled."""

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
        """Forwarding is tonemapper-agnostic: libplacebo's gamut handling was
        found to diverge from the LUT reference for CPU-capable tonemappers
        too (Hable measured ~61/255), not just bt.2390/spline, so this must
        hold for every tonemapper, not just GPU-only ones."""
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


class TestEffectiveLutEnabled(unittest.TestCase):
    """_effective_lut_enabled is the single place that decides what
    preview/export actually use: lut_export_var's raw checked state, applied
    uniformly regardless of tonemapper -- see gui.py's
    _apply_lut_export_availability for the matching checkbox-greying logic."""

    def _gui(self, lut_enabled: bool):
        gui = _FakeGui()
        gui.lut_export_var = MagicMock(); gui.lut_export_var.get.return_value = lut_enabled
        return gui

    def test_passes_through_checkbox_state(self):
        gui = self._gui(lut_enabled=True)
        self.assertTrue(gui._effective_lut_enabled())
        gui2 = self._gui(lut_enabled=False)
        self.assertFalse(gui2._effective_lut_enabled())

    def test_missing_lut_export_var_defaults_true(self):
        gui = _FakeGui()
        self.assertTrue(gui._effective_lut_enabled())

    def _gui_with_gpu(self, lut_enabled: bool, gpu_accel: bool):
        gui = self._gui(lut_enabled=lut_enabled)
        gui.gpu_accel_var = MagicMock()
        gui.gpu_accel_var.get.return_value = gpu_accel
        return gui

    def test_forces_true_when_gpu_off_regardless_of_stale_checkbox(self):
        """construct_ffmpeg_command's CPU branch never reads lut_enabled --
        real CPU export always applies the LUT. _apply_lut_export_availability
        only greys the checkbox out when GPU accel is off, it never resets
        lut_export_var, so a value left False from an earlier GPU session
        would otherwise make CPU preview show the no-LUT legacy filter while
        real CPU export always includes the LUT. Force True whenever GPU
        accel is off to match what export will actually do."""
        gui = self._gui_with_gpu(lut_enabled=False, gpu_accel=False)
        self.assertTrue(gui._effective_lut_enabled())

    def test_honors_checkbox_when_gpu_on(self):
        gui = self._gui_with_gpu(lut_enabled=False, gpu_accel=True)
        self.assertFalse(gui._effective_lut_enabled())
        gui2 = self._gui_with_gpu(lut_enabled=True, gpu_accel=True)
        self.assertTrue(gui2._effective_lut_enabled())

    def test_missing_gpu_accel_var_falls_back_to_checkbox(self):
        """Bare test doubles that don't set gpu_accel_var (e.g. existing
        callers of _gui() above) must keep today's behavior -- no gpu_accel_var
        means don't second-guess the checkbox."""
        gui = self._gui(lut_enabled=False)
        self.assertFalse(gui._effective_lut_enabled())


if __name__ == '__main__':
    unittest.main()
