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
from tkinter import ttk
from PIL import Image
from src.utils import FFMPEG_CONVERT_FILTER
from src.utils import FFMPEG_EXECUTABLE  # Import FFMPEG_EXECUTABLE

class TestConversionManager(unittest.TestCase):

    def setUp(self):
        # These tests assert the CPU / GPU-encoder command shapes, so pin the
        # libplacebo probe off (the GPU tonemap path has its own tests below).
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        mock_gui.root = MagicMock()
        mock_gui.root.after = MagicMock()

        progress_var = MagicMock()
        interactable_elements = []
        cancel_button = MagicMock()

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4',
            'output.mkv',
            2.2,
            False,
            progress_var,
            interactable_elements,
            mock_gui,
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
        mock_gui.root = MagicMock()  # cleanup captures real destroy before the mock below
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

    @patch('src.conversion.messagebox.showwarning')
    @patch('src.conversion.get_video_properties')
    def test_start_conversion_invalid_paths(self, mock_get_props, mock_showwarning):  # Swapped argument order
        """Test start_conversion with invalid input or output paths."""
        manager = ConversionManager()
        mock_gui = MagicMock()
        mock_gui.root = MagicMock()
        mock_gui.root.after = MagicMock()
        progress_var = MagicMock()
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
        mock_gui.root = MagicMock()
        mock_gui.root.after = MagicMock()
        progress_var = MagicMock()
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
    def test_construct_ffmpeg_command_with_subtitles(self, mock_popen):
        """Test that construct_ffmpeg_command includes subtitle streams when available."""
        properties = {
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
        tonemapper = 'reinhard'
        cmd = manager.construct_ffmpeg_command(
            'input.mp4', 'output.mkv', 2.2, properties,
            False, tonemapper=tonemapper
        )

        expected_filter = FFMPEG_CONVERT_FILTER.format(gamma=2.2, tonemapper=tonemapper)
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
        mock_gui.root = MagicMock()
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
        mock_gui.root = MagicMock()
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
        mock_gui.root = MagicMock()
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
            self.skipTest('STARTUPINFO behaviour is Windows-only')

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

    def test_is_gpu_available_no_gpu(self):
        """is_gpu_available returns False when detect_gpu_encoder finds nothing."""
        manager = ConversionManager()
        with patch.object(manager, 'detect_gpu_encoder', return_value=None):
            self.assertFalse(manager.is_gpu_available())

    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.ConversionManager.is_gpu_available', return_value=True)
    def test_construct_ffmpeg_command_with_gpu(self, mock_popen, mock_is_gpu):
        """Test construct_ffmpeg_command with GPU acceleration enabled."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        properties = {
            "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
            "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
            "duration": 120.0
        }
        cmd = manager.construct_ffmpeg_command(
            'input.mp4', 'output.mkv', 2.2, properties, use_gpu=True
        )
        self.assertIn('-hwaccel', cmd)
        self.assertIn('cuda', cmd)
        self.assertIn('h264_nvenc', cmd)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_nvenc')

    @patch('src.conversion.ConversionManager.is_gpu_available', return_value=False)
    @patch('src.conversion.subprocess.Popen')
    def test_construct_ffmpeg_command_without_gpu(self, mock_popen, mock_is_gpu):
        """Test construct_ffmpeg_command with GPU acceleration disabled."""
        manager = ConversionManager()
        properties = {
            "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'libx264',
            "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
            "duration": 120.0
        }
        gamma = 2.2
        tonemapper = 'reinhard'

        expected_filter = FFMPEG_CONVERT_FILTER.format(gamma=gamma, tonemapper=tonemapper)
        cmd = manager.construct_ffmpeg_command('input.mp4', 'output.mkv', gamma, properties,
                                               use_gpu=False, tonemapper=tonemapper)
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
                                               True, tonemapper='reinhard')
        mock_warn.assert_called_once()
        self.assertIn('libx264', cmd)
        self.assertNotIn('h264_nvenc', cmd)
        self.assertNotIn('-hwaccel', cmd)

    @patch('src.conversion.platform.system', return_value='Darwin')
    @patch('src.conversion.messagebox.showwarning')
    def test_construct_command_qsv_unsupported_platform(self, mock_warn, _plat):
        """On non-Windows/Linux, h264_qsv warns and falls back to CPU encoder."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_qsv'
        props = {
            "width": 1920, "height": 1080, "bit_rate": 4000000, "frame_rate": 30.0,
            "duration": 120.0, "audio_codec": "aac", "audio_bit_rate": 128000,
            "subtitle_streams": [],
        }
        cmd = manager.construct_ffmpeg_command('in.mp4', 'out.mkv', 2.2, props,
                                               True, tonemapper='reinhard')
        mock_warn.assert_called_once()
        self.assertIn('libx264', cmd)
        self.assertNotIn('h264_qsv', cmd)
        self.assertNotIn('-hwaccel', cmd)

    @patch('src.conversion.platform.system', return_value='Darwin')
    @patch('src.conversion.messagebox.showwarning')
    def test_construct_command_amf_unsupported_platform(self, mock_warn, _plat):
        """On non-Windows/Linux, h264_amf warns and falls back to CPU encoder."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_amf'
        props = {
            "width": 1920, "height": 1080, "bit_rate": 4000000, "frame_rate": 30.0,
            "duration": 120.0, "audio_codec": "aac", "audio_bit_rate": 128000,
            "subtitle_streams": [],
        }
        cmd = manager.construct_ffmpeg_command('in.mp4', 'out.mkv', 2.2, props,
                                               True, tonemapper='reinhard')
        mock_warn.assert_called_once()
        self.assertIn('libx264', cmd)
        self.assertNotIn('h264_amf', cmd)

    @patch('src.conversion.platform.system', return_value='Darwin')
    @patch('src.conversion.messagebox.showwarning')
    def test_construct_command_unknown_encoder_unsupported_platform(self, mock_warn, _plat):
        """On non-Windows/Linux, an unrecognised GPU encoder warns and falls back to CPU."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_unknown_gpu'
        props = {
            "width": 1920, "height": 1080, "bit_rate": 4000000, "frame_rate": 30.0,
            "duration": 120.0, "audio_codec": "aac", "audio_bit_rate": 128000,
            "subtitle_streams": [],
        }
        cmd = manager.construct_ffmpeg_command('in.mp4', 'out.mkv', 2.2, props,
                                               True, tonemapper='reinhard')
        mock_warn.assert_called_once()
        self.assertIn('libx264', cmd)
        self.assertNotIn('h264_unknown_gpu', cmd)

    _QUALITY_PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 120.0, "subtitle_streams": [],
    }

    def test_quality_sets_crf_for_cpu(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, False,
            tonemapper='reinhard', quality=19)
        self.assertEqual(cmd[cmd.index('-crf') + 1], '19')

    def test_quality_default_is_23(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, False, tonemapper='reinhard')
        self.assertEqual(cmd[cmd.index('-crf') + 1], '23')

    def test_quality_sets_cq_for_nvenc(self):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, True,
            tonemapper='reinhard', quality=18)
        self.assertEqual(cmd[cmd.index('-cq') + 1], '18')

    def test_quality_sets_global_quality_for_qsv(self):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_qsv'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, True,
            tonemapper='reinhard', quality=27)
        self.assertEqual(cmd[cmd.index('-global_quality') + 1], '27')

    @patch('src.conversion.platform.system', return_value='Windows')
    def test_quality_sets_qp_for_amf(self, _plat):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_amf'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._QUALITY_PROPS, True,
            tonemapper='reinhard', quality=22)
        self.assertIn('h264_amf', cmd)
        self.assertEqual(cmd[cmd.index('-qp_i') + 1], '22')
        self.assertEqual(cmd[cmd.index('-qp_p') + 1], '22')

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

    def test_gpu_acceleration_available_with_encoder_only(self):
        """A hardware encoder alone enables the GPU toggle."""
        manager = ConversionManager()
        with patch.object(manager, 'is_gpu_available', return_value=True), \
             patch('src.conversion.vulkan_libplacebo_available', return_value=False):
            self.assertTrue(manager.is_gpu_acceleration_available())

    def test_gpu_acceleration_available_with_libplacebo_only(self):
        """GPU tonemapping (libplacebo) alone enables the toggle, even with no
        hardware encoder -- the decoupled case."""
        manager = ConversionManager()
        with patch.object(manager, 'is_gpu_available', return_value=False), \
             patch('src.conversion.vulkan_libplacebo_available', return_value=True):
            self.assertTrue(manager.is_gpu_acceleration_available())

    def test_gpu_acceleration_unavailable_when_neither(self):
        manager = ConversionManager()
        with patch.object(manager, 'is_gpu_available', return_value=False), \
             patch('src.conversion.vulkan_libplacebo_available', return_value=False):
            self.assertFalse(manager.is_gpu_acceleration_available())

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
        mock_gui = MagicMock()
        progress_var = MagicMock()

        manager = ConversionManager()
        manager.start_conversion('input.mp4', 'output.mkv', 2.2, False,
                                 progress_var, [], mock_gui, False, MagicMock())

        mock_showwarning.assert_called_once()
        self.assertIn("duration", mock_showwarning.call_args[0][1].lower())
        mock_popen.assert_not_called()  # never launched ffmpeg
        self.assertIsNone(manager.process)


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
        manager.start_conversion('in.mp4', 'out.mkv', 1.0, False, MagicMock(),
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

    def setUp(self):
        # These assert the GPU-encoder decode-hwaccel shapes; the libplacebo
        # tonemap path (which drops decode hwaccel) is covered separately.
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

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
            use_gpu=True, tonemapper='reinhard'
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
            use_gpu=True, tonemapper='reinhard'
        )
        self.assertIn('h264_nvenc', cmd)
        self.assertIn('cuda', cmd)
        self.assertIn('-hwaccel', cmd)

    def test_nvenc_zero_bitrate_uses_fallback(self):
        """bit_rate=0 from MKV containers must not produce -b:v 0 / -maxrate 0 /
        -bufsize 0 — those args make nvenc behave unpredictably. A sensible
        fallback bitrate must be substituted."""
        cmd = self._cmd('h264_nvenc', props={**self._BASE_PROPS, 'bit_rate': 0})
        bv_val = cmd[cmd.index('-b:v') + 1]
        maxrate_val = cmd[cmd.index('-maxrate') + 1]
        bufsize_val = cmd[cmd.index('-bufsize') + 1]
        self.assertNotEqual(bv_val, '0')
        self.assertNotEqual(maxrate_val, '0')
        self.assertNotEqual(bufsize_val, '0')

    def test_qsv_zero_bitrate_uses_fallback(self):
        """Same zero-bitrate guard applies to h264_qsv."""
        cmd = self._cmd('h264_qsv', props={**self._BASE_PROPS, 'bit_rate': 0})
        bv_val = cmd[cmd.index('-b:v') + 1]
        self.assertNotEqual(bv_val, '0')

    def test_gpu_encoder_auto_detected_when_not_yet_probed(self):
        """construct_ffmpeg_command must call detect_gpu_encoder when use_gpu=True
        but _gpu_encoder is still None — the case when GPU was enabled in saved
        settings and the user converts without ever toggling the checkbox."""
        m = ConversionManager()
        # _gpu_encoder starts None (fresh __init__, settings loaded with gpu_accel=True)
        self.assertIsNone(m._gpu_encoder)
        with patch.object(m, 'detect_gpu_encoder', return_value='h264_nvenc') as mock_detect:
            cmd = m.construct_ffmpeg_command(
                'in.mp4', 'out.mkv', 1.0, self._BASE_PROPS,
                use_gpu=True, tonemapper='reinhard')
        mock_detect.assert_called_once()
        self.assertIn('h264_nvenc', cmd)


