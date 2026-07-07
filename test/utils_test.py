import contextlib
import io
import sys
import threading
import unittest
from unittest.mock import patch, MagicMock, ANY
from src.utils import (
    get_video_properties, run_ffmpeg_command, extract_frame,
    extract_frame_with_conversion, get_executable_path, initialize_ffmpeg,
    build_libplacebo_filter, vulkan_libplacebo_available, reset_libplacebo_probe,
    vulkan_cuda_interop_available, reset_cuda_interop_probe, VULKAN_CUDA_DEVICE_ARGS,
    get_maxcll, verify_ffmpeg_files, clear_video_properties_cache,
    clear_maxfall_cache,
    extract_frames_batch, extract_frames_with_conversion_batch, _split_png_frames,
)
import subprocess
from PIL import Image  # Added import
import json  # Ensure json is imported

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
            "bit_depth": 8,
            "is_dolby_vision": False,
            "dovi_profile": None,
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
            "bit_depth": 8,
            "is_dolby_vision": False,
            "dovi_profile": None,
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

    @patch('src.utils.run_ffmpeg_command')
    def test_extract_frame_with_conversion_success(self, mock_run_ffmpeg):
        with patch('src.utils.get_video_properties') as mock_get_props:
            mock_get_props.return_value = {
                "width": 1920, "height": 1080, "bit_rate": 4000000,
                "codec_name": "h264", "frame_rate": 30.0,
                "audio_codec": "aac", "audio_bit_rate": 128000,
                "duration": 90.0, "subtitle_streams": []
            }
            mock_run_ffmpeg.return_value = (
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
                b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
                b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
                b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82'
            )

            frame = extract_frame_with_conversion('input.mp4', gamma=2.2)

            expected_vf = (
                'zscale=t=linear:npl=100,tonemap=reinhard,'
                'zscale=t=bt709:m=bt709:r=tv:p=bt709,eq=gamma=2.2,'
                'scale=iw:ih:force_original_aspect_ratio=decrease'
            )
            self.assertIsInstance(frame, Image.Image)
            mock_run_ffmpeg.assert_called_once()
            actual_args = mock_run_ffmpeg.call_args[0][0]
            self.assertEqual(actual_args[1:], [
                '-ss', str(90.0 / 3), '-i', 'input.mp4',
                '-vf', expected_vf, '-vframes', '1', '-f', 'image2pipe', '-'
            ])

    @patch('src.utils.run_ffmpeg_command')
    def test_extract_frame_with_conversion_failure(self, mock_run_ffmpeg):
        with patch('src.utils.get_video_properties') as mock_get_props:
            mock_get_props.return_value = {
                "width": 1920, "height": 1080, "bit_rate": 4000000,
                "codec_name": "h264", "frame_rate": 30.0,
                "audio_codec": "aac", "audio_bit_rate": 128000,
                "duration": 90.0, "subtitle_streams": []
            }
            mock_run_ffmpeg.side_effect = RuntimeError("FFmpeg conversion failed")

            with self.assertRaises(RuntimeError):
                extract_frame_with_conversion('input.mp4', gamma=2.2)

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

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=_VALID_PNG)
    def test_conversion_uses_given_size_in_scale(self, mock_run, _props):
        extract_frame_with_conversion('in.mp4', gamma=1.0, width=960, height=540)
        vf = mock_run.call_args[0][0][mock_run.call_args[0][0].index('-vf') + 1]
        self.assertIn('scale=960:540', vf)

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=_VALID_PNG)
    def test_extract_frame_scale_does_not_upscale(self, mock_run, _props):
        """scale filter must include force_original_aspect_ratio=decrease so a
        1080p source is not upscaled when the 4K cap is larger than the source."""
        extract_frame('in.mp4', time_position=1.0, width=3840, height=2160)
        vf = mock_run.call_args[0][0][mock_run.call_args[0][0].index('-vf') + 1]
        self.assertIn('force_original_aspect_ratio=decrease', vf,
                      "scale filter is missing force_original_aspect_ratio=decrease")

    def test_ffmpeg_filter_scale_does_not_upscale(self):
        """FFMPEG_FILTER must include force_original_aspect_ratio=decrease."""
        from src.utils import FFMPEG_FILTER
        self.assertIn('force_original_aspect_ratio=decrease', FFMPEG_FILTER)


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


