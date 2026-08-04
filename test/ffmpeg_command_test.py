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


class TestContainerStreamArgs(unittest.TestCase):
    """Container-aware audio/subtitle handling, moved here from
    test/conversion_test.py's TestContainerStreamArgs -- this function is no
    longer a ConversionManager method."""

    def test_mkv_copies_everything(self):
        props = {'audio_codec': 'truehd',
                 'subtitle_streams': [{'codec_name': 'hdmv_pgs_subtitle', 'index': 2}]}
        self.assertEqual(
            ffmpeg_command._container_stream_args('out.mkv', props),
            (['-map', '0:s?'], ['-c:a', 'copy'], ['-c:s', 'copy']))

    def test_mp4_transcodes_truehd_and_keeps_only_text_subs(self):
        props = {'audio_codec': 'truehd', 'audio_bit_rate': 0,
                 'subtitle_streams': [
                     {'codec_name': 'subrip', 'index': 3},
                     {'codec_name': 'hdmv_pgs_subtitle', 'index': 2},
                     {'codec_name': 'ass', 'index': 4},
                 ]}
        sub_map, audio, sub_codec = ffmpeg_command._container_stream_args('out.mp4', props)
        self.assertEqual(sub_map, ['-map', '0:3', '-map', '0:4'])  # text subs only
        self.assertEqual(audio, ['-c:a', 'aac', '-b:a', '192k'])    # no source bitrate
        self.assertEqual(sub_codec, ['-c:s', 'mov_text'])

    def test_mp4_caps_transcode_bitrate(self):
        props = {'audio_codec': 'dts', 'audio_bit_rate': 1500000, 'subtitle_streams': []}
        _, audio, _ = ffmpeg_command._container_stream_args('out.mp4', props)
        self.assertEqual(audio, ['-c:a', 'aac', '-b:a', '384000'])

    def test_mp4_copies_compatible_audio_and_drops_image_subs(self):
        props = {'audio_codec': 'eac3', 'audio_bit_rate': 0,
                 'subtitle_streams': [{'codec_name': 'hdmv_pgs_subtitle', 'index': 2}]}
        sub_map, audio, sub_codec = ffmpeg_command._container_stream_args('out.mp4', props)
        self.assertEqual(sub_map, [])          # image subs dropped
        self.assertEqual(audio, ['-c:a', 'copy'])  # eac3 is mp4-legal
        self.assertEqual(sub_codec, [])

    def test_m4v_and_mov_behave_like_mp4(self):
        props = {'audio_codec': 'truehd', 'audio_bit_rate': 0, 'subtitle_streams': []}
        for path in ('out.m4v', 'out.MOV'):
            _, audio, _ = ffmpeg_command._container_stream_args(path, props)
            self.assertEqual(audio, ['-c:a', 'aac', '-b:a', '192k'])


class TestStreamMapArgs(unittest.TestCase):

    def test_mkv_licensed_non_dovi_maps_all_audio_first_then_subtitles(self):
        props = {'audio_codec': 'aac',
                 'subtitle_streams': [{'codec_name': 'subrip', 'index': 2}]}
        streams = ffmpeg_command._stream_map_args(_Req(output_path='out.mkv'), props)
        self.assertEqual(streams.map_args, ['-map', '0:a?', '-map', '0:s?'], msg=streams)

    def test_dolby_vision_unlicensed_downmixes_to_free_tier(self):
        props = {'is_dolby_vision': True, 'audio_codec': 'truehd',
                 'subtitle_streams': []}
        streams = ffmpeg_command._stream_map_args(
            _Req(output_path='out.mkv', licensed=False), props)
        self.assertEqual(streams.map_args, ['-map', '0:a:0?', '-map', '0:s?'], msg=streams)
        self.assertEqual(streams.audio_codec_args,
                         ['-c:a', 'aac', '-ac', '2', '-b:a', '192k'], msg=streams)

    def test_dolby_vision_licensed_keeps_container_aware_passthrough(self):
        props = {'is_dolby_vision': True, 'audio_codec': 'truehd',
                 'subtitle_streams': []}
        streams = ffmpeg_command._stream_map_args(
            _Req(output_path='out.mkv', licensed=True), props)
        self.assertEqual(streams.map_args, ['-map', '0:a?', '-map', '0:s?'], msg=streams)
        self.assertEqual(streams.audio_codec_args, ['-c:a', 'copy'], msg=streams)

    def test_non_dolby_vision_ignores_licensed_flag(self):
        props = {'audio_codec': 'aac', 'subtitle_streams': []}
        licensed = ffmpeg_command._stream_map_args(_Req(licensed=True), props)
        unlicensed = ffmpeg_command._stream_map_args(_Req(licensed=False), props)
        self.assertEqual(licensed.map_args, unlicensed.map_args)


