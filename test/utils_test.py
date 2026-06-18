import sys
import threading
import unittest
from unittest.mock import patch, MagicMock, ANY
from src.utils import (
    get_video_properties, run_ffmpeg_command, extract_frame,
    extract_frame_with_conversion, get_executable_path, initialize_ffmpeg,
    build_libplacebo_filter, vulkan_libplacebo_available, reset_libplacebo_probe,
    get_maxfall, verify_ffmpeg_files,
)
import subprocess
from PIL import Image  # Added import
import json  # Ensure json is imported

# Constants
FFMPEG_EXECUTABLE = 'c:\\Users\\Torin\\Desktop\\HDR to SDR\\src\\ffmpeg.exe'
FFMPEG_FILTER = 'zscale=primaries=bt709:transfer=bt709:matrix=bt709,tonemap=reinhard,eq=gamma={gamma},scale={width}:{height}'

class TestGetVideoProperties(unittest.TestCase):

    @patch('src.utils.subprocess.Popen')
    def test_get_video_properties(self, mock_popen):
        # Mock subprocess.Popen to return a predefined JSON output as bytes, including 'format'
        mock_process = mock_popen.return_value
        mock_process.communicate.return_value = (b'''
        {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "bit_rate": "5000000",
                    "codec_name": "h264",
                    "avg_frame_rate": "30/1",
                    "duration": "600.0"
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "bit_rate": "128000"
                }
            ],
            "format": {
                "duration": "600.0"
            }
        }
        ''', b'')
        mock_process.returncode = 0

        input_file = 'path/to/test_video.mkv'
        expected_properties = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 5000000,
            "codec_name": "h264",
            "frame_rate": 30.0,
            "audio_codec": "aac",
            "audio_bit_rate": 128000,
            "duration": 600.0,
            "subtitle_streams": [],
            "color_primaries": "",
            "color_transfer": "",
        }

        properties = get_video_properties(input_file)
        self.assertEqual(properties, expected_properties)

    @patch('src.utils.subprocess.Popen')
    def test_get_video_properties_with_subtitles(self, mock_popen):
        """Test that get_video_properties correctly parses subtitle streams."""
        # Mock subprocess.Popen to return a predefined JSON output with subtitles and 'format'
        mock_process = mock_popen.return_value
        mock_process.communicate.return_value = (
            json.dumps({
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "codec_name": "h264",
                        "avg_frame_rate": "30/1",
                        "bit_rate": "4000000",
                        "duration": "120.0"
                    },
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "bit_rate": "128000"
                    },
                    {
                        "codec_type": "subtitle",
                        "codec_name": "srt",
                        "index": 2
                    }
                ],
                "format": {
                    "duration": "120.0"
                }
            }).encode('utf-8'),
            b''
        )
        mock_process.returncode = 0

        properties = get_video_properties("dummy_video.mp4")
        
        expected_properties = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": "h264",
            "frame_rate": 30.0,
            "duration": 120.0,
            "audio_codec": "aac",
            "audio_bit_rate": 128000,
            "subtitle_streams": [
                {
                    "codec_type": "subtitle",
                    "codec_name": "srt",
                    "index": 2
                }
            ],
            "color_primaries": "",
            "color_transfer": "",
        }
        self.assertEqual(properties, expected_properties)

class TestRunFfmpegCommand(unittest.TestCase):

    @patch('subprocess.Popen')
    def test_run_ffmpeg_command_success(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'output', b'')
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = run_ffmpeg_command(['ffmpeg', '-i', 'input.mp4', 'output.mkv'])
        self.assertEqual(result, b'output')

    @patch('subprocess.Popen')
    def test_run_ffmpeg_command_failure(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'', b'error')
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        with self.assertRaises(RuntimeError):
            run_ffmpeg_command(['ffmpeg', '-i', 'input.mp4', 'output.mkv'])