class TestGetVideoPropertiesBitDepth(unittest.TestCase):
    """get_video_properties must surface the source's actual bit depth, even
    when it's above the 8/10-bit output options (e.g. 12-bit or 16-bit masters)."""

    @staticmethod
    def _probe(mock_popen, video_stream):
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (json.dumps({
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "codec_name": "hevc", "avg_frame_rate": "24/1",
                         "bit_rate": "10000000", **video_stream}],
            "format": {"duration": "60.0"},
        }).encode(), b'')

    @patch('src.utils.subprocess.Popen')
    def test_bit_depth_from_bits_per_raw_sample(self, mock_popen):
        self._probe(mock_popen, {"bits_per_raw_sample": "12", "pix_fmt": "yuv420p12le"})
        props = get_video_properties('bitdepth_raw.mkv')
        self.assertEqual(props['bit_depth'], 12)

    @patch('src.utils.subprocess.Popen')
    def test_bit_depth_falls_back_to_pix_fmt(self, mock_popen):
        self._probe(mock_popen, {"pix_fmt": "yuv420p10le"})
        props = get_video_properties('bitdepth_pixfmt.mkv')
        self.assertEqual(props['bit_depth'], 10)

    @patch('src.utils.subprocess.Popen')
    def test_bit_depth_16_from_pix_fmt(self, mock_popen):
        self._probe(mock_popen, {"pix_fmt": "yuv444p16le"})
        props = get_video_properties('bitdepth_16.mkv')
        self.assertEqual(props['bit_depth'], 16)

    @patch('src.utils.subprocess.Popen')
    def test_bit_depth_defaults_to_8_when_undeterminable(self, mock_popen):
        self._probe(mock_popen, {"pix_fmt": "yuv420p"})
        props = get_video_properties('bitdepth_default.mp4')
        self.assertEqual(props['bit_depth'], 8)

    @patch('src.utils.subprocess.Popen')
    def test_bit_depth_defaults_to_8_when_no_pix_fmt_or_raw_sample(self, mock_popen):
        self._probe(mock_popen, {})
        props = get_video_properties('bitdepth_missing.mp4')
        self.assertEqual(props['bit_depth'], 8)


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
        # get_video_properties logs the JSONDecodeError via print() on this path;
        # silence it here so a passing suite run stays quiet.
        with contextlib.redirect_stdout(io.StringIO()):
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

    def test_always_enables_peak_detection(self):
        f = build_libplacebo_filter(2.2, 'reinhard')
        self.assertIn('libplacebo=', f)
        self.assertIn('tonemapping=reinhard', f)
        self.assertIn('peak_detect=1', f)
        self.assertIn('eq=gamma=2.2', f)
        # Default keeps source resolution (no resize on a full conversion).
        self.assertIn('w=iw:h=ih', f)

    def test_tonemapper_is_lowercased(self):
        self.assertIn('tonemapping=hable', build_libplacebo_filter(1.0, 'Hable'))

    def test_explicit_size_passed_to_libplacebo(self):
        f = build_libplacebo_filter(1.0, 'reinhard', width=960, height=540)
        self.assertIn('w=960:h=540', f)

    def test_cpu_input_uses_format_p010_hwupload(self):
        """Default (CPU decode) path: filter starts with format=p010,hwupload."""
        f = build_libplacebo_filter(1.0, 'reinhard')
        self.assertTrue(f.startswith('format=p010,hwupload,'), f)

    def test_cuda_input_uses_hwmap_derive_device(self):
        """CUDA interop path: filter starts with hwmap=derive_device=vulkan, not hwupload."""
        f = build_libplacebo_filter(1.0, 'reinhard', cuda_input=True)
        self.assertTrue(f.startswith('hwmap=derive_device=vulkan,'), f)
        self.assertNotIn('format=p010,hwupload', f)

    def test_cuda_input_gamma_1_stays_fully_on_gpu(self):
        """gamma=1.0 + CUDA interop: remap Vulkan→CUDA after libplacebo, no CPU round-trip."""
        f = build_libplacebo_filter(1.0, 'reinhard', cuda_input=True)
        self.assertIn('hwmap=reverse=1:derive_device=cuda', f)
        self.assertNotIn('hwdownload', f)

    def test_cuda_input_gamma_not_1_still_downloads_for_eq(self):
        """gamma≠1.0 + CUDA interop: must download to CPU for the eq filter."""
        f = build_libplacebo_filter(2.2, 'reinhard', cuda_input=True)
        self.assertIn('hwdownload,format=nv12,eq=gamma=2.2', f)
        self.assertNotIn('hwmap=reverse=1:derive_device=cuda', f)

    def test_cpu_input_gamma_1_downloads_without_eq(self):
        """gamma=1.0 on plain Vulkan path: download is still needed for NVENC, but skip the no-op eq."""
        f = build_libplacebo_filter(1.0, 'reinhard', cuda_input=False)
        self.assertIn('hwdownload,format=nv12', f)
        self.assertNotIn('eq=gamma=1.0', f)
        self.assertNotIn('eq=gamma=1', f)


