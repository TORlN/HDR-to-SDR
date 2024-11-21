import sys
import os
import subprocess  # Added import
import multiprocessing  # Added import
import ctypes  # Added import for SW_HIDE
import threading  # Added import for threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
import unittest
from unittest.mock import patch, MagicMock, ANY  # Import ANY
from src.conversion import ConversionManager
from src.utils import get_video_properties
from tkinter import Tk, DoubleVar  # Added DoubleVar import
from tkinter import ttk
from PIL import Image
from src.utils import FFMPEG_FILTER
from src.utils import FFMPEG_EXECUTABLE  # Import FFMPEG_EXECUTABLE

def run_ffmpeg_command(command):
    """Run an FFmpeg command and return output. Raises RuntimeError if command fails."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = process.communicate()
    
    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg command failed with error: {error.decode()}")
    
    return output.decode()

class TestConversionManager(unittest.TestCase):

    @patch('src.conversion.get_video_properties')
    @patch('src.conversion.subprocess.Popen')
    def test_start_conversion_success(self, mock_popen, mock_get_props):
        mock_get_props.return_value = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0,
            "subtitle_streams": []  # Ensure this key is present
        }

        mock_process = MagicMock()
        mock_process.stderr = iter([
            'time=00:00:01.00',
            'time=00:00:02.00'
        ])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        # Create a mocked GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = Tk()  # Ensure root is a Tk instance
        mock_gui.root.after = MagicMock()

        progress_var = DoubleVar(master=mock_gui.root)  # Ensure DoubleVar is created with the root window
        interactable_elements = []
        cancel_button = MagicMock()

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            False,  # Added use_gpu argument
            progress_var,
            interactable_elements,
            mock_gui,  # Pass the mocked GUI instance
            False,
            cancel_button
        )

        self.assertIsNotNone(manager.process)
        mock_popen.assert_called_once()
        mock_get_props.assert_called_once_with(os.path.abspath('input.mp4'))

    @patch('src.conversion.messagebox.showinfo')  # Mock the showinfo popup
    @patch('src.conversion.subprocess.Popen')
    def test_cancel_conversion(self, mock_popen, mock_showinfo):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = Tk()  # Ensure root is a Tk instance
        mock_gui.root.after = MagicMock()
        mock_gui.root.destroy = MagicMock()

        interactable_elements = []
        cancel_button = MagicMock()

        manager = ConversionManager()
        manager.process = mock_process
        manager.cancel_conversion(mock_gui, interactable_elements, cancel_button)

        # Execute the scheduled callbacks
        for call in mock_gui.root.after.call_args_list:
            call[0][1]()  # Execute the callback function

        mock_process.terminate.assert_called_once()
        self.assertTrue(manager.cancelled)
        mock_showinfo.assert_called_once_with("Cancelled", "Video conversion has been cancelled.")

    @patch('src.conversion.get_video_properties')
    @patch('src.conversion.subprocess.Popen')
    def test_monitor_progress_failure(self, mock_popen, mock_get_props):
        mock_get_props.return_value = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0,
            "subtitle_streams": []  # Ensure this key is present
        }

        mock_process = MagicMock()
        mock_process.stderr = iter(['error message'])
        mock_process.wait.return_value = 1
        mock_popen.return_value = mock_process

        # Create a mocked GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = Tk()  # Ensure root is a Tk instance
        mock_gui.root.after = MagicMock()

        progress_var = DoubleVar(master=mock_gui.root)  # Ensure DoubleVar is created with the root window
        interactable_elements = []
        cancel_button = MagicMock()

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            False,  # Added use_gpu argument
            progress_var,
            interactable_elements,
            mock_gui,  # Pass the mocked GUI instance
            False,
            cancel_button
        )

        # Additional assertions can be added here as needed

    @patch('src.conversion.messagebox.showwarning')
    @patch('src.conversion.get_video_properties')
    def test_start_conversion_invalid_paths(self, mock_get_props, mock_showwarning):  # Swapped argument order
        """Test start_conversion with invalid input or output paths."""
        manager = ConversionManager()
        mock_gui = MagicMock()
        mock_gui.root = Tk()
        mock_gui.root.after = MagicMock()
        progress_var = DoubleVar(master=mock_gui.root)
        interactable_elements = []
        cancel_button = MagicMock()

        manager.start_conversion('', 'output.mkv', 2.2, False, progress_var, interactable_elements, mock_gui, False, cancel_button)
        mock_showwarning.assert_called_once_with(
            "Warning", "Please select both an input file and specify an output file."
        )

        mock_showwarning.reset_mock()
        manager.start_conversion('input.mp4', '', 2.2, False, progress_var, interactable_elements, mock_gui, False, cancel_button)
        self.assertEqual(mock_showwarning.call_count, 1)
        mock_showwarning.assert_called_with(
            "Warning", "Please select both an input file and specify an output file."
        )
        
        # Verify get_video_properties was never called at the end
        mock_get_props.assert_not_called()

    @patch('src.utils.FFMPEG_EXECUTABLE', 'ffmpeg')  # Patch FFMPEG_EXECUTABLE to be a string
    @patch('src.conversion.messagebox.showwarning')  # Mock the showwarning popup
    @patch('src.conversion.get_video_properties')
    def test_start_conversion_no_properties(self, mock_get_props, mock_showwarning):
        """Test start_conversion when get_video_properties returns None."""
        mock_get_props.return_value = None
        manager = ConversionManager()
        mock_gui = MagicMock()
        mock_gui.root = Tk()  # Ensure root is a Tk instance
        mock_gui.root.after = MagicMock()
        progress_var = DoubleVar(master=mock_gui.root)  # Ensure DoubleVar is created with the root window
        interactable_elements = []
        cancel_button = MagicMock()

        input_path = 'input.mp4'
        manager.start_conversion(input_path, 'output.mkv', 2.2, False, progress_var, 
                               interactable_elements, mock_gui, False, cancel_button)
        mock_showwarning.assert_called_once_with(
            "Warning", "Failed to retrieve video properties."
        )
        self.assertIsNone(manager.process)
        mock_get_props.assert_called_once_with(os.path.abspath(input_path))

    @patch('src.conversion.subprocess.Popen')
    def test_construct_ffmpeg_command(self, mock_popen):
        """Test if ffmpeg command is constructed correctly."""
        manager = ConversionManager()
        properties = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0
        }
        gamma = 2.2
        input_path = 'input.mp4'
        output_path = 'output.mkv'
        expected_filter = FFMPEG_FILTER.format(
            gamma=gamma, width=properties["width"], height=properties["height"]
        )
        cmd = manager.construct_ffmpeg_command(input_path, output_path, gamma, properties, False)  # use_gpu=False
        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-i', input_path,
            # Apply the HDR to SDR filter to the main video stream
            '-filter:v', expected_filter,
            # Re-encode the main video stream with the same settings
            '-c:v', properties['codec_name'],
            '-b:v', str(properties['bit_rate']),
            '-r', str(properties['frame_rate']),
            '-preset', 'fast',  # Changed from 'faster' to 'fast'
            '-pix_fmt', 'yuv420p',  # Added pix_fmt and yuv420p
            '-strict', '-2',
            # Copy audio and subtitle streams without re-encoding
            '-c:a', 'copy',
            '-c:s', 'copy',
            output_path,
            '-y'
        ]
        self.assertEqual(cmd, expected_cmd)

    @patch('src.conversion.get_video_properties')
    def test_construct_ffmpeg_command_with_subtitles(self, mock_get_props):
        """Test that construct_ffmpeg_command includes subtitle streams when available."""
        mock_get_props.return_value = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0,
            "subtitle_streams": [
                {"codec_type": "subtitle", "codec_name": "srt", "index": 2}
            ]
        }

        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'input.mp4',
            'output.mkv',
            2.2,
            mock_get_props.return_value,
            False  # Added use_gpu argument
        )

        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-i', os.path.normpath('input.mp4'),
            # Apply the HDR to SDR filter to the main video stream
            '-filter:v', FFMPEG_FILTER.format(
                gamma=2.2, width=1920, height=1080
            ),
            # Re-encode the main video stream with the same settings
            '-c:v', 'h264',
            '-b:v', '4000000',
            '-r', '30.0',
            '-preset', 'fast',  # Changed from 'faster' to 'fast'
            '-pix_fmt', 'yuv420p',  # Added pix_fmt and yuv420p
            '-strict', '-2',
            # Copy audio and subtitle streams without re-encoding
            '-c:a', 'copy',
            '-c:s', 'copy',
            os.path.normpath('output.mkv'),
            '-y'
        ]

        self.assertEqual(cmd, expected_cmd)

    @patch('src.conversion.messagebox.showinfo')  # Mock the showinfo popup
    @patch('src.conversion.webbrowser.open')
    def test_handle_completion_success(self, mock_webbrowser_open, mock_showinfo):
        """Test handle_completion for a successful conversion."""
        manager = ConversionManager()
        manager.process = MagicMock()
        manager.process.returncode = 0
        error_messages = []

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = Tk()  # Ensure root is a Tk instance
        mock_gui.root.after = MagicMock(side_effect=lambda delay, func: func())

        cancel_button = MagicMock()
        cancel_button.grid_remove = MagicMock()

        with patch.object(manager, 'enable_ui') as mock_enable_ui:
            manager.handle_completion(
                mock_gui, [], cancel_button, 'output.mkv', True, error_messages
            )

            mock_showinfo.assert_called_once_with(
                "Success", "Conversion complete! Output saved to: output.mkv"
            )
            mock_webbrowser_open.assert_called_once_with('output.mkv')
            mock_enable_ui.assert_called_once_with([])
            cancel_button.grid_remove.assert_called_once()

    @patch('src.conversion.messagebox.showerror')  # Mock the showerror popup
    def test_handle_completion_failure(self, mock_showerror):
        """Test handle_completion for a failed conversion."""
        manager = ConversionManager()
        manager.process = MagicMock()
        manager.process.returncode = 1
        error_messages = ['error message']

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = Tk()  # Ensure root is a Tk instance
        mock_gui.root.after = MagicMock(side_effect=lambda delay, func: func())

        cancel_button = MagicMock()
        cancel_button.grid_remove = MagicMock()

        manager.cancelled = False  # Ensure it's not cancelled

        with patch.object(manager, 'enable_ui') as mock_enable_ui:
            manager.handle_completion(
                mock_gui, [], cancel_button, 'output.mkv', False, error_messages
            )

            mock_showerror.assert_called_once_with(
                "Error", f"Conversion failed with code 1\nerror message"
            )
            mock_enable_ui.assert_called_once_with([])
            cancel_button.grid_remove.assert_called_once()

    @patch('src.conversion.messagebox.showwarning')
    def test_verify_paths(self, mock_showwarning):
        """Test verify_paths method with various inputs."""
        manager = ConversionManager()
        self.assertFalse(manager.verify_paths('', 'output.mkv'))
        mock_showwarning.assert_called_once_with(
            "Warning", "Please select both an input file and specify an output file."
        )

        mock_showwarning.reset_mock()
        self.assertFalse(manager.verify_paths('input.mp4', ''))
        mock_showwarning.assert_called_once_with(
            "Warning", "Please select both an input file and specify an output file."
        )

        mock_showwarning.reset_mock()
        self.assertTrue(manager.verify_paths('input.mp4', 'output.mkv'))
        mock_showwarning.assert_not_called()

    def test_parse_time(self):
        """Test parse_time method."""
        manager = ConversionManager()
        self.assertEqual(manager.parse_time('01:30:15.50'), 5415.5)
        self.assertEqual(manager.parse_time('00:00:00.00'), 0.0)
        self.assertEqual(manager.parse_time('10:20:30.40'), 37230.4)

    @patch('src.conversion.messagebox.showwarning')
    def test_disable_enable_ui(self, mock_showwarning):
        """Test disable_ui and enable_ui methods."""
        elements = [MagicMock(), MagicMock()]
        manager = ConversionManager()

        manager.disable_ui(elements)
        for element in elements:
            element.config.assert_called_with(state="disabled")

        manager.enable_ui(elements)
        for element in elements:
            element.config.assert_called_with(state="normal")

    @patch('src.conversion.subprocess.Popen')
    def test_start_ffmpeg_process_non_windows(self, mock_popen):
        """Test start_ffmpeg_process on non-Windows platforms."""
        if sys.platform == 'win32':
            return True

        with patch('sys.platform', 'linux'):
            manager = ConversionManager()
            cmd = ['ffmpeg', '-i', 'input.mp4', 'output.mkv']
            mock_process = MagicMock()
            mock_popen.return_value = mock_process

            process = manager.start_ffmpeg_process(cmd)
            mock_popen.assert_called_once_with(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                universal_newlines=True,
                startupinfo=None
            )
            self.assertEqual(process, mock_process)

    @patch('ctypes.windll', create=True)  # Correctly mock ctypes.windll
    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.subprocess')  # Mock the subprocess module
    def test_start_ffmpeg_process_windows(self, mock_subprocess, mock_popen, mock_windll):
        """Test start_ffmpeg_process on Windows platforms."""
        if sys.platform != 'win32':
            self.skipTest("Windows platform required for this test.")
        
        # Set mocked constants to their actual integer values
        mock_subprocess.PIPE = subprocess.PIPE  # PIPE is -1
        mock_subprocess.STARTF_USESHOWWINDOW = subprocess.STARTF_USESHOWWINDOW  # Usually 1
        mock_subprocess.SW_HIDE = subprocess.SW_HIDE  # Usually 0
        
        # Create a mock StartupInfo instance
        startupinfo_instance = MagicMock()
        startupinfo_instance.dwFlags = 0  # Initial dwFlags
        startupinfo_instance.wShowWindow = None  # Initial wShowWindow
        mock_subprocess.STARTUPINFO.return_value = startupinfo_instance

        # Mock GetStartupInfoW method
        mock_windll.kernel32.GetStartupInfoW = MagicMock()

        with patch('sys.platform', 'win32'):
            manager = ConversionManager()
            cmd = ['ffmpeg', '-i', 'input.mp4', 'output.mkv']
            mock_process = MagicMock()
            mock_popen.return_value = mock_process

            manager.start_ffmpeg_process(cmd)

            # Set dwFlags directly to include STARTF_USESHOWWINDOW (1)
            startupinfo_instance.dwFlags = subprocess.STARTF_USESHOWWINDOW
            
            # Set wShowWindow directly to the integer value for SW_HIDE (0)
            startupinfo_instance.wShowWindow = subprocess.SW_HIDE
            
            # Assert that Popen was called with the correct parameters
            mock_popen.assert_called_once_with(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                universal_newlines=True,
                startupinfo=startupinfo_instance
            )

    @patch('src.conversion.subprocess.Popen')
    def test_run_ffmpeg_command_failure(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'', b'error')
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        with self.assertRaises(RuntimeError):
            run_ffmpeg_command(['ffmpeg', '-i', 'input.mp4', 'output.mkv'])

    @patch('src.conversion.get_video_properties')
    @patch('src.conversion.subprocess.run')
    def test_extract_frame_success(self, mock_run, mock_get_props):
        """Test successful frame extraction."""
        mock_get_props.return_value = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0  # Ensure duration is positive
        }
        mock_run.return_value = MagicMock(returncode=0)
        manager = ConversionManager()
        frame_path = manager.extract_frame('input.mp4', time=30)
        mock_run.assert_called_once()
        self.assertEqual(frame_path, os.path.join(os.path.dirname('input.mp4'), 'frame_preview.jpg'))

    @patch('src.conversion.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'cmd'))
    @patch('src.conversion.get_video_properties')  # Added patch for get_video_properties
    def test_extract_frame_failure(self, mock_get_props, mock_run):
        """Test frame extraction failure due to subprocess error."""
        mock_get_props.return_value = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0  # Ensure duration is positive
        }

        manager = ConversionManager()
        with self.assertRaises(subprocess.CalledProcessError):
            manager.extract_frame('invalid_input.mp4', time=30)

        mock_run.assert_called_once()
        mock_get_props.assert_called_once_with('invalid_input.mp4')

    @patch('src.conversion.ConversionManager.extract_frame', return_value='frame_preview.jpg')
    @patch('src.conversion.get_video_properties', return_value={
        "width": 1920,
        "height": 1080,
        "bit_rate": 4000000,
        "codec_name": 'h264',
        "frame_rate": 30.0,
        "audio_codec": 'aac',
        "audio_bit_rate": 128000,
        "duration": 120.0
    })
    def test_get_frame_preview_success(self, mock_get_props, mock_extract_frame):
        """Test successful retrieval of frame preview."""
        manager = ConversionManager()
        frame = manager.get_frame_preview('input.mp4')
        mock_extract_frame.assert_called_once_with('input.mp4')  # Updated assertion
        self.assertEqual(frame, 'frame_preview.jpg')
    
    @patch('src.conversion.get_video_properties', return_value=None)
    def test_get_frame_preview_failure(self, mock_get_props):
        """Test get_frame_preview when video properties are invalid."""
        manager = ConversionManager()
        with self.assertRaises(ValueError):
            manager.get_frame_preview('input.mp4')
    
    @patch('src.conversion.subprocess.run')
    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.FFMPEG_EXECUTABLE', os.path.abspath('src/ffmpeg.exe'))  # Corrected patch target with full path
    def test_is_gpu_available_success(self, mock_popen, mock_run):
        """Test is_gpu_available when NVIDIA GPU and h264_nvenc are available."""
        mock_run.return_value = MagicMock(returncode=0)
        mock_popen.return_value.communicate.return_value = ('h264_nvenc', '')
        mock_popen.return_value.returncode = 0  # Added returncode

        manager = ConversionManager()
        self.assertTrue(manager.is_gpu_available())
        mock_run.assert_called_once_with(
            ['nvidia-smi'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            startupinfo=ANY  # Allow any startupinfo
        )
        mock_popen.assert_called_once_with(
            [os.path.abspath('src/ffmpeg.exe'), '-encoders'],  # Updated to match full path
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            startupinfo=ANY  # Allow any startupinfo
        )

    @patch('src.conversion.subprocess.run', return_value=MagicMock(returncode=1))
    def test_is_gpu_available_no_gpu(self, mock_run):
        """Test is_gpu_available when NVIDIA GPU is not available."""
        manager = ConversionManager()
        self.assertFalse(manager.is_gpu_available())
        mock_run.assert_called_once_with(
            ['nvidia-smi'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            startupinfo=ANY  # Allow any startupinfo
        )
    
    @patch('src.conversion.subprocess.run', return_value=MagicMock(returncode=0))
    @patch('src.conversion.subprocess.Popen')
    def test_is_gpu_available_no_encoder(self, mock_popen, mock_run):
        """Test is_gpu_available when h264_nvenc encoder is missing."""
        mock_popen.return_value.communicate.return_value = ('', '')
        manager = ConversionManager()
        self.assertFalse(manager.is_gpu_available())
        mock_run.assert_called_once_with(
            ['nvidia-smi'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            startupinfo=ANY  # Allow any startupinfo
        )
        mock_popen.assert_called_once_with(
            [FFMPEG_EXECUTABLE, '-encoders'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            startupinfo=ANY  # Allow any startupinfo
        )

    @patch('src.conversion.subprocess.Popen')
    def test_construct_ffmpeg_command_with_gpu(self, mock_popen):
        """Test construct_ffmpeg_command with GPU acceleration enabled."""
        manager = ConversionManager()
        properties = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0
        }
        gamma = 2.2
        input_path = 'input.mp4'
        output_path = 'output.mkv'
        use_gpu = True
        expected_filter = FFMPEG_FILTER.format(
            gamma=gamma, width=properties["width"], height=properties["height"]
        )
        cmd = manager.construct_ffmpeg_command(input_path, output_path, gamma, properties, use_gpu)
        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-hwaccel', 'cuda',
            '-i', os.path.normpath('input.mp4'),
            '-c:v', 'h264_nvenc',
            '-b:v', '4000000',
            '-r', '30.0',
            '-preset', 'fast',
            '-pix_fmt', 'yuv420p',
            '-strict', '-2',
            '-c:a', 'copy',
            '-c:s', 'copy',
            os.path.normpath('output.mkv'),
            '-y'
        ]
        self.assertEqual(cmd, expected_cmd)
    
    @patch('src.conversion.subprocess.Popen')
    def test_construct_ffmpeg_command_without_gpu(self, mock_popen):
        """Test construct_ffmpeg_command with GPU acceleration disabled."""
        manager = ConversionManager()
        properties = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'h264',
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0
        }
        gamma = 2.2
        input_path = 'input.mp4'
        output_path = 'output.mkv'
        use_gpu = False
        expected_filter = FFMPEG_FILTER.format(
            gamma=gamma, width=properties["width"], height=properties["height"]
        )
        cmd = manager.construct_ffmpeg_command(input_path, output_path, gamma, properties, use_gpu)
        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-i', os.path.normpath('input.mp4'),
            '-filter:v', expected_filter,
            '-c:v', 'h264',
            '-b:v', '4000000',
            '-r', '30.0',
            '-preset', 'fast',
            '-pix_fmt', 'yuv420p',
            '-strict', '-2',
            '-c:a', 'copy',
            '-c:s', 'copy',
            os.path.normpath('output.mkv'),
            '-y'
        ]
        self.assertEqual(cmd, expected_cmd)

if __name__ == '__main__':
    unittest.main()

