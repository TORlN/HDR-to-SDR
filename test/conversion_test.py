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

        selected_filter_index = 0  # Add a selected_filter_index value

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            False,  # Added use_gpu argument
            selected_filter_index,  # Added selected_filter_index argument
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

        selected_filter_index = 0  # Add a selected_filter_index value

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            False,  # use_gpu
            selected_filter_index,  # Added selected_filter_index argument
            progress_var,
            interactable_elements,
            mock_gui,  # gui_instance
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

        selected_filter_index = 0  # Add a selected_filter_index value

        manager.start_conversion('', 'output.mkv', 2.2, False, selected_filter_index, progress_var, interactable_elements, mock_gui, False, cancel_button)
        mock_showwarning.assert_called_once_with(
            "Warning", "Please select both an input file and specify an output file."
        )

        mock_showwarning.reset_mock()
        manager.start_conversion('input.mp4', '', 2.2, False, selected_filter_index, progress_var, interactable_elements, mock_gui, False, cancel_button)
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
        selected_filter_index = 0  # Add a selected_filter_index value

        manager.start_conversion(input_path, 'output.mkv', 2.2, False, selected_filter_index, progress_var, 
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
            "codec_name": 'libx264',  # Changed from 'h264' to 'libx264'
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0
        }
        gamma = 2.2
        input_path = 'input.mp4'
        output_path = 'output.mkv'
        selected_filter_index = 0  # Add selected_filter_index
        tonemapper = 'reinhard'  # Add tonemapper parameter
        
        expected_filter = FFMPEG_FILTER[selected_filter_index].format(
            gamma=gamma, 
            width=properties["width"], 
            height=properties["height"],
            tonemapper=tonemapper
        )
        cmd = manager.construct_ffmpeg_command(
            input_path, 
            output_path, 
            gamma, 
            properties, 
            False,  # use_gpu 
            selected_filter_index,
            tonemapper=tonemapper
        )
        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-i', input_path,
            '-filter_complex', f'[0:v:0]{expected_filter}[vout]',
            '-map', '[vout]',
            '-map', '0:a?',
            '-map', '0:s?',
            '-c:v', 'libx264',  # Changed from properties['codec_name'] which was 'h264'
            '-preset', 'veryfast',  # Changed from 'fast' to 'veryfast'
            '-tune', 'film',        # Added '-tune' option
            '-crf', '23',           # Added '-crf' option
            '-b:v', str(properties['bit_rate']),
            '-r', str(properties['frame_rate']),
            '-pix_fmt', 'yuv420p',  # Added pix_fmt and yuv420p
            '-strict', '-2',
            '-c:a', 'copy',
            '-c:s', 'copy',         # Added '-c:s'
            '-map_metadata', '0',  # Added metadata mapping
            '-movflags', '+faststart',  # Added movflags for streaming
            os.path.normpath(output_path),
            '-y'
        ]
        self.assertEqual(cmd, expected_cmd)

    @patch('src.conversion.get_maxfall')  # Mock get_maxfall
    @patch('src.conversion.subprocess.Popen')
    def test_construct_ffmpeg_command_with_subtitles(self, mock_get_props, mock_get_maxfall):
        """Test that construct_ffmpeg_command includes subtitle streams when available."""
        mock_get_maxfall.return_value = 10  # Set a predefined maxfall value
        
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
        tonemapper = 'reinhard'  # Add tonemapper parameter
        cmd = manager.construct_ffmpeg_command(
            'input.mp4',
            'output.mkv',
            2.2,
            mock_get_props.return_value,
            False,  # use_gpu
             1,      # selected_filter_index
            tonemapper=tonemapper
        )

        expected_filter = FFMPEG_FILTER[1].format(
            gamma=2.2, 
            width=1920, 
            height=1080, 
            npl=10,
            tonemapper=tonemapper
        )
        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-i', os.path.normpath('input.mp4'),
            '-filter_complex', f'[0:v:0]{expected_filter}[vout]',
            '-map', '[vout]',
            '-map', '0:a?',
            '-map', '0:s?',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-tune', 'film',
            '-crf', '23',
            '-b:v', '4000000',
            '-r', '30.0',
            '-pix_fmt', 'yuv420p',
            '-strict', '-2',
            '-c:a', 'copy',
            '-c:s', 'copy',
            '-map_metadata', '0',
            '-movflags', '+faststart',
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

    @patch('src.conversion.messagebox.showerror')
    def test_handle_completion_truncates_long_error_output(self, mock_showerror):
        """Error dialog must show only the last 50 stderr lines, not all of them.

        ffmpeg emits progress lines for every decoded frame; for a long
        conversion the full stderr can be thousands of lines. Showing all of
        them makes the dialog unreadable -- only the tail is shown instead.
        """
        manager = ConversionManager()
        manager.process = MagicMock()
        manager.process.returncode = 1
        manager.cancelled = False
        spam = [f"frame={i} fps=30 time=00:00:{i:02d}.00" for i in range(190)]
        real_errors = ["Error: something went wrong", "Error: codec not found"]
        error_messages = spam + real_errors

        mock_gui = MagicMock()
        mock_gui.root = Tk()
        mock_gui.root.after = MagicMock(side_effect=lambda delay, func: func())
        cancel_button = MagicMock()
        cancel_button.grid_remove = MagicMock()

        with patch.object(manager, 'enable_ui'):
            manager.handle_completion(
                mock_gui, [], cancel_button, 'out.mkv', False, error_messages
            )

        call_args = mock_showerror.call_args[0]
        shown_text = call_args[1]
        # Strip the "Conversion failed with code X" header line
        content_lines = shown_text.split('\n')[1:]
        self.assertLessEqual(len(content_lines), 50,
                             "Error dialog must show at most 50 stderr lines")
        self.assertIn("Error: something went wrong", shown_text)
        self.assertIn("Error: codec not found", shown_text)

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
                stdout=subprocess.DEVNULL,
                universal_newlines=True,
                startupinfo=None,
                encoding='utf-8',  # Added encoding
                errors='replace',    # Added errors
                creationflags=ANY  # Allow any creationflags
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
        mock_subprocess.DEVNULL = subprocess.DEVNULL  # DEVNULL is -3
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

            process = manager.start_ffmpeg_process(cmd)
            mock_popen.assert_called_once_with(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                universal_newlines=True,
                startupinfo=startupinfo_instance,
                encoding='utf-8',          # Added encoding
                errors='replace',            # Added errors
                creationflags=ANY  # Allow any creationflags
            )

    @patch('src.conversion.subprocess.Popen')
    def test_run_ffmpeg_command_failure(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'', b'error')
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        with self.assertRaises(RuntimeError):
            run_ffmpeg_command(['ffmpeg', '-i', 'input.mp4', 'output.mkv'])

    def test_is_gpu_available_no_gpu(self):
        """is_gpu_available returns False when detect_gpu_encoder finds nothing."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', return_value=None):
            self.assertFalse(manager.is_gpu_available())

    def test_is_gpu_available_no_encoder(self):
        """is_gpu_available returns False when no GPU encoder is detected."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', return_value=None):
            self.assertFalse(manager.is_gpu_available())

    @patch('src.conversion.get_maxfall')
    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.ConversionManager.is_gpu_available', return_value=True)
    def test_construct_ffmpeg_command_with_gpu(self, mock_popen, mock_get_maxfall, mock_is_gpu):
        """Test construct_ffmpeg_command with GPU acceleration enabled."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        mock_get_maxfall.return_value = 10
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
        selected_filter_index = 1  
        tonemapper = 'reinhard'

        expected_filter = FFMPEG_FILTER[selected_filter_index].format(
            gamma=gamma, 
            width=properties["width"], 
            height=properties["height"], 
            npl=mock_get_maxfall.return_value,
            tonemapper=tonemapper
        )
        cmd = manager.construct_ffmpeg_command(input_path, output_path, gamma, properties, use_gpu, selected_filter_index)
        
        # Verify the command includes GPU-specific parameters
        self.assertIn('-hwaccel', cmd)
        self.assertIn('cuda', cmd)
        self.assertIn('h264_nvenc', cmd)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_nvenc')

    @patch('src.conversion.ConversionManager.is_gpu_available', return_value=False)  
    @patch('src.conversion.get_maxfall')
    @patch('src.conversion.subprocess.Popen')
    def test_construct_ffmpeg_command_without_gpu(self, mock_popen, mock_get_maxfall, mock_is_gpu):
        """Test construct_ffmpeg_command with GPU acceleration disabled."""
        mock_get_maxfall.return_value = 10  
        manager = ConversionManager()
        properties = {
            "width": 1920,
            "height": 1080,
            "bit_rate": 4000000,
            "codec_name": 'libx264',  
            "frame_rate": 30.0,
            "audio_codec": 'aac',
            "audio_bit_rate": 128000,
            "duration": 120.0
        }
        gamma = 2.2
        input_path = 'input.mp4'
        output_path = 'output.mkv'
        use_gpu = False
        selected_filter_index = 0  
        tonemapper = 'reinhard'  # Add tonemapper parameter

        expected_filter = FFMPEG_FILTER[selected_filter_index].format(
            gamma=gamma, 
            width=properties["width"], 
            height=properties["height"],
            tonemapper=tonemapper
        )
        cmd = manager.construct_ffmpeg_command(input_path, output_path, gamma, properties, use_gpu, selected_filter_index)
        expected_cmd = [
            FFMPEG_EXECUTABLE, '-loglevel', 'info',
            '-i', os.path.normpath('input.mp4'),
            '-filter_complex', f'[0:v:0]{expected_filter}[vout]',
            '-map', '[vout]',
            '-map', '0:a?',
            '-map', '0:s?',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-tune', 'film',
            '-crf', '23',
            '-b:v', '4000000',
            '-r', '30.0',
            '-pix_fmt', 'yuv420p',
            '-strict', '-2',
            '-c:a', 'copy',
            '-c:s', 'copy',
            '-map_metadata', '0',
            '-movflags', '+faststart',
            os.path.normpath('output.mkv'),
            '-y'
        ]
        self.assertEqual(cmd, expected_cmd)

    @patch('src.conversion.platform.system', return_value='Darwin')
    @patch('src.conversion.messagebox.showwarning')
    def test_construct_command_gpu_unsupported_platform(self, mock_warn, _plat):
        """On non-Windows/Linux, requesting GPU warns and falls back to CPU."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        props = {
            "width": 1920, "height": 1080, "bit_rate": 4000000, "frame_rate": 30.0,
            "duration": 120.0, "audio_codec": "aac", "audio_bit_rate": 128000,
            "subtitle_streams": [],
        }
        cmd = manager.construct_ffmpeg_command('in.mp4', 'out.mkv', 2.2, props,
                                               True, 0, tonemapper='reinhard')
        mock_warn.assert_called_once()
        self.assertIn('libx264', cmd)
        self.assertNotIn('h264_nvenc', cmd)
        self.assertNotIn('-hwaccel', cmd)

    _QUALITY_PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 120.0, "subtitle_streams": [],
    }

    def test_quality_sets_crf_for_cpu(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, False, 0,
            tonemapper='reinhard', quality=19)
        self.assertEqual(cmd[cmd.index('-crf') + 1], '19')

    def test_quality_default_is_23(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, False, 0, tonemapper='reinhard')
        self.assertEqual(cmd[cmd.index('-crf') + 1], '23')

    @patch('src.conversion.get_maxfall', return_value=10)
    def test_quality_sets_cq_for_nvenc(self, _mf):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, True, 1,
            tonemapper='reinhard', quality=18)
        self.assertEqual(cmd[cmd.index('-cq') + 1], '18')

    def test_quality_sets_global_quality_for_qsv(self):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_qsv'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, True, 0,
            tonemapper='reinhard', quality=27)
        self.assertEqual(cmd[cmd.index('-global_quality') + 1], '27')

    @patch('src.conversion.platform.system', return_value='Windows')
    def test_quality_sets_qp_for_amf(self, _plat):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_amf'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, True, 0,
            tonemapper='reinhard', quality=22)
        self.assertIn('h264_amf', cmd)
        self.assertEqual(cmd[cmd.index('-qp_i') + 1], '22')
        self.assertEqual(cmd[cmd.index('-qp_p') + 1], '22')

    def test_is_gpu_available_encoder_list_fails(self):
        """is_gpu_available returns False when encoder detection finds nothing."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', return_value=None):
            self.assertFalse(manager.is_gpu_available())

    def test_is_gpu_available_nvenc_not_listed(self):
        """is_gpu_available returns False when no supported GPU encoder is detected."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', return_value=None):
            self.assertFalse(manager.is_gpu_available())

    def test_is_gpu_available_nvidia_smi_missing(self):
        """is_gpu_available returns False when detect_gpu_encoder raises."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', side_effect=FileNotFoundError()):
            self.assertFalse(manager.is_gpu_available())

    def test_is_gpu_available(self):
        """is_gpu_available returns True when an encoder is detected."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', return_value='h264_nvenc'):
            self.assertTrue(manager.is_gpu_available())

    @patch('src.conversion.messagebox.showwarning')
    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.get_video_properties')
    def test_start_conversion_zero_duration_aborts(self, mock_get_props, mock_popen, mock_showwarning):
        """A zero-duration file must abort before the monitor thread can divide by zero."""
        mock_get_props.return_value = {
            "width": 1920, "height": 1080, "bit_rate": 4000000,
            "codec_name": 'h264', "frame_rate": 30.0, "audio_codec": 'aac',
            "audio_bit_rate": 128000, "duration": 0, "subtitle_streams": []
        }
        root = Tk()
        mock_gui = MagicMock()
        mock_gui.root = root
        progress_var = DoubleVar(master=root)

        manager = ConversionManager()
        manager.start_conversion('input.mp4', 'output.mkv', 2.2, False, 0,
                                 progress_var, [], mock_gui, False, MagicMock())

        mock_showwarning.assert_called_once()
        self.assertIn("duration", mock_showwarning.call_args[0][1].lower())
        mock_popen.assert_not_called()  # never launched ffmpeg
        self.assertIsNone(manager.process)
        root.destroy()


