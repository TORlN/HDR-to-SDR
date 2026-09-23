import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.conversion import ConversionManager, ConversionRequest
from tools.benchmark_conversion import (
    FIXTURE_PATH, MODE_SETTINGS, _HeadlessView, build_command, build_request,
    validate_mode_command,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = str(REPO_ROOT / 'test' / 'smoke_test_videos' / 'hdr10_10bit.mp4')


class TestBuildRequest(unittest.TestCase):
    def test_script_uses_the_committed_fixture(self):
        self.assertEqual(FIXTURE_PATH, FIXTURE)

    def test_requests_use_identical_benchmark_settings_and_intended_modes(self):
        expected_modes = {
            'CPU accurate': (False, True),
            'GPU accurate': (True, True),
            'GPU fast': (True, False),
        }
        self.assertEqual(MODE_SETTINGS, expected_modes)

        for mode, (use_gpu, lut_enabled) in expected_modes.items():
            with self.subTest(mode=mode):
                request = build_request(mode, FIXTURE, 'benchmark.mp4')
                self.assertIsInstance(request, ConversionRequest)
                self.assertEqual(request.input_path, FIXTURE)
                self.assertEqual(request.output_path, 'benchmark.mp4')
                self.assertEqual(request.gamma, 1.0)
                self.assertEqual(request.tonemapper, 'reinhard')
                self.assertEqual(request.quality_mode, 'cq')
                self.assertEqual(request.quality, 23)
                self.assertEqual(request.bit_depth, 10)
                self.assertIsNone(request.resolution)
                self.assertEqual(request.use_gpu, use_gpu)
                self.assertEqual(request.lut_enabled, lut_enabled)
                self.assertFalse(request.open_after_conversion)


class TestBuildCommand(unittest.TestCase):
    def test_adapts_app_command_for_looped_null_output_and_progress(self):
        input_path = str(REPO_ROOT / 'input.mp4')
        output_path = str(REPO_ROOT / 'benchmark.mp4')
        progress_path = str(REPO_ROOT / 'folder with spaces' / 'progress.txt')
        expected_encoder = 'hevc_nvenc'
        base_command = [
            'ffmpeg.exe', '-loglevel', 'info', '-init_hw_device', 'vulkan=vk:0',
            '-filter_hw_device', 'vk', '-i', os.path.normpath(input_path),
            '-filter_complex', '[0:v:0]libplacebo=lut=rec2020_to_rec709[vout]',
            '-map', '[vout]', '-map', '0:a?', '-c:v', expected_encoder,
            '-preset', 'p4', '-rc', 'vbr', '-cq', '23', '-pix_fmt', 'p010le',
            '-movflags', '+faststart', os.path.normpath(output_path), '-y',
        ]
        manager = ConversionManager()
        view = _HeadlessView()

        with (
            patch.object(manager, 'construct_ffmpeg_command',
                          return_value=list(base_command)) as build,
            patch('tools.benchmark_conversion.get_video_properties',
                  return_value={'width': 960}),
        ):
            command = build_command(
                'GPU accurate', input_path, output_path, progress_path, manager, view)

        build.assert_called_once()
        args = build.call_args.args
        self.assertEqual(args[0].use_gpu, True)
        self.assertEqual(args[0].lut_enabled, True)
        self.assertEqual(args[1], {'width': 960})
        self.assertIs(args[2], view)
        self.assertEqual(command[command.index('-c:v') + 1], expected_encoder)
        self.assertEqual(command[command.index('-map') + 1], '[vout]')
        self.assertIn('0:a?', command)
        self.assertIn('vulkan=vk:0', command)
        self.assertLess(command.index('-stream_loop'), command.index('-i'))
        self.assertEqual(command[command.index('-stats_period') + 1], '0.1')
        progress_url = command[command.index('-progress') + 1]
        self.assertTrue(progress_url.startswith('file:'))
        self.assertIn('folder with spaces/progress.txt', progress_url)
        self.assertNotIn('%20', progress_url)
        self.assertNotIn('-movflags', command)
        self.assertEqual(command[command.index('-f') + 1:command.index('-f') + 3], ['null', '-'])
        self.assertNotIn(output_path, command)
        self.assertEqual(command[-1], '-y')


class TestValidateModeCommand(unittest.TestCase):
    _PROPERTIES = {
        'width': 960,
        'height': 540,
        'bit_rate': 1_000_000,
        'frame_rate': 24.0,
        'codec_name': 'hevc',
        'color_transfer': 'smpte2084',
        'color_primaries': 'bt2020',
        'audio_codec': 'aac',
        'audio_bit_rate': 128_000,
        'subtitle_streams': [],
        'is_dolby_vision': False,
    }

    def _build_real_command(self, mode, *, encoder='h264_nvenc', libplacebo=True):
        manager = ConversionManager()
        with (
            patch('tools.benchmark_conversion.get_video_properties',
                  return_value=self._PROPERTIES),
            patch('src.conversion.vulkan_libplacebo_available',
                  return_value=libplacebo),
            patch('src.conversion.vulkan_cuda_interop_available',
                  return_value=False),
            patch.object(manager, 'detect_gpu_encoder', return_value=encoder),
        ):
            return build_command(
                mode, FIXTURE, 'benchmark.mp4', 'progress.txt', manager, _HeadlessView())

    def test_all_modes_match_their_cpu_or_gpu_lut_filter_path(self):
        commands = {
            mode: self._build_real_command(mode)
            for mode in ('CPU accurate', 'GPU accurate', 'GPU fast')
        }
        for mode, command in commands.items():
            with self.subTest(mode=mode):
                self.assertIsNone(validate_mode_command(mode, command))
                encoder_index = command.index('-c:v')
                expected_encoder = 'libx265' if mode == 'CPU accurate' else 'hevc_nvenc'
                self.assertEqual(command[encoder_index + 1], expected_encoder)
        mapped_streams = [
            [command[index + 1] for index, value in enumerate(command[:-1])
             if value == '-map']
            for command in commands.values()
        ]
        self.assertEqual(mapped_streams[0], mapped_streams[1])
        self.assertEqual(mapped_streams[0], mapped_streams[2])

        cpu_filter = commands['CPU accurate'][commands['CPU accurate'].index('-filter_complex') + 1]
        accurate_filter = commands['GPU accurate'][commands['GPU accurate'].index('-filter_complex') + 1]
        fast_filter = commands['GPU fast'][commands['GPU fast'].index('-filter_complex') + 1]
        self.assertIn('zscale=t=linear', cpu_filter)
        self.assertIn('lut3d=file=', cpu_filter)
        self.assertIn('libplacebo=', accurate_filter)
        self.assertIn('lut3d=file=', accurate_filter)
        self.assertIn('libplacebo=', fast_filter)
        self.assertNotIn('lut3d=file=', fast_filter)

    def test_gpu_request_that_resolves_to_cpu_filter_is_unavailable(self):
        command = self._build_real_command(
            'GPU accurate', encoder=None, libplacebo=False)
        diagnostic = validate_mode_command('GPU accurate', command)
        self.assertIsNotNone(diagnostic)
        self.assertIn('GPU tonemapping unavailable', diagnostic)

    def test_cpu_mode_rejects_hardware_encoder(self):
        command = self._build_real_command('CPU accurate')
        command[command.index('-c:v') + 1] = 'hevc_nvenc'
        self.assertIsNotNone(validate_mode_command('CPU accurate', command))


if __name__ == '__main__':
    unittest.main()
