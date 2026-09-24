import io
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
    FIXTURE_PATH, MEASURE_ORDER, MODE_SETTINGS, RunResult, _HeadlessView,
    build_command, build_request, collect_metadata, format_report, main,
    median_fps, probe_physical_vulkan, read_progress, run_timed,
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


class TestBenchmarkStatistics(unittest.TestCase):
    @staticmethod
    def _run(fps, status='SUCCESSFUL'):
        return RunResult(status, fps * 60, 60.0)

    def test_median_requires_two_successful_measured_runs(self):
        samples = [self._run(10), self._run(20, 'FAILED'), self._run(30)]
        self.assertEqual(median_fps(samples), 20.0)
        self.assertIsNone(median_fps([self._run(10), self._run(20, 'FAILED')]))
        self.assertIsNone(median_fps([self._run(10, 'FAILED')]))
        self.assertIsNone(median_fps([]))

    def test_report_has_run_details_and_all_pairwise_median_comparisons(self):
        results = {
            'CPU accurate': [self._run(10), self._run(10), self._run(10)],
            'GPU accurate': [self._run(15), self._run(15), self._run(15)],
            'GPU fast': [self._run(12), self._run(12), self._run(12)],
        }
        report = format_report({'GPU model': 'Example GPU'}, results)

        self.assertIn('Run 1', report)
        self.assertIn('Run 2', report)
        self.assertIn('Run 3', report)
        self.assertIn('600 frames / 60.00s / 10.00 FPS', report)
        self.assertIn('Median: 15.00 FPS (3/3 successful runs)', report)
        self.assertIn('1.50x', report)
        self.assertIn('+5.00 FPS', report)
        self.assertIn('+50.0% higher throughput', report)
        self.assertIn('1.25x', report)
        self.assertIn('1.20x', report)
        self.assertIn('GPU model: Example GPU', report)
        self.assertIn('results apply only to the tested hardware and fixture', report)
        self.assertNotIn('faster', report.lower())

    def test_report_labels_partial_runs_and_omits_invalid_pairwise_results(self):
        results = {
            'CPU accurate': [self._run(10), self._run(20), self._run(30, 'FAILED')],
            'GPU accurate': [self._run(15), self._run(20, 'FAILED'), self._run(30, 'FAILED')],
            'GPU fast': [self._run(12, 'UNAVAILABLE') for _ in range(3)],
        }
        report = format_report({}, results)

        self.assertIn('Median: 15.00 FPS (2/3 successful runs)', report)
        self.assertIn('Median: unavailable (1/3 successful runs)', report)
        self.assertIn('UNAVAILABLE', report)
        self.assertIn('FAILED', report)
        self.assertNotIn('CPU accurate vs GPU accurate:', report)
        self.assertNotIn('GPU accurate vs GPU fast:', report)
        self.assertNotIn('CPU accurate vs GPU fast:', report)

    def test_missing_optional_metadata_is_reported_as_unknown(self):
        with (
            patch('tools.benchmark_conversion.platform.platform', return_value='Test OS'),
            patch('tools.benchmark_conversion.platform.processor', return_value=''),
            patch('tools.benchmark_conversion.FFMPEG_EXECUTABLE', None),
        ):
            metadata = collect_metadata({}, None, {})

        self.assertEqual(metadata['Operating system'], 'Test OS')
        self.assertEqual(metadata['CPU model'], 'Unknown')
        self.assertEqual(metadata['GPU model'], 'Unknown')
        self.assertEqual(metadata['FFmpeg version/build'], 'Unknown')

    def test_metadata_records_fixture_settings_and_selected_encoders(self):
        version = Mock(returncode=0, stdout=(
            'ffmpeg version N-12345\nbuilt with gcc 15\nconfiguration: --enable-vulkan\n'))
        commands = {
            'CPU accurate': ['ffmpeg.exe', '-c:v', 'libx265'],
            'GPU accurate': ['ffmpeg.exe', '-c:v', 'hevc_nvenc'],
        }
        properties = {
            'width': 960,
            'height': 540,
            'codec_name': 'hevc',
            'bit_depth': 10,
            'color_primaries': 'bt2020',
            'color_transfer': 'smpte2084',
            'frame_rate': 24.0,
            'duration': 2.0,
        }
        with (
            patch('tools.benchmark_conversion.platform.platform', return_value='Windows test'),
            patch('tools.benchmark_conversion.platform.processor', return_value='CPU test'),
            patch('tools.benchmark_conversion.FFMPEG_EXECUTABLE', 'ffmpeg.exe'),
            patch('tools.benchmark_conversion.subprocess.run', return_value=version),
        ):
            metadata = collect_metadata(properties, 'GPU test (discrete)', commands)

        self.assertEqual(metadata['FFmpeg version/build'],
                         'ffmpeg version N-12345 | built with gcc 15')
        self.assertIn('960x540, hevc, 10-bit, bt2020/smpte2084', metadata['Fixture'])
        self.assertIn('CPU accurate: libx265', metadata['Selected encoders'])
        self.assertIn('GPU accurate: hevc_nvenc', metadata['Selected encoders'])
        self.assertIn('10s warm-up, 3 interleaved 60s runs per mode', metadata['Settings'])