class TestBatchCompletionHook(unittest.TestCase):
    """An on_complete callback replaces the per-file dialog for batch (queue) runs."""

    _PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 10.0, "subtitle_streams": [],
    }

    def _gui(self):
        gui = MagicMock()
        gui.root.after = MagicMock(side_effect=lambda delay, func: func())
        return gui

    @patch('src.conversion.messagebox')
    @patch('src.conversion.webbrowser.open')
    def test_on_complete_called_instead_of_dialog_on_success(self, mock_open, mock_mb):
        manager = ConversionManager()
        manager.process = MagicMock(); manager.process.returncode = 0
        manager._on_complete = MagicMock()
        manager.handle_completion(self._gui(), [], MagicMock(), 'out.mkv', False, [])
        manager._on_complete.assert_called_once_with(True)
        mock_mb.showinfo.assert_not_called()  # no per-file success dialog

    @patch('src.conversion.messagebox')
    def test_on_complete_called_with_false_on_failure(self, mock_mb):
        manager = ConversionManager()
        manager.process = MagicMock(); manager.process.returncode = 1
        manager.cancelled = False
        manager._on_complete = MagicMock()
        manager.handle_completion(self._gui(), [], MagicMock(), 'out.mkv', False, ['err'])
        manager._on_complete.assert_called_once_with(False)
        mock_mb.showerror.assert_not_called()  # no per-file error dialog

    @patch('src.conversion.messagebox')
    def test_on_complete_does_not_enable_ui_between_files(self, mock_mb):
        # Between queued files the UI must stay disabled and the cancel button shown.
        manager = ConversionManager()
        manager.process = MagicMock(); manager.process.returncode = 0
        manager._on_complete = MagicMock()
        cancel = MagicMock()
        with patch.object(manager, 'enable_ui') as mock_enable:
            manager.handle_completion(self._gui(), ['e'], cancel, 'out.mkv', False, [])
        mock_enable.assert_not_called()
        cancel.grid_remove.assert_not_called()

    @patch('src.conversion.get_video_properties')
    @patch('src.conversion.subprocess.Popen')
    def test_start_conversion_stores_on_complete(self, mock_popen, mock_props):
        mock_props.return_value = dict(self._PROPS)
        proc = MagicMock(); proc.stderr = iter([]); proc.returncode = 0
        mock_popen.return_value = proc
        manager = ConversionManager()
        done = MagicMock()
        manager.start_conversion('in.mp4', 'out.mkv', 1.0, False, 0, MagicMock(),
                                 [], self._gui(), False, MagicMock(), on_complete=done)
        self.assertIs(manager._on_complete, done)


