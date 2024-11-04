import unittest
from unittest.mock import patch, MagicMock
from utils import extract_frame_with_conversion
from PIL import Image

class TestExtractFrameWithConversion(unittest.TestCase):
    @patch('utils.run_ffmpeg_command')
    def test_extract_frame_with_conversion_success(self, mock_run_ffmpeg):
        # Mock ffmpeg output
        mock_run_ffmpeg.return_value = b'test image data'
        with patch('utils.Image.open', return_value=MagicMock(spec=Image.Image)) as mock_image_open:
            image = extract_frame_with_conversion('dummy_path.mp4', 2.2)
            mock_run_ffmpeg.assert_called_once()
            mock_image_open.assert_called_once()
            self.assertIsNotNone(image)
    
    @patch('utils.run_ffmpeg_command')
    def test_extract_frame_with_conversion_ffmpeg_error(self, mock_run_ffmpeg):
        # Simulate ffmpeg error
        mock_run_ffmpeg.side_effect = RuntimeError('ffmpeg error')
        with self.assertRaises(RuntimeError):
            extract_frame_with_conversion('dummy_path.mp4', 2.2)