class TestPhysicalVulkanProbe(unittest.TestCase):
    def test_rejects_lavapipe_selected_by_active_icd_without_running_probe(self):
        with (
            patch.dict(os.environ, {
                'VK_ICD_FILENAMES': '/usr/share/vulkan/icd.d/lvp_icd.json',
            }),
            patch('tools.benchmark_conversion.subprocess.run') as run,
        ):
            device, diagnostic = probe_physical_vulkan()

        self.assertIsNone(device)
        self.assertIn('lavapipe', diagnostic.lower())
        run.assert_not_called()

    def test_accepts_physical_ffmpeg_device_and_rejects_software_log(self):
        physical = Mock(returncode=0, stderr=(
            'Device 0 selected: NVIDIA Example GPU (discrete) (0x10de)\n'))
        software = Mock(returncode=0, stderr=(
            'Device 0 selected: llvmpipe (software) (0x0)\n'))
        with (
            patch.dict(os.environ, {}, clear=True),
            patch('tools.benchmark_conversion.subprocess.run',
                  side_effect=[physical, software]),
            patch('tools.benchmark_conversion.FFMPEG_EXECUTABLE', 'ffmpeg.exe'),
        ):
            device, diagnostic = probe_physical_vulkan()
            software_device, software_diagnostic = probe_physical_vulkan()

        self.assertEqual(device, 'NVIDIA Example GPU (discrete)')
        self.assertIsNone(diagnostic)
        self.assertIsNone(software_device)
        self.assertIn('software', software_diagnostic.lower())