class TestDetectGpuEncoder(unittest.TestCase):
    """detect_gpu_encoder finds the best available H.264 GPU encoder."""

    def _manager_with_encoders(self, encoder_string, nvidia_present=False):
        """Helper: patch _list_encoders and _nvidia_present, return manager."""
        m = ConversionManager()
        m._list_encoders = MagicMock(return_value=encoder_string)
        m._nvidia_present = MagicMock(return_value=nvidia_present)
        return m

    def test_nvenc_returned_when_nvidia_present(self):
        m = self._manager_with_encoders('h264_nvenc h264_amf h264_qsv', nvidia_present=True)
        self.assertEqual(m.detect_gpu_encoder(), 'h264_nvenc')
        self.assertEqual(m._gpu_encoder, 'h264_nvenc')

    def test_amf_returned_when_nvidia_absent_but_amf_listed(self):
        m = self._manager_with_encoders('h264_amf h264_qsv', nvidia_present=False)
        self.assertEqual(m.detect_gpu_encoder(), 'h264_amf')

    def test_qsv_returned_when_only_qsv_available(self):
        m = self._manager_with_encoders('h264_qsv', nvidia_present=False)
        self.assertEqual(m.detect_gpu_encoder(), 'h264_qsv')

    def test_none_returned_when_no_gpu_encoders(self):
        m = self._manager_with_encoders('libx264 libx265', nvidia_present=False)
        self.assertIsNone(m.detect_gpu_encoder())
        self.assertIsNone(m._gpu_encoder)

    def test_nvenc_not_chosen_without_nvidia_gpu(self):
        """Even if h264_nvenc is in ffmpeg, skip it when nvidia-smi says no GPU."""
        m = self._manager_with_encoders('h264_nvenc h264_amf', nvidia_present=False)
        self.assertEqual(m.detect_gpu_encoder(), 'h264_amf')

    def test_is_gpu_available_delegates_to_detect(self):
        m = ConversionManager()
        with patch.object(m, 'detect_gpu_encoder', return_value='h264_amf') as mock_detect:
            self.assertTrue(m.is_gpu_available())
            mock_detect.assert_called_once()

    def test_is_gpu_available_false_when_detect_returns_none(self):
        m = ConversionManager()
        with patch.object(m, 'detect_gpu_encoder', return_value=None):
            self.assertFalse(m.is_gpu_available())