class TestExtractFrame(unittest.TestCase):

    @patch('src.utils.run_ffmpeg_command')
    @patch('src.utils.get_video_properties')
    def test_extract_frame_success(self, mock_get_props, mock_run_ffmpeg):
        # Mock the video properties to have a duration of 90 seconds
        mock_get_props.return_value = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 5000000,
            "codec_name": "h264",
            "frame_rate": 30.0,
            "audio_codec": "aac",
            "audio_bit_rate": 128000,
            "duration": 90.0,
            "subtitle_streams": []
        }

        # Provide valid PNG image bytes
        mock_run_ffmpeg.return_value = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
            b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
            b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82'
        )

        frame = extract_frame('input.mp4')
        self.assertIsInstance(frame, Image.Image)

        # Verify that ffmpeg was called with the correct command and timestamp
        expected_time = 90.0 / 3  # 30 seconds
        mock_run_ffmpeg.assert_called_once_with([
            ANY, '-ss', str(expected_time), '-i', 'input.mp4',
            '-vframes', '1', '-f', 'image2pipe', '-'
        ])

    @patch('subprocess.Popen')
    def test_extract_frame_failure(self, mock_popen):
        # Mock video properties first
        with patch('src.utils.get_video_properties') as mock_get_props:
            mock_get_props.return_value = {
                "width": 1920,
                "height": 1080,
                "bit_rate": 5000000,
                "codec_name": "h264",
                "frame_rate": 30.0,
                "audio_codec": "aac",
                "audio_bit_rate": 128000,
                "duration": 90.0,
                "subtitle_streams": []
            }

            # Setup the ffmpeg command failure
            mock_process = MagicMock()
            mock_process.communicate.return_value = (b'', b'error')
            mock_process.returncode = 1
            mock_popen.return_value = mock_process

            with self.assertRaises(RuntimeError):
                extract_frame('input.mp4')

class TestExtractFrameWithConversion(unittest.TestCase):

    @patch('src.utils.get_maxfall')  # Added patch for get_maxfall
    @patch('src.utils.run_ffmpeg_command')
    def test_extract_frame_with_conversion_success(self, mock_run_ffmpeg, mock_get_maxfall):
        # Mock the video properties to have a duration of 90 seconds
        with patch('src.utils.get_video_properties') as mock_get_props:
            mock_get_props.return_value = {
                "width": 1920,
                "height": 1080,
                "bit_rate": 4000000,
                "codec_name": "h264",
                "frame_rate": 30.0,
                "audio_codec": "aac",
                "audio_bit_rate": 128000,
                "duration": 90.0,
                "subtitle_streams": []
            }

            # Provide valid PNG image bytes
            mock_run_ffmpeg.return_value = (
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
                b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
                b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
                b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82'
            )

            mock_get_maxfall.return_value = 100.0  # Mocked get_maxfall return value
            gamma = 2.2
            frame = extract_frame_with_conversion('input.mp4', gamma, filter_index=1)  # Added filter_index
            
            # Update the expected_vf string to match actual format
            expected_vf = 'zscale=t=linear:npl=100.0,tonemap=reinhard,zscale=t=bt709:m=bt709:r=tv:p=bt709,eq=gamma=2.2,scale=iw:ih'
            
            self.assertIsInstance(frame, Image.Image)
    
            # Verify that ffmpeg was called with the correct command and timestamp
            expected_time = 90.0 / 3  # 30 seconds
    
            mock_run_ffmpeg.assert_called_once()
            actual_args = mock_run_ffmpeg.call_args[0][0]
            
            # Check all arguments except the first one (ffmpeg path)
            self.assertEqual(actual_args[1:], [
                '-ss', str(expected_time), '-i', 'input.mp4',
                '-vf', expected_vf, '-vframes', '1', '-f', 'image2pipe', '-'
            ])

    @patch('src.utils.run_ffmpeg_command')
    @patch('src.utils.get_maxfall')  # Added patch for get_maxfall
    def test_extract_frame_with_conversion_failure(self, mock_run_ffmpeg, mock_get_maxfall):
        # Mock video properties first
        with patch('src.utils.get_video_properties') as mock_get_props:
            mock_get_props.return_value = {
                "width": 1920,
                "height": 1080,
                "bit_rate": 4000000,
                "codec_name": "h264",
                "frame_rate": 30.0,
                "audio_codec": "aac",
                "audio_bit_rate": 128000,
                "duration": 90.0,
                "subtitle_streams": []
            }

            # Mock get_maxfall to return a predefined value
            mock_get_maxfall.return_value = 100.0  # Mocked get_maxfall return value

            # Setup run_ffmpeg_command to raise RuntimeError to simulate failure
            mock_run_ffmpeg.side_effect = RuntimeError("FFmpeg conversion failed")

            with self.assertRaises(RuntimeError):
                extract_frame_with_conversion('input.mp4', gamma=2.2, filter_index=1)  # Added gamma and filter_index

_VALID_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
    b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
    b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82'
)


