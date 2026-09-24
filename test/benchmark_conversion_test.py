import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.conversion import ConversionManager, ConversionRequest
from tools.benchmark_conversion import (
    FIXTURE_PATH, MODE_SETTINGS, RunResult, _HeadlessView, build_command,
    build_request, read_progress, run_timed, validate_mode_command,
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


class TestReadProgress(unittest.TestCase):
    def test_reads_complete_frame_records_from_a_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='benchmark progress ') as folder:
            progress = Path(folder) / 'progress file.txt'
            progress.write_bytes(b'frame=24\nfps=30.0\nprogress=continue\n')

            offset, frames = read_progress(progress, 0, 0)

        self.assertEqual(offset, len(b'frame=24\nfps=30.0\nprogress=continue\n'))
        self.assertEqual(frames, 24)

    def test_leaves_an_incomplete_trailing_record_for_the_next_poll(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'progress.txt'
            progress.write_bytes(b'frame=12')

            offset, frames = read_progress(progress, 0, 12)

        self.assertEqual((offset, frames), (0, 12))

    def test_reads_appended_chunks_and_ignores_invalid_or_decreasing_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'progress.txt'
            progress.write_bytes(b'frame=18\n')
            offset, frames = read_progress(progress, 0, 0)
            with progress.open('ab') as stream:
                stream.write(b'frame=18\nframe=4\nframe=nope\nframe=23')
            offset, frames = read_progress(progress, offset, frames)

        self.assertEqual(frames, 18)
        self.assertEqual(
            offset, len(b'frame=18\nframe=18\nframe=4\nframe=nope\n'))

    def test_accepts_a_later_complete_count_after_an_incomplete_chunk(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'progress.txt'
            progress.write_bytes(b'frame=7\nframe=')
            offset, frames = read_progress(progress, 0, 0)
            with progress.open('ab') as stream:
                stream.write(b'11\nprogress=end\n')
            offset, frames = read_progress(progress, offset, frames)

        self.assertEqual(frames, 11)
        self.assertEqual(offset, len(b'frame=7\nframe=11\nprogress=end\n'))


class TestRunTimed(unittest.TestCase):
    @staticmethod
    def _command():
        return ['ffmpeg.exe', '-progress', 'file:placeholder.txt', '-i', 'input.mp4']

    @staticmethod
    def _process(wait_effect=None, poll_return=None):
        process = Mock()
        process.stdin = Mock()
        process.poll.return_value = poll_return
        process.wait.side_effect = wait_effect or [0]
        process.returncode = poll_return
        return process

    def test_freezes_frames_and_duration_at_cutoff_then_stops_and_cleans_files(self):
        with tempfile.TemporaryDirectory(prefix='benchmark run ') as folder:
            process = self._process(wait_effect=[0])

            def launch(command, **kwargs):
                progress_url = command[command.index('-progress') + 1]
                Path(progress_url.removeprefix('file:')).write_bytes(b'frame=120\n')
                self.assertIs(kwargs['stdin'], subprocess.PIPE)
                self.assertIs(kwargs['stdout'], subprocess.DEVNULL)
                self.assertEqual(kwargs['stderr'].mode, 'wb')
                return process

            def late_progress(_interval):
                progress_url = popen.call_args.args[0][
                    popen.call_args.args[0].index('-progress') + 1]
                Path(progress_url.removeprefix('file:')).write_bytes(b'frame=999\n')

            popen = Mock(side_effect=launch)
            with (
                patch('tools.benchmark_conversion.subprocess.Popen', popen),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 0.0, 60.0]),
                patch('tools.benchmark_conversion.time.sleep', side_effect=late_progress),
            ):
                result = run_timed(self._command(), 60.0, Path(folder))

            self.assertIsInstance(result, RunResult)
            self.assertEqual((result.status, result.frames, result.duration),
                             ('SUCCESSFUL', 120, 60.0))
            self.assertEqual(result.fps, 2.0)
            process.stdin.write.assert_called_once_with(b'q\n')
            process.wait.assert_called_once()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_escalates_from_graceful_stop_to_terminate_and_kill(self):
        with tempfile.TemporaryDirectory() as folder:
            process = self._process(wait_effect=[
                subprocess.TimeoutExpired('ffmpeg.exe', 1),
                subprocess.TimeoutExpired('ffmpeg.exe', 1),
                0,
            ])
            with (
                patch('tools.benchmark_conversion.subprocess.Popen', return_value=process),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 60.0]),
                patch('tools.benchmark_conversion.time.sleep'),
            ):
                result = run_timed(self._command(), 60.0, Path(folder))

        self.assertEqual(result.status, 'FAILED')
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 3)

    def test_early_exit_and_zero_frames_are_failed_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            early = self._process(poll_return=0)
            with (
                patch('tools.benchmark_conversion.subprocess.Popen', return_value=early),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 0.0]),
                patch('tools.benchmark_conversion.time.sleep'),
            ):
                early_result = run_timed(self._command(), 60.0, Path(folder))

        with tempfile.TemporaryDirectory() as folder:
            zero = self._process(wait_effect=[0])
            with (
                patch('tools.benchmark_conversion.subprocess.Popen', return_value=zero),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 0.0, 60.0]),
                patch('tools.benchmark_conversion.time.sleep'),
            ):
                zero_result = run_timed(self._command(), 60.0, Path(folder))

        self.assertEqual(early_result.status, 'FAILED')
        self.assertEqual(zero_result.status, 'FAILED')
        self.assertEqual(zero_result.frames, 0)

    def test_terminate_after_cutoff_retains_measured_sample(self):
        with tempfile.TemporaryDirectory() as folder:
            process = self._process(wait_effect=[
                subprocess.TimeoutExpired('ffmpeg.exe', 1), 0,
            ])

            def launch(command, **_kwargs):
                progress_url = command[command.index('-progress') + 1]
                Path(progress_url.removeprefix('file:')).write_bytes(b'frame=42\n')
                return process

            with (
                patch('tools.benchmark_conversion.subprocess.Popen', side_effect=launch),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 0.0, 60.0]),
                patch('tools.benchmark_conversion.time.sleep'),
            ):
                result = run_timed(self._command(), 60.0, Path(folder))

        self.assertEqual((result.status, result.frames, result.duration),
                         ('SUCCESSFUL', 42, 60.0))
        self.assertIn('terminate', result.diagnostic)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    def test_keyboard_interrupt_stops_and_reaps_ffmpeg_before_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            process = self._process(wait_effect=[0])
            with (
                patch('tools.benchmark_conversion.subprocess.Popen',
                      return_value=process),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 0.0]),
                patch('tools.benchmark_conversion.time.sleep',
                      side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_timed(self._command(), 60.0, Path(folder))

            process.stdin.write.assert_called_once_with(b'q\n')
            process.wait.assert_called_once()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_keeps_temporary_files_if_ffmpeg_cannot_be_reaped(self):
        with tempfile.TemporaryDirectory() as folder:
            process = self._process()
            process.wait.side_effect = OSError('wait failed')
            with (
                patch('tools.benchmark_conversion.subprocess.Popen',
                      return_value=process),
                patch('tools.benchmark_conversion.time.perf_counter',
                      side_effect=[0.0, 60.0]),
                patch('tools.benchmark_conversion.time.sleep'),
            ):
                result = run_timed(self._command(), 60.0, Path(folder))

            leftovers = list(Path(folder).iterdir())
            self.assertEqual(result.status, 'FAILED')
            self.assertEqual(len(leftovers), 2)
            for path in leftovers:
                path.unlink()


if __name__ == '__main__':
    unittest.main()