class TestBenchmarkMain(unittest.TestCase):
    _PROPERTIES = {
        'width': 960,
        'height': 540,
        'bit_rate': 1_000_000,
        'frame_rate': 24.0,
        'codec_name': 'hevc',
        'bit_depth': 10,
        'color_transfer': 'smpte2084',
        'color_primaries': 'bt2020',
        'is_dolby_vision': False,
    }

    def test_warms_up_then_runs_exact_interleaved_schedule_after_failures(self):
        warmups = [RunResult('SUCCESSFUL', 100, 10.0) for _ in range(3)]
        measured = [RunResult('SUCCESSFUL', 600, 60.0) for _ in range(9)]
        measured[2] = RunResult('FAILED', 0, 60.0, 'simulated failure')
        run_results = warmups + measured
        run_calls = []

        def record_run(command, duration, temp_dir):
            run_calls.append((command[1], duration))
            return run_results[len(run_calls) - 1]

        output = io.StringIO()
        with (
            patch('tools.benchmark_conversion.get_video_properties',
                  return_value=self._PROPERTIES),
            patch('tools.benchmark_conversion.probe_physical_vulkan',
                  return_value=('NVIDIA Example GPU (discrete)', None)),
            patch('tools.benchmark_conversion.build_command',
                  side_effect=lambda mode, *_args: ['ffmpeg.exe', mode]),
            patch('tools.benchmark_conversion.validate_mode_command', return_value=None),
            patch('tools.benchmark_conversion.collect_metadata', return_value={}),
            patch('tools.benchmark_conversion.run_timed', side_effect=record_run),
            patch('sys.stdout', output),
        ):
            result = main([])

        expected_modes = [
            'CPU accurate', 'GPU accurate', 'GPU fast',
            *MEASURE_ORDER[0], *MEASURE_ORDER[1], *MEASURE_ORDER[2],
        ]
        self.assertEqual([mode for mode, _ in run_calls], expected_modes)
        self.assertEqual([duration for _, duration in run_calls],
                         [10.0] * 3 + [60.0] * 9)
        self.assertEqual(result, 0)
        self.assertIn('simulated failure', output.getvalue())

    def test_software_vulkan_marks_gpu_modes_unavailable_before_any_gpu_run(self):
        run_results = [RunResult('SUCCESSFUL', 100, 10.0)] + [
            RunResult('SUCCESSFUL', 600, 60.0) for _ in range(3)
        ]
        run_calls = []

        def record_run(command, duration, temp_dir):
            run_calls.append(command[1])
            return run_results[len(run_calls) - 1]

        output = io.StringIO()
        with (
            patch('tools.benchmark_conversion.get_video_properties',
                  return_value=self._PROPERTIES),
            patch('tools.benchmark_conversion.probe_physical_vulkan',
                  return_value=(None, 'Software Vulkan selected: lavapipe')),
            patch('tools.benchmark_conversion.build_command',
                  side_effect=lambda mode, *_args: ['ffmpeg.exe', mode]) as builder,
            patch('tools.benchmark_conversion.validate_mode_command', return_value=None),
            patch('tools.benchmark_conversion.collect_metadata', return_value={}),
            patch('tools.benchmark_conversion.run_timed', side_effect=record_run),
            patch('sys.stdout', output),
        ):
            result = main([])

        self.assertEqual([call.args[0] for call in builder.call_args_list],
                         ['CPU accurate'])
        self.assertEqual(run_calls, ['CPU accurate'] * 4)
        self.assertEqual(result, 0)
        self.assertIn('UNAVAILABLE', output.getvalue())
        self.assertIn('lavapipe', output.getvalue())

    def test_failed_warmup_skips_only_that_modes_measured_runs(self):
        run_calls = []
        run_results = [
            RunResult('FAILED', 0, 10.0, 'warm-up failure'),
            RunResult('SUCCESSFUL', 100, 10.0),
            RunResult('SUCCESSFUL', 100, 10.0),
            *[RunResult('SUCCESSFUL', 600, 60.0) for _ in range(6)],
        ]

        def record_run(command, duration, _temp_dir):
            run_calls.append((command[1], duration))
            return run_results[len(run_calls) - 1]

        output = io.StringIO()
        with (
            patch('tools.benchmark_conversion.get_video_properties',
                  return_value=self._PROPERTIES),
            patch('tools.benchmark_conversion.probe_physical_vulkan',
                  return_value=('NVIDIA Example GPU (discrete)', None)),
            patch('tools.benchmark_conversion.build_command',
                  side_effect=lambda mode, *_args: ['ffmpeg.exe', mode]),
            patch('tools.benchmark_conversion.validate_mode_command', return_value=None),
            patch('tools.benchmark_conversion.collect_metadata', return_value={}),
            patch('tools.benchmark_conversion.run_timed', side_effect=record_run),
            patch('sys.stdout', output),
        ):
            result = main([])

        self.assertEqual(result, 0)
        self.assertEqual(sum(mode == 'CPU accurate' for mode, _ in run_calls), 1)
        self.assertEqual(len(run_calls), 9)
        self.assertIn('Warm-up failed; measured runs skipped: warm-up failure', output.getvalue())


if __name__ == '__main__':
    unittest.main()