class TestGpuEncoderCommandConstruction(unittest.TestCase):
    """construct_ffmpeg_command uses the detected GPU encoder type correctly."""

    _BASE_PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000,
        "frame_rate": 30.0, "audio_codec": "aac", "audio_bit_rate": 128000,
        "subtitle_streams": [],
    }

    def _cmd(self, encoder, props=None):
        m = ConversionManager()
        m._gpu_encoder = encoder
        return m.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 1.0, props or self._BASE_PROPS,
            use_gpu=True, selected_filter_index=0, tonemapper='reinhard'
        )

    def test_amf_encoder_used_and_no_hwaccel(self):
        cmd = self._cmd('h264_amf')
        self.assertIn('h264_amf', cmd)
        self.assertNotIn('-hwaccel', cmd)
        self.assertNotIn('h264_nvenc', cmd)

    def test_qsv_encoder_used_with_qsv_hwaccel(self):
        cmd = self._cmd('h264_qsv')
        self.assertIn('h264_qsv', cmd)
        self.assertIn('-hwaccel', cmd)
        self.assertIn('qsv', cmd)
        self.assertNotIn('cuda', cmd)

    def test_nvenc_encoder_used_with_cuda_hwaccel(self):
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = m.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 1.0, self._BASE_PROPS,
            use_gpu=True, selected_filter_index=0, tonemapper='reinhard'
        )
        self.assertIn('h264_nvenc', cmd)
        self.assertIn('cuda', cmd)
        self.assertIn('-hwaccel', cmd)