class TestBitDepthPixelFormat(unittest.TestCase):
    """Output Color Depth: -pix_fmt and codec selection on the CPU path, for
    a non-HEVC (h264) source -- see TestHEVCSourcePreservation below for the
    HEVC-source case, which switches to libx265 even at 8/10-bit.

    This app's bundled ffmpeg is compiled with high-bit-depth libx264 support
    (verified: 'ffmpeg -h encoder=libx264' lists yuv420p10le among supported
    pixel formats) and a 12-bit-capable libx265 (verified: running it prints
    'x265 [info]: Main 12 profile'), so for an h264 source, 8/10-bit output
    stays on libx264 while 12-bit switches to libx265 -- libx264 silently
    downgrades yuv420p12le to 10-bit instead of erroring, so this switch has
    to be explicit.
    """

    def setUp(self):
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    _PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 120.0, "subtitle_streams": [],
    }

    def test_eight_bit_appends_yuv420p(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._PROPS, False,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p')
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'libx264')

    def test_ten_bit_appends_yuv420p10le_and_keeps_libx264(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._PROPS, False,
            tonemapper='reinhard', bit_depth=10)
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p10le')
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'libx264')

    def test_bit_depth_defaults_to_eight(self):
        """Omitting bit_depth entirely must not change the existing 8-bit behavior."""
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._PROPS, False, tonemapper='reinhard')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p')

    def test_twelve_bit_switches_to_libx265_and_yuv420p12le(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._PROPS, False,
            tonemapper='reinhard', bit_depth=12)
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p12le')
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'libx265')

    def test_twelve_bit_never_passes_tune_film(self):
        """'-tune film' is an x264-only tune; libx265 fails to open with it
        (verified against the bundled binary: 'Error setting preset/tune
        veryfast/film'). It must never leak onto the libx265 path."""
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._PROPS, False,
            tonemapper='reinhard', bit_depth=12)
        self.assertNotIn('-tune', cmd)

    def test_twelve_bit_forces_cpu_even_when_gpu_requested(self):
        """No hardware encoder (any vendor) supports 12-bit -- it's a fixed
        silicon limitation, not a driver gap. 12-bit must force the full CPU
        pipeline regardless of the GPU toggle."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, self._PROPS, use_gpu=True,
            tonemapper='reinhard', bit_depth=12)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'libx265')
        self.assertNotIn('-hwaccel', cmd)


class TestHEVCSourcePreservation(unittest.TestCase):
    """When the source is already HEVC, keep the output HEVC too, independent
    of bit depth -- the goal is to preserve the video's characteristics as
    closely as possible and only change what tonemapping requires.
    """

    def setUp(self):
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    _HEVC_PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'hevc',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 120.0, "subtitle_streams": [],
    }

    def test_hevc_source_cpu_path_uses_libx265_at_eight_bit(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 2.2, self._HEVC_PROPS, False,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'libx265')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p')
        self.assertNotIn('-tune', cmd)

    def test_h264_source_cpu_path_still_uses_libx264(self):
        """Non-HEVC sources keep today's default -- this is preservation of
        the source codec, not a blanket switch to HEVC for everyone."""
        manager = ConversionManager()
        props = dict(self._HEVC_PROPS, codec_name='h264')
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, props, False,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'libx264')

    def test_hevc_source_gpu_path_swaps_to_hevc_nvenc_at_eight_bit(self):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 2.2, self._HEVC_PROPS, use_gpu=True,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'hevc_nvenc')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p')

    def test_h264_source_gpu_path_stays_h264_nvenc_at_eight_bit(self):
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        props = dict(self._HEVC_PROPS, codec_name='h264')
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 2.2, props, use_gpu=True,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_nvenc')


class TestHEVCContainerTag(unittest.TestCase):
    """HEVC tracks in MP4/MOV must be tagged 'hvc1': ffmpeg's default sample
    entry is 'hev1', which QuickTime/Apple devices (and some Windows players)
    refuse to recognize even though the stream itself is fine. Matroska has no
    such codec tag, so MKV output must not receive the flag.
    """

    def setUp(self):
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    _PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 120.0, "subtitle_streams": [],
    }
    _HEVC_PROPS = dict(_PROPS, codec_name='hevc')

    @staticmethod
    def _tag_value(cmd):
        return cmd[cmd.index('-tag:v') + 1] if '-tag:v' in cmd else None

    def test_twelve_bit_libx265_mp4_gets_hvc1_tag(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 2.2, self._PROPS, False,
            tonemapper='reinhard', bit_depth=12)
        self.assertEqual(self._tag_value(cmd), 'hvc1')

    def test_hevc_source_eight_bit_mp4_gets_hvc1_tag(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 2.2, self._HEVC_PROPS, False,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(self._tag_value(cmd), 'hvc1')

    def test_hevc_source_mov_gets_hvc1_tag(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mov', 2.2, self._HEVC_PROPS, False,
            tonemapper='reinhard', bit_depth=8)
        self.assertEqual(self._tag_value(cmd), 'hvc1')

    def test_gpu_hevc_encoder_mp4_gets_hvc1_tag(self):
        """The hardware-HEVC swap (10-bit forces hevc_nvenc) produces HEVC in
        MP4 too, so it needs the tag just like the libx265 path."""
        manager = ConversionManager()
        manager._gpu_encoder = 'h264_nvenc'
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 2.2, self._PROPS, use_gpu=True,
            tonemapper='reinhard', bit_depth=10)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'hevc_nvenc')
        self.assertEqual(self._tag_value(cmd), 'hvc1')

    def test_mkv_output_gets_no_tag(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 2.2, self._HEVC_PROPS, False,
            tonemapper='reinhard', bit_depth=12)
        self.assertNotIn('-tag:v', cmd)

    def test_h264_output_mp4_gets_no_tag(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 2.2, self._PROPS, False,
            tonemapper='reinhard', bit_depth=8)
        self.assertNotIn('-tag:v', cmd)


class TestBitDepthHardwareEncoderMapping(unittest.TestCase):
    """10-bit (or higher) output on a hardware encoder must swap to its HEVC
    counterpart.

    None of the vendor H.264 hardware encoders (nvenc/amf/qsv) support 10-bit;
    the HEVC variants do, using the semi-planar p010le pixel format.
    """

    def setUp(self):
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    _BASE_PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000,
        "frame_rate": 30.0, "audio_codec": "aac", "audio_bit_rate": 128000,
        "subtitle_streams": [],
    }

    def _cmd(self, encoder, bit_depth):
        m = ConversionManager()
        m._gpu_encoder = encoder
        return m.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 1.0, self._BASE_PROPS,
            use_gpu=True, tonemapper='reinhard', bit_depth=bit_depth
        )

    def test_nvenc_maps_to_hevc_nvenc_p010le(self):
        cmd = self._cmd('h264_nvenc', bit_depth=10)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'hevc_nvenc')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'p010le')
        # The CUDA decode hwaccel dispatch is keyed on the NVIDIA vendor, not
        # on the H.264-vs-HEVC encode target, so it must still be present.
        self.assertIn('-hwaccel', cmd)
        self.assertIn('cuda', cmd)

    def test_amf_maps_to_hevc_amf_p010le(self):
        cmd = self._cmd('h264_amf', bit_depth=10)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'hevc_amf')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'p010le')

    def test_qsv_maps_to_hevc_qsv_p010le(self):
        cmd = self._cmd('h264_qsv', bit_depth=10)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'hevc_qsv')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'p010le')
        self.assertIn('-hwaccel', cmd)
        self.assertIn('qsv', cmd)

    def test_nvenc_eight_bit_is_unaffected(self):
        """bit_depth=8 must not disturb the existing H.264 hardware path."""
        cmd = self._cmd('h264_nvenc', bit_depth=8)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_nvenc')
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'yuv420p')


class TestBitDepthContainerGuardrail(unittest.TestCase):
    """Defensive validation: some legacy containers can never carry >8-bit video.

    The .m4v profile (Apple's legacy "iPod video" MPEG-4 variant) predates
    HEVC/10-bit entirely and only ever allowed 8-bit H.264 Baseline/Main/High.
    Selecting it with a higher bit depth must be caught before ffmpeg is ever
    launched -- not discovered via a failing subprocess.
    """

    def setUp(self):
        self.manager = ConversionManager()

    def test_rejects_legacy_m4v_with_ten_bit(self):
        error = self.manager.validate_bit_depth_output('C:/out/movie.m4v', 10)
        self.assertIsNotNone(error)
        self.assertIn('m4v', error.lower())

    def test_rejects_legacy_m4v_with_twelve_bit(self):
        error = self.manager.validate_bit_depth_output('C:/out/movie.m4v', 12)
        self.assertIsNotNone(error)
        self.assertIn('m4v', error.lower())

    def test_allows_modern_containers_with_ten_bit(self):
        for path in ('out.mp4', 'out.mkv', 'out.mov'):
            self.assertIsNone(self.manager.validate_bit_depth_output(path, 10),
                              msg=f'{path} should be allowed with 10-bit')

    def test_skips_validation_when_eight_bit_requested(self):
        # An 8-bit .m4v output is perfectly normal; only >8-bit is the problem.
        self.assertIsNone(self.manager.validate_bit_depth_output('legacy.m4v', 8))

    @patch('src.conversion.messagebox.showwarning')
    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.get_video_properties')
    def test_start_conversion_blocks_before_launching_ffmpeg(
            self, mock_get_props, mock_popen, mock_showwarning):
        """The GUI/batch entry point must warn and bail out -- never hand an
        invalid 10-bit + .m4v combination to subprocess.Popen."""
        mock_gui = MagicMock()
        progress_var = MagicMock()

        manager = ConversionManager()
        manager.start_conversion(
            'input.mp4', 'output.m4v', 2.2, False,
            progress_var, [], mock_gui, False, MagicMock(), bit_depth=10)

        mock_showwarning.assert_called_once()
        mock_popen.assert_not_called()
        mock_get_props.assert_not_called()  # bail out before even probing the file
        self.assertIsNone(manager.process)


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


class TestLibplaceboCommandConstruction(unittest.TestCase):
    """The GPU tonemap path: libplacebo replaces the CPU zscale/tonemap chain."""

    _PROPS = {
        "width": 2560, "height": 1440, "bit_rate": 8000000,
        "frame_rate": 60.0, "audio_codec": "aac", "audio_bit_rate": 128000,
        "duration": 60.0, "subtitle_streams": [],
    }

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=False)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_libplacebo_plain_vulkan_fallback(self, _avail, _interop):
        """When CUDA interop is unavailable, use the plain Vulkan path (CPU decode)."""
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = m.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 2.2, self._PROPS,
            use_gpu=True, tonemapper='Hable',
        )
        joined = ' '.join(cmd)
        self.assertIn('-init_hw_device', cmd)
        self.assertIn('vulkan=vk:0', cmd)
        self.assertIn('libplacebo', joined)
        self.assertIn('tonemapping=hable', joined)
        self.assertIn('peak_detect=1', joined)
        self.assertIn('eq=gamma=2.2', joined)
        self.assertIn('format=p010,hwupload', joined)   # CPU-decode upload prefix
        self.assertNotIn('cuda=cu:0', joined)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_nvenc')

    @patch('src.conversion.vulkan_libplacebo_available', return_value=False)
    def test_cpu_path_when_probe_false(self, _avail):
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='reinhard'))
        self.assertNotIn('libplacebo', cmd)
        self.assertIn('zscale', cmd)

    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_cpu_path_when_gpu_toggle_off(self, _avail):
        m = ConversionManager()
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 1.0, self._PROPS,
            use_gpu=False, tonemapper='reinhard'))
        self.assertNotIn('libplacebo', cmd)
        self.assertIn('zscale', cmd)
        self.assertIn('libx264', cmd)


class TestCudaVulkanInteropPath(unittest.TestCase):
    """NVIDIA-specific fast path: NVDEC decode → Vulkan hwmap → libplacebo → NVENC."""

    _PROPS = {
        "width": 3840, "height": 2160, "bit_rate": 40_000_000,
        "frame_rate": 24.0, "audio_codec": "aac", "audio_bit_rate": 192000,
        "duration": 120.0, "subtitle_streams": [],
    }

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=True)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_cuda_interop_path_used_for_nvenc(self, _avail, _interop):
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = m.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='mobius',
        )
        joined = ' '.join(cmd)
        self.assertIn('cuda=cu:0', joined)
        self.assertIn('vulkan=vk@cu', joined)
        self.assertIn('-hwaccel', cmd)
        self.assertIn('-hwaccel_output_format', cmd)
        self.assertIn('hwmap=derive_device=vulkan', joined)
        self.assertNotIn('format=p010,hwupload', joined)
        self.assertNotIn('vulkan=vk:0', joined)
        self.assertEqual(cmd[cmd.index('-c:v') + 1], 'h264_nvenc')

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=False)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_falls_back_to_plain_vulkan_when_interop_unavailable(self, _avail, _interop):
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = m.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='reinhard',
        )
        joined = ' '.join(cmd)
        self.assertIn('vulkan=vk:0', joined)
        self.assertNotIn('vulkan=vk@cu', joined)
        self.assertNotIn('cuda=cu:0', joined)
        self.assertIn('format=p010,hwupload', joined)

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=True)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_interop_not_used_for_amf(self, _avail, _interop):
        """AMF (AMD) has no CUDA interop — falls back to plain Vulkan."""
        m = ConversionManager()
        m._gpu_encoder = 'h264_amf'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='reinhard',
        ))
        self.assertNotIn('cuda=cu:0', cmd)
        self.assertIn('vulkan=vk:0', cmd)
        self.assertIn('format=p010,hwupload', cmd)

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=True)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_interop_not_used_for_qsv(self, _avail, _interop):
        """QSV (Intel) has no CUDA interop — falls back to plain Vulkan."""
        m = ConversionManager()
        m._gpu_encoder = 'h264_qsv'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='reinhard',
        ))
        self.assertNotIn('cuda=cu:0', cmd)
        self.assertIn('vulkan=vk:0', cmd)

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=True)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_interop_not_used_when_gpu_disabled(self, _avail, _interop):
        m = ConversionManager()
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 1.0, self._PROPS,
            use_gpu=False, tonemapper='reinhard',
        ))
        self.assertNotIn('cuda=cu:0', cmd)
        self.assertIn('zscale', cmd)

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=True)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_libplacebo_and_gamma_still_correct_on_interop_path(self, _avail, _interop):
        """libplacebo tonemapping and gamma are both present on the interop path."""
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 2.2, self._PROPS,
            use_gpu=True, tonemapper='Hable',
        ))
        self.assertIn('tonemapping=hable', cmd)
        self.assertIn('peak_detect=1', cmd)
        self.assertIn('eq=gamma=2.2', cmd)


# ---------------------------------------------------------------------------
# Issue #2 — Concurrency: stable process reference in monitor_progress
# ---------------------------------------------------------------------------

class TestMonitorProgressCancellationRace(unittest.TestCase):
    """monitor_progress must not raise AttributeError when self.process is cleared.

    cancel_conversion sets self.process = None on the main thread.  The worker
    thread running monitor_progress can observe this at two moments:

    (a) mid-iteration  — handled today by the 'if self.process is None: return'
        guard inside the for-loop body.  This case is already GREEN.

    (b) between the loop ending and self.process.wait() / self.process.returncode
        being read — NOT protected today.  self.process.wait() executes, then
        self.process is None, so self.process.returncode raises AttributeError.
        This case is RED today and turns GREEN once a stable local reference
        (proc = self.process) is captured at worker-thread entry.
    """

    def _make_gui(self) -> MagicMock:
        gui = MagicMock()
        # Leave root.after as a plain MagicMock so _handle callbacks are recorded
        # but never executed — we only care that monitor_progress itself doesn't crash.
        return gui

    def test_process_nulled_between_loop_end_and_wait(self) -> None:
        """Race (b): AttributeError must not occur when self.process is cleared
        inside .wait() before returncode is read.

        RED today  — self.process.returncode follows self.process.wait() without
                     re-checking; if wait() clears the reference, the next line crashes.
        GREEN after fix — a local `proc = self.process` reference outlives the clear.
        """
        manager = ConversionManager()
        manager.cancelled = False
        manager.use_gpu = False

        class RacyProcess:
            """Process whose .wait() mimics cancel_conversion clearing the reference."""
            stderr = iter([])  # empty → the for-loop exits immediately
            returncode = 0

            def wait(inner_self) -> None:
                # Simulate the main thread running cancel_conversion concurrently:
                # the shared attribute is yanked right after the loop but before
                # returncode is read.
                manager.process = None

        manager.process = RacyProcess()

        try:
            manager.monitor_progress(
                MagicMock(), 10.0, self._make_gui(), [],
                MagicMock(), 'out.mkv', False, 2.2,
            )
        except AttributeError as exc:
            self.fail(
                f"monitor_progress raised AttributeError when self.process was "
                f"cleared inside .wait() before returncode was read: {exc}"
            )

    def test_process_nulled_mid_iteration_does_not_crash(self) -> None:
        """Race (a): clearing self.process between two yielded stderr lines must
        not crash (already GREEN — the existing in-loop None-guard covers this).

        Included here as an isolation regression test so any future refactor that
        removes or moves the guard becomes immediately visible.
        """
        manager = ConversionManager()
        manager.cancelled = False
        manager.use_gpu = False

        def make_stderr():  # type: ignore[return]
            yield 'frame=1 fps=30 time=00:00:01.00\n'
            manager.process = None  # concurrent cancel clears the reference mid-stream

        mock_proc = MagicMock()
        mock_proc.stderr = make_stderr()
        mock_proc.returncode = 0
        manager.process = mock_proc

        try:
            manager.monitor_progress(
                MagicMock(), 10.0, self._make_gui(), [],
                MagicMock(), 'out.mkv', False, 2.2,
            )
        except AttributeError as exc:
            self.fail(
                f"monitor_progress raised AttributeError when self.process was "
                f"cleared mid-iteration: {exc}"
            )


class TestConstructCommandNoFilterIndex(unittest.TestCase):
    """After removing Static, construct_ffmpeg_command takes no selected_filter_index."""

    _PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000,
        "codec_name": "h264", "frame_rate": 30.0,
        "audio_codec": "aac", "audio_bit_rate": 128000,
        "duration": 90.0, "subtitle_streams": [],
    }

    def test_construct_ffmpeg_command_no_filter_index_uses_npl_100(self):
        """CPU path without selected_filter_index always emits npl=100."""
        m = ConversionManager()
        cmd = m.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 1.0, self._PROPS,
            use_gpu=False, tonemapper='reinhard',
        )
        joined = ' '.join(cmd)
        self.assertIn('npl=100', joined)
        self.assertNotIn('Static', joined)

    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_libplacebo_path_no_filter_index_always_peak_detect_1(self, _avail):
        """GPU path without selected_filter_index always uses peak_detect=1."""
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='reinhard',
        ))
        self.assertIn('peak_detect=1', cmd)


if __name__ == '__main__':
    unittest.main()

