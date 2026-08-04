"""Unit tests for src/ffmpeg_command.py's pure helpers (audit item 6).

Each helper is tested directly, with no ConversionView, no ConversionManager,
and no test double -- they are pure functions returning frozen dataclasses.
test/ffmpeg_command_golden_test.py is the end-to-end safety net; this file
is where a broken helper is diagnosed to its specific job.
"""
from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import ffmpeg_command  # noqa: E402
from conversion_view import Notice  # noqa: E402


@dataclass(frozen=True)
class _Req:
    """A minimal RequestLike -- only the fields ffmpeg_command's helpers
    declare in their Protocol, not the full ConversionRequest."""
    input_path: str = 'in.mp4'
    output_path: str = 'out.mkv'
    gamma: float = 1.0
    use_gpu: bool = False
    tonemapper: str = 'reinhard'
    quality: int = 23
    quality_mode: str = 'cq'
    bit_depth: int = 8
    licensed: bool = False
    lut_enabled: bool = True


_PROPS = {'codec_name': 'h264'}


class TestTonemapPlan(unittest.TestCase):
    """resolve_libplacebo_available is always a plain stub callable here --
    never unittest.mock.patch -- because _tonemap_plan takes it as a
    parameter rather than importing utils.vulkan_libplacebo_available
    itself. See this task's file-level note on why."""

    def test_default_request_stays_on_cpu_no_notices(self):
        plan = ffmpeg_command._tonemap_plan(_Req(), _PROPS, lambda: False)
        self.assertFalse(plan.use_gpu, msg=plan)
        self.assertFalse(plan.dovi_needs_rpu, msg=plan)
        self.assertFalse(plan.use_libplacebo, msg=plan)
        self.assertEqual(plan.notices, [], msg=plan)

    def test_use_gpu_is_carried_through_when_not_overridden(self):
        plan = ffmpeg_command._tonemap_plan(_Req(use_gpu=True), _PROPS, lambda: False)
        self.assertTrue(plan.use_gpu, msg=plan)

    def test_twelve_bit_forces_gpu_off(self):
        """No hardware HEVC encoder has a 12-bit profile -- this is a fixed
        silicon limitation, not a policy choice."""
        plan = ffmpeg_command._tonemap_plan(
            _Req(use_gpu=True, bit_depth=12), _PROPS, lambda: False)
        self.assertFalse(plan.use_gpu, msg=plan)

    def test_resolve_libplacebo_available_not_called_without_gpu_or_dovi(self):
        """Laziness: (use_gpu or dovi_needs_rpu) short-circuits before the
        probe -- a plain CPU/non-DoVi request must not call it at all."""
        def _forbidden():
            raise AssertionError('resolve_libplacebo_available must not be called')
        ffmpeg_command._tonemap_plan(_Req(use_gpu=False), _PROPS, _forbidden)

    def test_dolby_vision_profile_5_without_libplacebo_warns(self):
        props = {'codec_name': 'hevc', 'is_dolby_vision': True, 'dovi_profile': 5}
        plan = ffmpeg_command._tonemap_plan(_Req(), props, lambda: False)
        self.assertTrue(plan.dovi_needs_rpu, msg=plan)
        self.assertFalse(plan.use_libplacebo, msg=plan)
        self.assertEqual(len(plan.notices), 1, msg=plan)
        self.assertEqual(plan.notices[0].kind, 'warning', msg=plan)

    def test_dolby_vision_profile_5_forces_libplacebo_even_with_gpu_unchecked(self):
        """dovi_needs_rpu alone can flip use_libplacebo True even when
        request.use_gpu is False -- the info notice tells the user why."""
        props = {'codec_name': 'hevc', 'is_dolby_vision': True, 'dovi_profile': 5}
        plan = ffmpeg_command._tonemap_plan(_Req(use_gpu=False), props, lambda: True)
        self.assertTrue(plan.dovi_needs_rpu, msg=plan)
        self.assertTrue(plan.use_libplacebo, msg=plan)
        self.assertEqual(len(plan.notices), 1, msg=plan)
        self.assertEqual(plan.notices[0].kind, 'info', msg=plan)

    def test_dolby_vision_profile_7_needs_no_rpu(self):
        """Only profile 5 lacks an HDR10-compatible base layer -- profile 7
        is handled by the standard chain and needs no special tonemap
        handling here (its audio-tier split lives in _stream_map_args)."""
        props = {'codec_name': 'hevc', 'is_dolby_vision': True, 'dovi_profile': 7}
        plan = ffmpeg_command._tonemap_plan(_Req(), props, lambda: False)
        self.assertFalse(plan.dovi_needs_rpu, msg=plan)
        self.assertEqual(plan.notices, [], msg=plan)


if __name__ == '__main__':
    unittest.main()