class TestCodecAndPixFmt(unittest.TestCase):

    def test_default_cpu_8bit_h264_is_libx264_yuv420p(self):
        plan = ffmpeg_command._codec_and_pix_fmt(_Req(), {'codec_name': 'h264'}, None)
        self.assertEqual(plan.codec, 'libx264', msg=plan)
        self.assertEqual(plan.pix_fmt, 'yuv420p', msg=plan)
        self.assertFalse(plan.produces_hevc, msg=plan)

    def test_ten_bit_cpu_uses_yuv420p10le_and_stays_libx264(self):
        plan = ffmpeg_command._codec_and_pix_fmt(
            _Req(bit_depth=10), {'codec_name': 'h264'}, None)
        self.assertEqual(plan.codec, 'libx264', msg=plan)
        self.assertEqual(plan.pix_fmt, 'yuv420p10le', msg=plan)

    def test_twelve_bit_forces_libx265_and_yuv420p12le(self):
        plan = ffmpeg_command._codec_and_pix_fmt(
            _Req(bit_depth=12), {'codec_name': 'h264'}, None)
        self.assertEqual(plan.codec, 'libx265', msg=plan)
        self.assertEqual(plan.pix_fmt, 'yuv420p12le', msg=plan)
        self.assertTrue(plan.produces_hevc, msg=plan)

    def test_hevc_source_preserves_libx265_even_at_8bit_cpu(self):
        plan = ffmpeg_command._codec_and_pix_fmt(
            _Req(bit_depth=8), {'codec_name': 'hevc'}, None)
        self.assertEqual(plan.codec, 'libx265', msg=plan)
        self.assertTrue(plan.produces_hevc, msg=plan)

    def test_ten_bit_hardware_encoder_swaps_to_hevc_variant_and_p010le(self):
        plan = ffmpeg_command._codec_and_pix_fmt(
            _Req(bit_depth=10), {'codec_name': 'h264'}, 'h264_nvenc')
        self.assertEqual(plan.codec, 'hevc_nvenc', msg=plan)
        self.assertEqual(plan.pix_fmt, 'p010le', msg=plan)
        self.assertTrue(plan.produces_hevc, msg=plan)

    def test_hevc_source_on_hardware_encoder_swaps_even_at_8bit(self):
        """Preservation swap: an already-HEVC source swaps at 8-bit purely
        to keep the source codec, though the H.264 hardware encoder could
        otherwise handle 8-bit fine."""
        plan = ffmpeg_command._codec_and_pix_fmt(
            _Req(bit_depth=8), {'codec_name': 'hevc'}, 'h264_amf')
        self.assertEqual(plan.codec, 'hevc_amf', msg=plan)
        # Only bit_depth == 10 triggers the p010le swap -- 8-bit is unaffected.
        self.assertEqual(plan.pix_fmt, 'yuv420p', msg=plan)
        self.assertTrue(plan.produces_hevc, msg=plan)

    def test_produces_hevc_false_for_plain_h264_hardware_encoder(self):
        plan = ffmpeg_command._codec_and_pix_fmt(
            _Req(bit_depth=8), {'codec_name': 'h264'}, 'h264_nvenc')
        self.assertEqual(plan.codec, 'h264_nvenc', msg=plan)
        self.assertFalse(plan.produces_hevc, msg=plan)


class TestEncoderRateArgs(unittest.TestCase):

    _PROPS = {'bit_rate': 4_000_000}

    def test_libx264_cq_mode(self):
        args = ffmpeg_command._encoder_rate_args(_Req(quality=20), self._PROPS, 'libx264')
        self.assertEqual(
            args, ['-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'film',
                   '-crf', '20'], msg=args)

    def test_libx265_has_no_tune_flag(self):
        """libx265 has no 'film' tune -- x264's -tune film fails x265 init."""
        args = ffmpeg_command._encoder_rate_args(_Req(quality=20), self._PROPS, 'libx265')
        self.assertNotIn('-tune', args, msg=args)

    def test_nvenc_cq_mode_uses_cq_flag_and_source_bitrate_as_ceiling(self):
        args = ffmpeg_command._encoder_rate_args(
            _Req(quality=23), self._PROPS, 'h264_nvenc')
        self.assertIn('-cq', args, msg=args)
        self.assertEqual(args[args.index('-cq') + 1], '23', msg=args)
        self.assertIn('4000000', args, msg=args)

    def test_qsv_cq_mode_uses_global_quality(self):
        args = ffmpeg_command._encoder_rate_args(
            _Req(quality=23), self._PROPS, 'h264_qsv')
        self.assertIn('-global_quality', args, msg=args)

    def test_amf_cq_mode_uses_matched_qp_triplet(self):
        args = ffmpeg_command._encoder_rate_args(
            _Req(quality=23), self._PROPS, 'h264_amf')
        for flag in ('-qp_i', '-qp_p', '-qp_b'):
            self.assertIn(flag, args, msg=args)
            self.assertEqual(args[args.index(flag) + 1], '23', msg=args)

    def test_bitrate_mode_computes_maxrate_and_bufsize_from_quality_kbps(self):
        args = ffmpeg_command._encoder_rate_args(
            _Req(quality=5000, quality_mode='bitrate'), self._PROPS, 'libx264')
        self.assertEqual(args[args.index('-b:v') + 1], '5000000', msg=args)
        self.assertEqual(args[args.index('-maxrate') + 1], '7500000', msg=args)
        self.assertEqual(args[args.index('-bufsize') + 1], '10000000', msg=args)
        self.assertNotIn('-crf', args, msg=args)

    def test_amf_bitrate_mode_adds_vbr_peak_rc(self):
        """AMF is the one encoder with a non-empty bitrate-only args tuple
        entry -- nvenc/qsv/libx264/libx265 all have an empty one."""
        args = ffmpeg_command._encoder_rate_args(
            _Req(quality=5000, quality_mode='bitrate'), self._PROPS, 'h264_amf')
        self.assertIn('vbr_peak', args, msg=args)

    def test_mkv_zero_bitrate_falls_back_to_8mbps_ceiling(self):
        """MKV containers often report bit_rate=0; nvenc/qsv must not
        receive -b:v 0 / -maxrate 0 / -bufsize 0."""
        args = ffmpeg_command._encoder_rate_args(
            _Req(quality=23), {'bit_rate': 0}, 'h264_nvenc')
        self.assertEqual(args[args.index('-b:v') + 1], '8000000', msg=args)


if __name__ == '__main__':
    unittest.main()
