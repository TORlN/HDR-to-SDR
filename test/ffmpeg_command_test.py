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
from unittest.mock import patch

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


class TestGpuDeviceArgs(unittest.TestCase):
    """resolve_cuda_interop_available is always a plain stub callable, for
    the same reason resolve_libplacebo_available is in TestTonemapPlan --
    _gpu_device_args takes it as a parameter rather than importing
    utils.vulkan_cuda_interop_available itself. platform.system() is
    different: _gpu_device_args still calls it directly (see this task's
    file-level note on why that one needs no such treatment), so tests that
    care about its return value still use @patch('ffmpeg_command.platform.system', ...)."""

    def _plan(self, **overrides) -> ffmpeg_command.TonemapPlan:
        base = dict(use_gpu=False, dovi_needs_rpu=False, use_libplacebo=False, notices=[])
        base.update(overrides)
        return ffmpeg_command.TonemapPlan(**base)

    def test_gpu_off_returns_no_encoder_no_args(self):
        gpu = ffmpeg_command._gpu_device_args(self._plan(), lambda: 'h264_nvenc', lambda: False)
        self.assertIsNone(gpu.active_encoder, msg=gpu)
        self.assertEqual(gpu.pre_input_args, [], msg=gpu)
        self.assertEqual(gpu.notices, [], msg=gpu)

    def test_resolve_gpu_encoder_is_not_called_when_plan_use_gpu_is_false(self):
        """The core of the GPU-probe-laziness sharp edge: plan.use_gpu is
        already the post-12-bit-override value (from _tonemap_plan), so a
        12-bit GPU-requested conversion reaches here with use_gpu already
        False -- the probe (which may run a real subprocess) must not run."""
        def _forbidden():
            raise AssertionError('resolve_gpu_encoder must not be called')
        ffmpeg_command._gpu_device_args(self._plan(use_gpu=False), _forbidden, lambda: False)

    def test_resolve_cuda_interop_available_not_called_without_libplacebo(self):
        """Laziness: use_cuda_interop's condition short-circuits on
        plan.use_libplacebo before reaching the interop probe."""
        def _forbidden():
            raise AssertionError('resolve_cuda_interop_available must not be called')
        ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True, use_libplacebo=False), lambda: 'h264_nvenc', _forbidden)

    @patch('ffmpeg_command.platform.system', return_value='Windows')
    def test_nvenc_windows_without_libplacebo_adds_cuda_hwaccel(self, _plat):
        gpu = ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True), lambda: 'h264_nvenc', lambda: False)
        self.assertEqual(gpu.active_encoder, 'h264_nvenc', msg=gpu)
        self.assertEqual(gpu.pre_input_args, ['-hwaccel', 'cuda', '-hwaccel_device', '0'], msg=gpu)
        self.assertEqual(gpu.notices, [], msg=gpu)

    @patch('ffmpeg_command.platform.system', return_value='Darwin')
    def test_nvenc_unsupported_platform_warns_and_clears_encoder(self, _plat):
        gpu = ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True), lambda: 'h264_nvenc', lambda: False)
        self.assertIsNone(gpu.active_encoder, msg=gpu)
        self.assertEqual(gpu.pre_input_args, [], msg=gpu)
        self.assertEqual(len(gpu.notices), 1, msg=gpu)
        self.assertEqual(gpu.notices[0].kind, 'warning', msg=gpu)

    @patch('ffmpeg_command.platform.system', return_value='Windows')
    def test_amf_windows_needs_no_hwaccel_flag(self, _plat):
        gpu = ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True), lambda: 'h264_amf', lambda: False)
        self.assertEqual(gpu.active_encoder, 'h264_amf', msg=gpu)
        self.assertEqual(gpu.pre_input_args, [], msg=gpu)

    @patch('ffmpeg_command.platform.system', return_value='Windows')
    def test_unknown_encoder_always_warns(self, _plat):
        gpu = ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True), lambda: 'h264_mystery', lambda: False)
        self.assertIsNone(gpu.active_encoder, msg=gpu)
        self.assertEqual(len(gpu.notices), 1, msg=gpu)

    @patch('ffmpeg_command.platform.system', return_value='Windows')
    def test_nvenc_libplacebo_with_interop_available_uses_cuda_device_args(self, _plat):
        gpu = ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True, use_libplacebo=True), lambda: 'h264_nvenc', lambda: True)
        self.assertTrue(gpu.use_cuda_interop, msg=gpu)
        self.assertEqual(gpu.pre_input_args, ffmpeg_command.VULKAN_CUDA_DEVICE_ARGS, msg=gpu)

    @patch('ffmpeg_command.platform.system', return_value='Windows')
    def test_nvenc_libplacebo_without_interop_uses_plain_vulkan_device_args(self, _plat):
        gpu = ffmpeg_command._gpu_device_args(
            self._plan(use_gpu=True, use_libplacebo=True), lambda: 'h264_nvenc', lambda: False)
        self.assertFalse(gpu.use_cuda_interop, msg=gpu)
        self.assertEqual(gpu.pre_input_args, ffmpeg_command.VULKAN_DEVICE_ARGS, msg=gpu)


class TestFilterArgs(unittest.TestCase):

    def _plan(self, **overrides) -> ffmpeg_command.TonemapPlan:
        base = dict(use_gpu=False, dovi_needs_rpu=False, use_libplacebo=False, notices=[])
        base.update(overrides)
        return ffmpeg_command.TonemapPlan(**base)

    def _gpu(self, **overrides) -> ffmpeg_command.GpuPlan:
        base = dict(active_encoder=None, pre_input_args=[], use_cuda_interop=False, notices=[])
        base.update(overrides)
        return ffmpeg_command.GpuPlan(**base)

    def test_cpu_path_uses_zscale_chain(self):
        filt = ffmpeg_command._filter_args(_Req(), self._plan(), self._gpu())
        self.assertIn('zscale=t=linear', filt, msg=filt)
        self.assertIn('tonemap=reinhard', filt, msg=filt)

    def test_libplacebo_path_uses_libplacebo_chain(self):
        filt = ffmpeg_command._filter_args(
            _Req(use_gpu=True), self._plan(use_libplacebo=True), self._gpu())
        self.assertIn('libplacebo=', filt, msg=filt)
        self.assertNotIn('zscale=t=linear', filt, msg=filt)

    def test_libplacebo_path_forwards_cuda_input_from_gpu_plan(self):
        filt = ffmpeg_command._filter_args(
            _Req(use_gpu=True), self._plan(use_libplacebo=True),
            self._gpu(use_cuda_interop=True))
        self.assertIn('hwmap=derive_device=vulkan', filt, msg=filt)

    def test_gpu_only_tonemapper_on_cpu_path_raises(self):
        with self.assertRaises(ValueError) as ctx:
            ffmpeg_command._filter_args(
                _Req(tonemapper='bt.2390'), self._plan(use_libplacebo=False), self._gpu())
        self.assertIn('bt.2390 requires GPU tonemapping', str(ctx.exception))

    def test_gpu_only_tonemapper_on_libplacebo_path_does_not_raise(self):
        filt = ffmpeg_command._filter_args(
            _Req(use_gpu=True, tonemapper='bt.2390'),
            self._plan(use_libplacebo=True), self._gpu())
        self.assertIn('tonemapping=bt.2390', filt, msg=filt)


if __name__ == '__main__':
    unittest.main()
