"""Tests for the temporary preview-pane LUT toggle (see
docs/superpowers/specs/2026-07-22-lut-color-pipeline-design.md and the LUT
color pipeline implementation plan, Task 5). This whole file is deleted in
Task 9 once the toggle itself is removed."""
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


if __name__ == '__main__':
    unittest.main()
