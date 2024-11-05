import unittest
from unittest.mock import patch, MagicMock
from src.utils import get_video_properties, run_ffmpeg_command, extract_frame, extract_frame_with_conversion
import subprocess  
from PIL import Image  # Added import

class TestGetVideoProperties(unittest.TestCase):

    @patch('src.utils.subprocess.Popen')
    def test_get_video_properties(self, mock_popen):
        # Mock subprocess.Popen to return a predefined JSON output as bytes
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
            ]
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
            "duration": 600.0
        }

        properties = get_video_properties(input_file)
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

    @patch('subprocess.Popen')
    def test_extract_frame_success(self, mock_popen):
        mock_process = MagicMock()
        # Provide valid PNG image bytes
        mock_process.communicate.return_value = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
            b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
            b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82',
            b''
        )
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = extract_frame('input.mp4')  # Changed from list to string
        self.assertIsInstance(result, Image.Image)

    @patch('subprocess.Popen')
    def test_extract_frame_failure(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'', b'error')
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        with self.assertRaises(RuntimeError):
            extract_frame('input.mp4')  # Changed from list to string

class TestExtractFrameWithConversion(unittest.TestCase):

    @patch('subprocess.Popen')
    def test_extract_frame_with_conversion_success(self, mock_popen):
        mock_process = MagicMock()
        # Provide valid PNG image bytes for conversion
        mock_process.communicate.return_value = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
            b'\x00\x00\nIDATx\xdac\xf8\x0f\x00\x01\x01\x01\x00'
            b'\x18\xdd\x8d\x1b\x00\x00\x00\x00IEND\xaeB`\x82',
            b''
        )
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = extract_frame_with_conversion('input.mp4', 1)  # Changed from list to string and added gamma
        self.assertIsInstance(result, Image.Image)

    @patch('subprocess.Popen')
    def test_extract_frame_with_conversion_failure(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'', b'conversion_error')
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        with self.assertRaises(RuntimeError):
            extract_frame_with_conversion('input.mp4', 1)  # Changed from list to string and added gamma

if __name__ == '__main__':
    unittest.main()