class TestPreviewScaling(unittest.TestCase):
    """Extraction can target a preview resolution so the GUI decodes less data."""

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=_VALID_PNG)
    def test_extract_frame_scales_when_size_given(self, mock_run, _props):
        extract_frame('in.mp4', time_position=1.0, width=960, height=540)
        args = mock_run.call_args[0][0]
        self.assertIn('-vf', args)
        self.assertIn('scale=960:540', args[args.index('-vf') + 1])

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=_VALID_PNG)
    def test_extract_frame_unscaled_by_default(self, mock_run, _props):
        extract_frame('in.mp4', time_position=1.0)
        self.assertNotIn('-vf', mock_run.call_args[0][0])  # unchanged default

    @patch('src.utils.get_maxfall', return_value=100.0)
    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=_VALID_PNG)
    def test_conversion_uses_given_size_in_scale(self, mock_run, _props, _mf):
        extract_frame_with_conversion('in.mp4', gamma=1.0, filter_index=1,
                                      width=960, height=540)
        vf = mock_run.call_args[0][0][mock_run.call_args[0][0].index('-vf') + 1]
        self.assertIn('scale=960:540', vf)


class TestGetVideoPropertiesColorFields(unittest.TestCase):
    """get_video_properties must return HDR color metadata when present."""

    @patch('src.utils.subprocess.Popen')
    def test_color_primaries_and_transfer_returned(self, mock_popen):
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (json.dumps({
            "streams": [{
                "codec_type": "video", "width": 3840, "height": 2160,
                "codec_name": "hevc", "avg_frame_rate": "24000/1001",
                "bit_rate": "20000000",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
            }],
            "format": {"duration": "5400.0"},
        }).encode(), b'')
        props = get_video_properties('hdr.mkv')
        self.assertEqual(props['color_primaries'], 'bt2020')
        self.assertEqual(props['color_transfer'], 'smpte2084')

    @patch('src.utils.subprocess.Popen')
    def test_color_fields_default_empty_when_absent(self, mock_popen):
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (json.dumps({
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "codec_name": "h264", "avg_frame_rate": "30/1",
                         "bit_rate": "4000000"}],
            "format": {"duration": "60.0"},
        }).encode(), b'')
        props = get_video_properties('sdr.mp4')
        self.assertEqual(props.get('color_primaries'), '')
        self.assertEqual(props.get('color_transfer'), '')


class TestGetVideoPropertiesErrors(unittest.TestCase):

    @patch('src.utils.subprocess.Popen')
    def test_no_video_stream_returns_none(self, mock_popen):
        proc = mock_popen.return_value
        proc.communicate.return_value = (json.dumps({
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "10.0"},
        }).encode('utf-8'), b'')
        proc.returncode = 0
        self.assertIsNone(get_video_properties('x.mp4'))

    @patch('src.utils.subprocess.Popen')
    def test_invalid_json_returns_none(self, mock_popen):
        proc = mock_popen.return_value
        proc.communicate.return_value = (b'this is not json', b'')
        proc.returncode = 0
        self.assertIsNone(get_video_properties('x.mp4'))


class TestRunFfmpegColorspace(unittest.TestCase):

    @patch('src.utils.subprocess.Popen')
    def test_colorspace_error_gives_friendly_message(self, mock_popen):
        proc = MagicMock()
        proc.communicate.return_value = (
            b'', b'Impossible to convert between the formats... '
                 b'no path between colorspaces\n')
        proc.returncode = 1
        mock_popen.return_value = proc
        with self.assertRaises(RuntimeError) as ctx:
            run_ffmpeg_command(['ffmpeg', '-i', 'in.mkv', 'out.mkv'])
        self.assertIn('Colorspace', str(ctx.exception))


class TestExecutableResolution(unittest.TestCase):

    @patch('src.utils.os.path.exists', return_value=True)
    def test_found_in_bundle_dir(self, _exists):
        path = get_executable_path('ffmpeg.exe')
        # Resolves next to the module (or its stripped name off Windows).
        self.assertTrue(path.endswith('ffmpeg.exe') or path.endswith('ffmpeg'))

    @patch('src.utils.shutil.which', return_value='/usr/bin/ffmpeg')
    @patch('src.utils.os.path.exists', return_value=False)
    def test_falls_back_to_system_path(self, _exists, _which):
        self.assertEqual(get_executable_path('ffmpeg.exe'), '/usr/bin/ffmpeg')

    @patch('src.utils.shutil.which', return_value=None)
    @patch('src.utils.os.path.exists', return_value=False)
    def test_missing_everywhere_raises(self, _exists, _which):
        with self.assertRaises(FileNotFoundError):
            get_executable_path('ffmpeg.exe')


