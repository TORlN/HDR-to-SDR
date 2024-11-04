import sys
import os
import subprocess  # Added import
import multiprocessing  # Added import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
import unittest
from unittest.mock import patch, MagicMock
from src.conversion import ConversionManager
from src.utils import get_video_properties
from tkinter import Tk
from tkinter import ttk
from PIL import Image
from src.utils import FFMPEG_FILTER

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
            "duration": 120.0
        }

        mock_process = MagicMock()
        mock_process.stderr = iter([
            'time=00:00:01.00',
            'time=00:00:02.00'
        ])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        root = Tk()
        progress_var = ttk.Progressbar(root).master
        interactable_elements = []
        cancel_button = ttk.Button(root)

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            progress_var,
            interactable_elements,
            root,
            False,
            cancel_button
        )

        self.assertIsNotNone(manager.process)
        mock_popen.assert_called_once()
        mock_get_props.assert_called_once_with('input.mp4')

    @patch('src.conversion.messagebox.showinfo')  # Mock the showinfo popup
    @patch('src.conversion.subprocess.Popen')
    def test_cancel_conversion(self, mock_popen, mock_showinfo):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = MagicMock()

        # Mock the 'after' method to execute the callback immediately
        mock_gui.root.after.side_effect = lambda delay, func: func()

        interactable_elements = []
        cancel_button = ttk.Button(MagicMock())

        manager = ConversionManager()
        manager.process = mock_process
        manager.cancel_conversion(mock_gui, interactable_elements, cancel_button)

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
            "duration": 120.0
        }

        mock_process = MagicMock()
        mock_process.stderr = iter(['error message'])
        mock_process.wait.return_value = 1
        mock_popen.return_value = mock_process

        root = Tk()
        progress_var = ttk.Progressbar(root).master
        interactable_elements = []
        cancel_button = ttk.Button(root)

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            progress_var,
            interactable_elements,
            root,
            False,
            cancel_button
        )

        # Assuming handle_completion shows a messagebox, which should be mocked if needed

    @patch('src.conversion.messagebox.showwarning')  # Mock the showwarning popup
    def test_start_conversion_invalid_paths(self, mock_showwarning):
        """Test start_conversion with invalid input or output paths."""
        manager = ConversionManager()
        manager.start_conversion('', 'output.mkv', 2.2, None, [], None, False, None)
        mock_showwarning.assert_called_once_with(
            "Warning", "Please select both an input file and specify an output file."
        )

        manager.start_conversion('input.mp4', '', 2.2, None, [], None, False, None)
        self.assertEqual(mock_showwarning.call_count, 2)

    @patch('src.conversion.get_video_properties')
    @patch('src.conversion.messagebox.showwarning')  # Mock the showwarning popup
    def test_start_conversion_no_properties(self, mock_showwarning, mock_get_props):
        """Test start_conversion when get_video_properties returns None."""
        mock_get_props.return_value = None
        manager = ConversionManager()
        manager.start_conversion('input.mp4', 'output.mkv', 2.2, None, [], None, False, None)
        mock_showwarning.assert_called_once_with(
            "Warning", "Failed to retrieve video properties."
        )
        self.assertIsNone(manager.process)
        mock_get_props.assert_called_once_with('input.mp4')

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
        cmd = manager.construct_ffmpeg_command(input_path, output_path, gamma, properties)
        expected_cmd = [
            'ffmpeg', '-loglevel', 'info',
            '-i', 'input.mp4',
            '-vf', expected_filter,
            '-c:v', 'h264',
            '-b:v', '4000000',
            '-r', '30.0',
            '-aspect', '1920/1080',
            '-threads', str(multiprocessing.cpu_count()),
            '-preset', 'faster',
            '-acodec', 'aac',
            '-b:a', '128000',
            'output.mkv',
            '-y'
        ]
        self.assertEqual(cmd, expected_cmd)

    @patch('src.conversion.messagebox.showinfo')  # Ensure this path matches the actual import in src.conversion
    @patch('src.conversion.webbrowser.open')
    def test_handle_completion_success(self, mock_webbrowser_open, mock_showinfo):
        """Test handle_completion for a successful conversion."""
        manager = ConversionManager()
        manager.process = MagicMock()
        manager.process.returncode = 0
        error_messages = []

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = MagicMock()
        # Mock the 'after' method to execute the callback immediately
        mock_gui.root.after.side_effect = lambda delay, func: func()

        cancel_button = ttk.Button(MagicMock())  # Ensure cancel_button is not None
        cancel_button.grid = MagicMock()  # Mock grid method

        with patch.object(manager, 'enable_ui') as mock_enable_ui:
            manager.handle_completion(
                gui_instance=mock_gui,
                interactable_elements=[],
                cancel_button=cancel_button,
                output_path='output.mkv',
                open_after_conversion=True,
                error_messages=error_messages
            )
            mock_showinfo.assert_called_once_with(
                "Success", "Conversion complete! Output saved to: output.mkv"
            )
            mock_webbrowser_open.assert_called_once_with('output.mkv')
            mock_enable_ui.assert_called_once_with([])

    @patch('src.conversion.messagebox.showerror')  # Mock the showerror popup
    def test_handle_completion_failure(self, mock_showerror):
        """Test handle_completion for a failed conversion."""
        manager = ConversionManager()
        manager.process = MagicMock()
        manager.process.returncode = 1
        error_messages = ['error message']

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = MagicMock()
        # Mock the 'after' method to execute the callback immediately
        mock_gui.root.after.side_effect = lambda delay, func: func()

        cancel_button = ttk.Button(MagicMock())  # Ensure cancel_button is not None
        cancel_button.grid = MagicMock()  # Mock grid method

        with patch.object(manager, 'enable_ui') as mock_enable_ui:
            manager.handle_completion(
                gui_instance=mock_gui,
                interactable_elements=[],
                cancel_button=cancel_button,
                output_path='output.mkv',
                open_after_conversion=False,
                error_messages=error_messages
            )
            mock_showerror.assert_called_once_with(
                "Error", "Conversion failed with code 1\nerror message"
            )
            mock_enable_ui.assert_called_once_with([])

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

    @patch('src.conversion.subprocess.STARTUPINFO')
    @patch('src.conversion.subprocess.STARTF_USESHOWWINDOW', new=1)  # Assign integer value
    @patch('src.conversion.subprocess.SW_HIDE', new=0)               # Assign integer value
    @patch('src.conversion.subprocess.Popen')
    def test_start_ffmpeg_process_windows(self, mock_popen, mock_startupinfo):
        """Test start_ffmpeg_process on Windows platforms."""
        if sys.platform != 'win32':
            self.skipTest("Windows platform required for this test.")
        
        # Create a mock StartupInfo instance
        startupinfo_instance = MagicMock()
        startupinfo_instance.dwFlags = 0  # Initial dwFlags
        startupinfo_instance.wShowWindow = None  # Initial wShowWindow
        mock_startupinfo.return_value = startupinfo_instance

        with patch('sys.platform', 'win32'):
            manager = ConversionManager()
            cmd = ['ffmpeg', '-i', 'input.mp4', 'output.mkv']
            mock_process = MagicMock()
            mock_popen.return_value = mock_process

            manager.start_ffmpeg_process(cmd)

            # Assert that dwFlags was updated with STARTF_USESHOWWINDOW (1)
            self.assertEqual(startupinfo_instance.dwFlags, 1, 
                             "dwFlags should include STARTF_USESHOWWINDOW (1)")
            
            # Assert that wShowWindow was set to SW_HIDE (0)
            self.assertEqual(startupinfo_instance.wShowWindow, 0, 
                             "wShowWindow should be set to SW_HIDE (0)")

            # Assert that Popen was called with the correct parameters
            mock_popen.assert_called_once_with(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                universal_newlines=True,
                startupinfo=startupinfo_instance
            )

if __name__ == '__main__':
    unittest.main()