class TestVulkanCudaInteropProbe(unittest.TestCase):
    """The cached CUDA→Vulkan interop capability probe."""

    def setUp(self):
        reset_cuda_interop_probe()
        self.addCleanup(reset_cuda_interop_probe)

    @patch('src.utils.FFMPEG_EXECUTABLE', None)
    def test_false_without_ffmpeg(self):
        self.assertFalse(vulkan_cuda_interop_available())

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run')
    def test_true_when_probe_succeeds(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(vulkan_cuda_interop_available())
        mock_run.assert_called_once()

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run')
    def test_false_when_probe_fails(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(vulkan_cuda_interop_available())

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run', side_effect=OSError('no cuda'))
    def test_false_when_probe_raises(self, _run):
        self.assertFalse(vulkan_cuda_interop_available())

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')
    @patch('src.utils.subprocess.run')
    def test_result_is_cached(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        vulkan_cuda_interop_available()
        vulkan_cuda_interop_available()
        mock_run.assert_called_once()

    def test_device_args_contains_cuda_and_vulkan_interop(self):
        """VULKAN_CUDA_DEVICE_ARGS must set up both CUDA and linked Vulkan device."""
        joined = ' '.join(VULKAN_CUDA_DEVICE_ARGS)
        self.assertIn('cuda=cu:0', joined)
        self.assertIn('vulkan=vk@cu', joined)
        self.assertIn('-hwaccel', VULKAN_CUDA_DEVICE_ARGS)
        self.assertIn('-hwaccel_output_format', VULKAN_CUDA_DEVICE_ARGS)


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
    """_get_hdr_metadata must call _probe_hdr_metadata exactly once under concurrent cache misses.

    Without a threading.Lock, two threads that both see the cache miss can both
    call _probe_hdr_metadata before either one writes the result back — spawning two
    ffprobe processes for the same file.
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

        def slow_probe(path: str) -> dict:
            import time
            time.sleep(0.05)
            call_count.append(1)
            return {'maxcll': 400.0, 'maxfall': None, 'mastering_peak': None}

        results: list = []

        def worker() -> None:
            start.wait()
            results.append(get_maxcll('/fake/concurrent/video.mkv'))

        with patch('src.utils._probe_hdr_metadata', side_effect=slow_probe):
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
            f"_probe_hdr_metadata was called {len(call_count)} time(s); expected exactly 1 "
            "(a threading.Lock must prevent duplicate ffprobe launches)",
        )


# ---------------------------------------------------------------------------
# Issue #3 — Portability: verify_ffmpeg_files must use platform-agnostic keys
# ---------------------------------------------------------------------------

class TestVerifyFfmpegFilesPortability(unittest.TestCase):
    """verify_ffmpeg_files must use extension-free keys ('ffmpeg', not 'ffmpeg.exe').

    Regression guards ensuring the implementation uses platform-agnostic names as
    dict keys and as the lookup arguments passed to get_executable_path.
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
    """Regression guards ensuring every subprocess call hides the console on Windows.

    All helpers that shell out (run_ffmpeg_command, get_video_properties,
    vulkan_libplacebo_available, _probe_hdr_metadata) must pass a STARTUPINFO with
    SW_HIDE so no console window flashes during conversion or probing.
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
    def test_probe_hdr_metadata_hides_console_on_windows(self, mock_check: MagicMock) -> None:
        """_probe_hdr_metadata must pass a STARTUPINFO on Windows (hide console)."""
        import src.utils as _u
        try:
            _u._probe_hdr_metadata('/fake/video.mkv')
        except Exception:
            pass  # JSON parse errors are irrelevant to this test

        kwargs = mock_check.call_args[1]
        if sys.platform == 'win32':
            self.assertIsNotNone(
                kwargs.get('startupinfo'),
                "Windows: startupinfo must be set in _probe_hdr_metadata",
            )
        else:
            self.assertIsNone(
                kwargs.get('startupinfo'),
                "Non-Windows: startupinfo must be None in _probe_hdr_metadata",
            )


class TestSetupDpiAwareness(unittest.TestCase):
    """setup_dpi_awareness() should enable Per-Monitor DPI awareness on Windows."""

    @patch('sys.platform', 'win32')
    def test_calls_set_process_dpi_awareness_on_windows(self):
        mock_shcore = MagicMock()
        with patch.dict('sys.modules', {'ctypes': MagicMock(windll=MagicMock(shcore=mock_shcore))}):
            import importlib
            import src.utils as _u
            importlib.reload(_u)
            _u.setup_dpi_awareness()
        mock_shcore.SetProcessDpiAwareness.assert_called_once_with(1)

    @patch('sys.platform', 'darwin')
    def test_no_op_on_non_windows(self):
        mock_shcore = MagicMock()
        with patch.dict('sys.modules', {'ctypes': MagicMock(windll=MagicMock(shcore=mock_shcore))}):
            import importlib
            import src.utils as _u
            importlib.reload(_u)
            _u.setup_dpi_awareness()
        mock_shcore.SetProcessDpiAwareness.assert_not_called()

    @patch('sys.platform', 'win32')
    def test_swallows_exceptions(self):
        mock_shcore = MagicMock()
        mock_shcore.SetProcessDpiAwareness.side_effect = OSError("unavailable")
        with patch.dict('sys.modules', {'ctypes': MagicMock(windll=MagicMock(shcore=mock_shcore))}):
            import importlib
            import src.utils as _u
            importlib.reload(_u)
            _u.setup_dpi_awareness()  # must not raise


class TestGetVideoPropertiesRobustness(unittest.TestCase):
    """get_video_properties must handle malformed / unusual ffprobe output gracefully."""

    @patch('src.utils.subprocess.Popen')
    def test_returns_none_when_format_key_missing(self, mock_popen):
        """ffprobe output without a 'format' key must return None, not raise KeyError."""
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (json.dumps({
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "codec_name": "hevc", "avg_frame_rate": "24/1",
                         "bit_rate": "5000000"}],
            # 'format' key intentionally absent
        }).encode(), b'')
        self.assertIsNone(get_video_properties('exotic.mkv'))

    @patch('src.utils.subprocess.Popen')
    def test_handles_na_bit_rate_without_crashing(self, mock_popen):
        """bit_rate='N/A' (returned by ffprobe for some containers) must yield
        bit_rate=0 in the result dict, not crash with ValueError."""
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (json.dumps({
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "codec_name": "hevc", "avg_frame_rate": "24/1",
                         "bit_rate": "N/A"},
                        {"codec_type": "audio", "codec_name": "aac",
                         "bit_rate": "N/A"}],
            "format": {"duration": "120.0"},
        }).encode(), b'')
        props = get_video_properties('video.mkv')
        self.assertIsNotNone(props, "should return valid props, not None")
        self.assertEqual(props['bit_rate'], 0)
        self.assertEqual(props['audio_bit_rate'], 0)


class TestVideoPropertiesCache(unittest.TestCase):
    """get_video_properties must cache results and only probe once per path."""

    _VALID_PROPS_JSON = json.dumps({
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "codec_name": "hevc", "avg_frame_rate": "24/1", "bit_rate": "5000000"}],
        "format": {"duration": "60.0"},
    }).encode()

    def setUp(self):
        import src.utils as _u
        _u._VIDEO_PROPS_CACHE.clear()
        self.addCleanup(_u._VIDEO_PROPS_CACHE.clear)

    def _mock_popen(self, mock_popen):
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (self._VALID_PROPS_JSON, b'')
        return proc

    @patch('src.utils.subprocess.Popen')
    def test_second_call_uses_cache_not_popen(self, mock_popen):
        """Repeated calls for the same path must not spawn a second ffprobe."""
        self._mock_popen(mock_popen)
        first = get_video_properties('cache_test.mkv')
        second = get_video_properties('cache_test.mkv')
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(first, second)

    @patch('src.utils.subprocess.Popen')
    def test_clear_video_properties_cache_forces_reprobe(self, mock_popen):
        """After clear_video_properties_cache(), the next call must spawn a fresh ffprobe."""
        self._mock_popen(mock_popen)
        get_video_properties('cache_clear_test.mkv')
        clear_video_properties_cache()
        get_video_properties('cache_clear_test.mkv')
        self.assertEqual(mock_popen.call_count, 2)

    @patch('src.utils.subprocess.Popen')
    def test_clear_maxfall_cache_also_clears_video_props(self, mock_popen):
        """clear_maxfall_cache() must evict video properties so both caches stay in sync."""
        self._mock_popen(mock_popen)
        get_video_properties('sync_clear_test.mkv')
        clear_maxfall_cache()
        get_video_properties('sync_clear_test.mkv')
        self.assertEqual(mock_popen.call_count, 2)

    @patch('src.utils.subprocess.Popen')
    def test_none_result_not_cached(self, mock_popen):
        """A None result (bad output) must not be cached; next call must reprobe."""
        proc = mock_popen.return_value
        proc.returncode = 1  # ffprobe failure
        proc.communicate.return_value = (b'', b'error')
        get_video_properties('bad_file.mkv')
        get_video_properties('bad_file.mkv')
        self.assertEqual(mock_popen.call_count, 2)


def _minimal_png() -> bytes:
    """Return a valid 1×1 RGB PNG as bytes (for batch-function tests)."""
    import struct, zlib
    def _chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xffffffff
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    idat = _chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff'))
    iend = _chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


class TestSplitPngFrames(unittest.TestCase):
    """_split_png_frames parses a concatenated PNG stream into PIL Image objects."""

    def test_empty_data_returns_empty_list(self):
        self.assertEqual(_split_png_frames(b''), [])

    def test_single_png_returns_one_image(self):
        frames = _split_png_frames(_minimal_png())
        self.assertEqual(len(frames), 1)

    def test_three_concatenated_pngs_return_three_images(self):
        frames = _split_png_frames(_minimal_png() * 3)
        self.assertEqual(len(frames), 3)

    def test_leading_junk_is_skipped(self):
        frames = _split_png_frames(b'\x00junk' + _minimal_png())
        self.assertEqual(len(frames), 1)


class TestExtractFramesBatch(unittest.TestCase):
    """extract_frames_batch must extract N frames in exactly 1 ffmpeg process."""

    def _popen_ok(self, mock_popen, n: int):
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (_minimal_png() * n, b'')

    @patch('src.utils.subprocess.Popen')
    def test_three_positions_spawn_one_process(self, mock_popen):
        """Three timestamps → exactly 1 Popen call, returns 3 images."""
        self._popen_ok(mock_popen, 3)
        result = extract_frames_batch('vid.mkv', [10.0, 20.0, 30.0], 960, 540)
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(len(result), 3)

    @patch('src.utils.subprocess.Popen')
    def test_empty_positions_returns_empty_without_popen(self, mock_popen):
        result = extract_frames_batch('vid.mkv', [], 960, 540)
        self.assertEqual(result, [])
        mock_popen.assert_not_called()

    @patch('src.utils.subprocess.Popen')
    def test_single_position_works(self, mock_popen):
        self._popen_ok(mock_popen, 1)
        result = extract_frames_batch('vid.mkv', [5.0], 960, 540)
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(len(result), 1)

    @patch('src.utils.subprocess.Popen')
    def test_ffmpeg_error_raises_runtime_error(self, mock_popen):
        proc = mock_popen.return_value
        proc.returncode = 1
        proc.communicate.return_value = (b'', b'some ffmpeg error')
        with self.assertRaises(RuntimeError):
            extract_frames_batch('vid.mkv', [10.0], 960, 540)


class TestExtractFramesWithConversionBatch(unittest.TestCase):
    """extract_frames_with_conversion_batch must tonemap N frames in 1 ffmpeg process."""

    def _popen_ok(self, mock_popen, n: int):
        proc = mock_popen.return_value
        proc.returncode = 0
        proc.communicate.return_value = (_minimal_png() * n, b'')

    @patch('src.utils.subprocess.Popen')
    def test_two_positions_spawn_one_process(self, mock_popen):
        self._popen_ok(mock_popen, 2)
        result = extract_frames_with_conversion_batch('vid.mkv', [5.0, 15.0], 1.0, 'reinhard', 960, 540)
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(len(result), 2)

    @patch('src.utils.subprocess.Popen')
    def test_empty_positions_returns_empty_without_popen(self, mock_popen):
        result = extract_frames_with_conversion_batch('vid.mkv', [], 1.0, 'reinhard', 960, 540)
        self.assertEqual(result, [])
        mock_popen.assert_not_called()

    @patch('src.utils.subprocess.Popen')
    def test_ffmpeg_error_raises_runtime_error(self, mock_popen):
        proc = mock_popen.return_value
        proc.returncode = 1
        proc.communicate.return_value = (b'', b'tonemap failed')
        with self.assertRaises(RuntimeError):
            extract_frames_with_conversion_batch('vid.mkv', [5.0], 1.0, 'reinhard', 960, 540)

    @patch('src.utils.subprocess.Popen')
    def test_tonemapper_name_is_lowercased_in_filter(self, mock_popen):
        self._popen_ok(mock_popen, 1)
        extract_frames_with_conversion_batch('vid.mkv', [5.0], 1.0, 'Reinhard', 960, 540)
        cmd = mock_popen.call_args[0][0]
        filter_arg = ' '.join(cmd)
        self.assertIn('reinhard', filter_arg)
        self.assertNotIn('Reinhard', filter_arg)


class TestProbeHdrMetadata(unittest.TestCase):
    """_probe_hdr_metadata returns MaxCLL, MAXFALL, and mastering peak from the first frame."""

    def setUp(self):
        import src.utils as _u
        self._u = _u
        _u._MAXFALL_CACHE.clear()
        self.addCleanup(_u._MAXFALL_CACHE.clear)

    def _frame_data(self, maxcll=1000, maxfall=400, mastering_peak='10000000/10000'):
        side_data = []
        if maxcll is not None:
            side_data.append({
                'side_data_type': 'Content light level metadata',
                'max_content': maxcll,
                'max_average': maxfall,
            })
        if mastering_peak is not None:
            side_data.append({
                'side_data_type': 'Mastering display metadata',
                'min_luminance': '0/10000',
                'max_luminance': mastering_peak,
            })
        return json.dumps({'frames': [{'side_data_list': side_data}]}).encode()

    @patch('src.utils.subprocess.check_output')
    def test_reads_maxcll_from_content_light_level(self, mock_out):
        mock_out.return_value = self._frame_data()
        result = self._u._probe_hdr_metadata('/fake/hdr.mkv')
        self.assertEqual(result['maxcll'], 1000.0)

    @patch('src.utils.subprocess.check_output')
    def test_reads_maxfall_from_content_light_level(self, mock_out):
        """max_average from Content light level metadata must be stored as maxfall."""
        mock_out.return_value = self._frame_data(maxfall=400)
        result = self._u._probe_hdr_metadata('/fake/hdr.mkv')
        self.assertEqual(result['maxfall'], 400.0)

    @patch('src.utils.subprocess.check_output')
    def test_reads_mastering_peak_as_fraction(self, mock_out):
        """max_luminance '40000000/10000' must be parsed to 4000.0 nits."""
        mock_out.return_value = self._frame_data(mastering_peak='40000000/10000')
        result = self._u._probe_hdr_metadata('/fake/hdr.mkv')
        self.assertAlmostEqual(result['mastering_peak'], 4000.0)

    @patch('src.utils.subprocess.check_output')
    def test_returns_none_values_when_no_metadata(self, mock_out):
        mock_out.return_value = json.dumps({'frames': [{'side_data_list': []}]}).encode()
        result = self._u._probe_hdr_metadata('/fake/sdr.mkv')
        self.assertIsNone(result['maxcll'])
        self.assertIsNone(result['maxfall'])
        self.assertIsNone(result['mastering_peak'])


class TestDynamicOnlyFilter(unittest.TestCase):
    """After removing Static, there is one filter chain: Dynamic with npl=100."""

    def test_ffmpeg_filter_is_string_not_list(self):
        from src.utils import FFMPEG_FILTER
        self.assertIsInstance(FFMPEG_FILTER, str, "FFMPEG_FILTER must be a single string, not a list")

    def test_ffmpeg_filter_has_npl_100(self):
        from src.utils import FFMPEG_FILTER
        self.assertIn('npl=100', FFMPEG_FILTER)

    def test_ffmpeg_convert_filter_is_string_not_list(self):
        from src.utils import FFMPEG_CONVERT_FILTER
        self.assertIsInstance(FFMPEG_CONVERT_FILTER, str)

    def test_ffmpeg_convert_filter_has_npl_100(self):
        from src.utils import FFMPEG_CONVERT_FILTER
        self.assertIn('npl=100', FFMPEG_CONVERT_FILTER)

    @patch('src.utils.get_video_properties', return_value={'duration': 90.0})
    @patch('src.utils.run_ffmpeg_command', return_value=_VALID_PNG)
    def test_extract_frame_with_conversion_no_filter_index(self, mock_run, _props):
        """extract_frame_with_conversion accepts no filter_index and uses npl=100."""
        extract_frame_with_conversion('in.mp4', gamma=1.0, tonemapper='reinhard')
        vf = mock_run.call_args[0][0][mock_run.call_args[0][0].index('-vf') + 1]
        self.assertIn('npl=100', vf)

    def test_build_libplacebo_filter_no_filter_index_always_peak_detect_1(self):
        """build_libplacebo_filter takes no filter_index and always enables peak_detect."""
        from src.utils import build_libplacebo_filter
        result = build_libplacebo_filter(gamma=1.0, tonemapper='reinhard')
        self.assertIn('peak_detect=1', result)


class TestDolbyVisionDetection(unittest.TestCase):
    """get_video_properties flags Dolby Vision inputs from ffprobe's stream
    side_data_list (the 'DOVI configuration record' entry), exposing
    is_dolby_vision / dovi_profile for the conversion tier split and the UI
    badge."""

    def setUp(self):
        clear_video_properties_cache()
        self.addCleanup(clear_video_properties_cache)

    @staticmethod
    def _probe_json(side_data=None):
        video = {
            "codec_type": "video", "width": 3840, "height": 2160,
            "bit_rate": "20000000", "codec_name": "hevc",
            "avg_frame_rate": "24/1", "pix_fmt": "yuv420p10le",
            "color_primaries": "bt2020", "color_transfer": "smpte2084",
        }
        if side_data is not None:
            video["side_data_list"] = side_data
        return json.dumps({
            "streams": [
                video,
                {"codec_type": "audio", "codec_name": "truehd",
                 "bit_rate": "3000000"},
            ],
            "format": {"duration": "600.0"},
        }).encode('utf-8')

    def _props_for(self, mock_popen, side_data, name):
        mock_process = mock_popen.return_value
        mock_process.communicate.return_value = (self._probe_json(side_data), b'')
        mock_process.returncode = 0
        return get_video_properties(name)

    _DOVI_P8_RECORD = {
        "side_data_type": "DOVI configuration record",
        "dv_version_major": 1, "dv_version_minor": 0,
        "dv_profile": 8, "dv_level": 6,
        "rpu_present_flag": 1, "el_present_flag": 0, "bl_present_flag": 1,
        "dv_bl_signal_compatibility_id": 1,
    }

    @patch('src.utils.subprocess.Popen')
    def test_dovi_configuration_record_sets_flag_and_profile(self, mock_popen):
        props = self._props_for(mock_popen, [self._DOVI_P8_RECORD], 'dovi_p8.mkv')
        self.assertTrue(props['is_dolby_vision'])
        self.assertEqual(props['dovi_profile'], 8)

    @patch('src.utils.subprocess.Popen')
    def test_dovi_profile_5_detected(self, mock_popen):
        record = dict(self._DOVI_P8_RECORD,
                      dv_profile=5, dv_bl_signal_compatibility_id=0)
        props = self._props_for(mock_popen, [record], 'dovi_p5.mp4')
        self.assertTrue(props['is_dolby_vision'])
        self.assertEqual(props['dovi_profile'], 5)

    @patch('src.utils.subprocess.Popen')
    def test_plain_hdr10_stream_is_not_flagged(self, mock_popen):
        props = self._props_for(mock_popen, None, 'plain_hdr10.mkv')
        self.assertFalse(props['is_dolby_vision'])
        self.assertIsNone(props['dovi_profile'])

    @patch('src.utils.subprocess.Popen')
    def test_unrelated_side_data_is_not_flagged(self, mock_popen):
        props = self._props_for(mock_popen, [
            {"side_data_type": "Display Matrix", "rotation": 0},
            {"side_data_type": "Content light level metadata",
             "max_content": 1000, "max_average": 400},
        ], 'rotated_hdr10.mkv')
        self.assertFalse(props['is_dolby_vision'])
        self.assertIsNone(props['dovi_profile'])

    @patch('src.utils.subprocess.Popen')
    def test_dovi_record_with_missing_profile_still_flags_dovi(self, mock_popen):
        record = {k: v for k, v in self._DOVI_P8_RECORD.items()
                  if k != 'dv_profile'}
        props = self._props_for(mock_popen, [record], 'dovi_no_profile.mkv')
        self.assertTrue(props['is_dolby_vision'])
        self.assertIsNone(props['dovi_profile'])

    @patch('src.utils.subprocess.Popen')
    def test_dovi_record_with_string_profile_is_parsed(self, mock_popen):
        """ffprobe emits numbers, but a string value must not crash detection."""
        record = dict(self._DOVI_P8_RECORD, dv_profile="8")
        props = self._props_for(mock_popen, [record], 'dovi_str_profile.mkv')
        self.assertTrue(props['is_dolby_vision'])
        self.assertEqual(props['dovi_profile'], 8)


if __name__ == '__main__':
    unittest.main()