class TestInitializeFfmpeg(unittest.TestCase):

    @patch('src.utils.verify_ffmpeg_files', side_effect=RuntimeError('missing'))
    def test_failure_raises_without_a_dialog(self, _verify):
        # messagebox was removed from utils; failure must propagate, not pop a UI.
        with self.assertRaises(RuntimeError):
            initialize_ffmpeg()


class TestBuildLibplaceboFilter(unittest.TestCase):
    """The libplacebo (GPU) tonemap filter builder."""

    def test_static_disables_peak_detection(self):
        f = build_libplacebo_filter(0, 2.2, 'reinhard')
        self.assertIn('libplacebo=', f)
        self.assertIn('tonemapping=reinhard', f)
        self.assertIn('peak_detect=0', f)
        self.assertIn('eq=gamma=2.2', f)
        # Default keeps source resolution (no resize on a full conversion).
        self.assertIn('w=iw:h=ih', f)

    def test_dynamic_enables_peak_detection(self):
        f = build_libplacebo_filter(1, 1.0, 'mobius')
        self.assertIn('peak_detect=1', f)
        self.assertIn('tonemapping=mobius', f)

    def test_tonemapper_is_lowercased(self):
        self.assertIn('tonemapping=hable', build_libplacebo_filter(0, 1.0, 'Hable'))

    def test_explicit_size_passed_to_libplacebo(self):
        f = build_libplacebo_filter(0, 1.0, 'reinhard', width=960, height=540)
        self.assertIn('w=960:h=540', f)