class TestContainerStreamArgs(unittest.TestCase):
    """Container-aware audio/subtitle handling in construct_ffmpeg_command."""

    def setUp(self):
        self.manager = ConversionManager()

    def test_mkv_copies_everything(self):
        props = {'audio_codec': 'truehd',
                 'subtitle_streams': [{'codec_name': 'hdmv_pgs_subtitle', 'index': 2}]}
        self.assertEqual(
            self.manager._container_stream_args('out.mkv', props),
            (['-map', '0:s?'], ['-c:a', 'copy'], ['-c:s', 'copy']))

    def test_mp4_transcodes_truehd_and_keeps_only_text_subs(self):
        props = {'audio_codec': 'truehd', 'audio_bit_rate': 0,
                 'subtitle_streams': [
                     {'codec_name': 'subrip', 'index': 3},
                     {'codec_name': 'hdmv_pgs_subtitle', 'index': 2},
                     {'codec_name': 'ass', 'index': 4},
                 ]}
        sub_map, audio, sub_codec = self.manager._container_stream_args('out.mp4', props)
        self.assertEqual(sub_map, ['-map', '0:3', '-map', '0:4'])  # text subs only
        self.assertEqual(audio, ['-c:a', 'aac', '-b:a', '192k'])    # no source bitrate
        self.assertEqual(sub_codec, ['-c:s', 'mov_text'])

    def test_mp4_caps_transcode_bitrate(self):
        props = {'audio_codec': 'dts', 'audio_bit_rate': 1500000, 'subtitle_streams': []}
        _, audio, _ = self.manager._container_stream_args('out.mp4', props)
        self.assertEqual(audio, ['-c:a', 'aac', '-b:a', '384000'])

    def test_mp4_copies_compatible_audio_and_drops_image_subs(self):
        props = {'audio_codec': 'eac3', 'audio_bit_rate': 0,
                 'subtitle_streams': [{'codec_name': 'hdmv_pgs_subtitle', 'index': 2}]}
        sub_map, audio, sub_codec = self.manager._container_stream_args('out.mp4', props)
        self.assertEqual(sub_map, [])          # image subs dropped
        self.assertEqual(audio, ['-c:a', 'copy'])  # eac3 is mp4-legal
        self.assertEqual(sub_codec, [])

    def test_m4v_and_mov_behave_like_mp4(self):
        props = {'audio_codec': 'truehd', 'audio_bit_rate': 0, 'subtitle_streams': []}
        for path in ('out.m4v', 'out.MOV'):
            _, audio, _ = self.manager._container_stream_args(path, props)
            self.assertEqual(audio, ['-c:a', 'aac', '-b:a', '192k'])


if __name__ == '__main__':
    unittest.main()