class TestVulkanLibplaceboProbe(unittest.TestCase):
    """The cached Vulkan/libplacebo capability probe."""

    def setUp(self):
        reset_libplacebo_probe()
        self.addCleanup(reset_libplacebo_probe)

    @patch('src.utils.FFMPEG_EXECUTABLE', None)
    def test_false_without_ffmpeg(self):
        self.assertFalse(vulkan_libplacebo_available())

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run')
    def test_true_when_probe_succeeds(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(vulkan_libplacebo_available())
        mock_run.assert_called_once()

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run')
    def test_false_when_probe_fails(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(vulkan_libplacebo_available())

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run', side_effect=OSError('boom'))
    def test_false_when_probe_raises(self, _run):
        self.assertFalse(vulkan_libplacebo_available())

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run')
    def test_result_is_cached(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        vulkan_libplacebo_available()
        vulkan_libplacebo_available()
        mock_run.assert_called_once()  # probed once, then cached


# ---------------------------------------------------------------------------
# Issue #1 — Concurrency: _MAXFALL_CACHE must be guarded by a lock
# ---------------------------------------------------------------------------

class TestMaxfallConcurrency(unittest.TestCase):
    """get_maxfall must call _compute_maxfall exactly once under concurrent cache misses.

    Without a threading.Lock, two threads that both see the cache miss can both
    call _compute_maxfall before either one writes the result back — spawning two
    ffprobe processes for the same file.  The test below is RED today and will
    turn GREEN once a lock serialises the read-check-write sequence.
    """

    def setUp(self):
        import src.utils as _u
        self._cache = _u._MAXFALL_CACHE
        self._cache.clear()
        self.addCleanup(self._cache.clear)

    def test_concurrent_calls_compute_once(self):
        """Four threads racing on an uncached path must spawn only one ffprobe process."""
        call_count: list[int] = []
        start = threading.Barrier(4)

        def slow_compute(path: str) -> float:
            # time.sleep releases the GIL, giving other threads a chance to pass
            # the cache-miss check before the first thread writes the result back.
            import time
            time.sleep(0.05)
            call_count.append(1)
            return 400.0

        results: list[float] = []

        def worker() -> None:
            start.wait()  # all four threads hit the cache check simultaneously
            results.append(get_maxfall('/fake/concurrent/video.mkv'))

        with patch('src.utils._compute_maxfall', side_effect=slow_compute):
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertEqual(len(results), 4, "all callers must receive a return value")
        self.assertTrue(
            all(r == 400.0 for r in results),
            "all callers must receive the same cached value",
        )
        self.assertEqual(
            len(call_count), 1,
            f"_compute_maxfall was called {len(call_count)} time(s); expected exactly 1 "
            "(a threading.Lock must prevent duplicate ffprobe launches)",
        )


# ---------------------------------------------------------------------------
# Issue #3 — Portability: verify_ffmpeg_files must use platform-agnostic keys
# ---------------------------------------------------------------------------

class TestVerifyFfmpegFilesPortability(unittest.TestCase):
    """verify_ffmpeg_files must use extension-free keys ('ffmpeg', not 'ffmpeg.exe').

    The current implementation hard-codes Windows-specific names as dict keys and
    as the lookup arguments passed to get_executable_path.  Both assertions below
    are RED today and will turn GREEN once the keys are stripped of the .exe suffix.
    """

    @patch('src.utils.get_executable_path', return_value='/usr/local/bin/ffmpeg')
    def test_returned_keys_contain_no_exe_suffix(self, _exec: MagicMock) -> None:
        import src.utils as _u
        orig = (_u.FFMPEG_EXECUTABLE, _u.FFPROBE_EXECUTABLE)
        try:
            result = _u.verify_ffmpeg_files()
        finally:
            _u.FFMPEG_EXECUTABLE, _u.FFPROBE_EXECUTABLE = orig

        for key in result:
            self.assertFalse(
                key.endswith('.exe'),
                f"Key '{key}' must not carry a Windows-specific .exe suffix",
            )
        self.assertIn('ffmpeg',  result, "result dict must expose a 'ffmpeg' key")
        self.assertIn('ffprobe', result, "result dict must expose a 'ffprobe' key")

    @patch('src.utils.get_executable_path', return_value='/usr/bin/ffmpeg')
    def test_get_executable_path_not_called_with_exe_suffixes(self, mock_exec: MagicMock) -> None:
        import src.utils as _u
        orig = (_u.FFMPEG_EXECUTABLE, _u.FFPROBE_EXECUTABLE)
        try:
            _u.verify_ffmpeg_files()
        finally:
            _u.FFMPEG_EXECUTABLE, _u.FFPROBE_EXECUTABLE = orig

        for call in mock_exec.call_args_list:
            name: str = call[0][0]
            self.assertFalse(
                name.endswith('.exe'),
                f"get_executable_path received Windows-specific name '{name}'; "
                "suffixes must be appended dynamically, not baked into the key",
            )


# ---------------------------------------------------------------------------
# Issue #4 — DRY: STARTUPINFO behavioral regression guards
# ---------------------------------------------------------------------------

class TestStartupinfoConsistency(unittest.TestCase):
    """Regression guards for the STARTUPINFO deduplication refactor (issue #4).

    These tests are already GREEN and must remain GREEN after the duplicate
    STARTUPINFO blocks in run_ffmpeg_command / _compute_maxfall / get_video_properties
    / vulkan_libplacebo_available are consolidated into a shared helper.
    """

    @patch('src.utils.subprocess.Popen')
    def test_run_ffmpeg_command_hides_console_on_windows(self, mock_popen: MagicMock) -> None:
        """run_ffmpeg_command must pass a STARTUPINFO on Windows (hide console)."""
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b'ok', b'')
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        run_ffmpeg_command(['ffmpeg', '-version'])

        kwargs = mock_popen.call_args[1]
        if sys.platform == 'win32':
            self.assertIsNotNone(
                kwargs.get('startupinfo'),
                "Windows: startupinfo must be set to suppress the console window",
            )
        else:
            self.assertIsNone(
                kwargs.get('startupinfo'),
                "Non-Windows: startupinfo must be None",
            )

    @patch('src.utils.subprocess.check_output', return_value=b'{"frames": []}')
    def test_compute_maxfall_hides_console_on_windows(self, mock_check: MagicMock) -> None:
        """_compute_maxfall must pass a STARTUPINFO on Windows (hide console)."""
        import src.utils as _u
        _u._MAXFALL_CACHE.clear()
        try:
            _u._compute_maxfall('/fake/video.mkv')
        except Exception:
            pass  # JSON parse / no MAXFALL — irrelevant to this test

        kwargs = mock_check.call_args[1]
        if sys.platform == 'win32':
            self.assertIsNotNone(
                kwargs.get('startupinfo'),
                "Windows: startupinfo must be set in _compute_maxfall",
            )
        else:
            self.assertIsNone(
                kwargs.get('startupinfo'),
                "Non-Windows: startupinfo must be None in _compute_maxfall",
            )
        _u._MAXFALL_CACHE.clear()


if __name__ == '__main__':
    unittest.main()