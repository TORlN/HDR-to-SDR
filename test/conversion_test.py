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
from src.utils import FFMPEG_CONVERT_FILTER, get_lut_filter_path
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

        expected_filter = FFMPEG_CONVERT_FILTER.format(
            gamma=2.2, tonemapper=tonemapper, lut_path=get_lut_filter_path())
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
        error_messages = []

        # Create a mock GUI instance with a 'root' attribute
        mock_gui = MagicMock()
        mock_gui.root = MagicMock()
        mock_gui.root.after = MagicMock(side_effect=lambda delay, func: func())

        cancel_button = MagicMock()
        cancel_button.grid_remove = MagicMock()

        with patch.object(manager, 'enable_ui') as mock_enable_ui:
            manager.handle_completion(
                mock_gui, [], cancel_button, 'output.mkv', True, error_messages, 0
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
                mock_gui, [], cancel_button, 'output.mkv', False, error_messages, 1
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
                mock_gui, [], cancel_button, 'out.mkv', False, error_messages, 1
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

    @patch('src.conversion.messagebox.showwarning')
    def test_verify_paths_rejects_same_input_and_output(self, mock_showwarning):
        """Output path must not resolve to the same file as the input -- ffmpeg
        would read and overwrite the source simultaneously, corrupting it."""
        manager = ConversionManager()
        self.assertFalse(manager.verify_paths('video.mp4', 'video.mp4'))
        mock_showwarning.assert_called_once_with(
            "Warning", "Input and output file cannot be the same."
        )

        mock_showwarning.reset_mock()
        self.assertFalse(manager.verify_paths('./video.mp4', 'video.mp4'))
        mock_showwarning.assert_called_once_with(
            "Warning", "Input and output file cannot be the same."
        )

    @unittest.skipUnless(sys.platform == 'win32', "NTFS case-insensitivity is Windows-only")
    @patch('src.conversion.messagebox.showwarning')
    def test_verify_paths_rejects_case_variant_of_same_file(self, mock_showwarning):
        """NTFS is case-insensitive, so 'movie.mp4' and 'Movie.mp4' are the
        same file on disk. os.path.abspath alone doesn't normalize case, so
        without normcase this guard would pass and ffmpeg (-y) would read and
        write the same file at once, corrupting the source."""
        manager = ConversionManager()
        self.assertFalse(manager.verify_paths('C:/videos/movie.mp4', 'C:/videos/Movie.mp4'))
        mock_showwarning.assert_called_once_with(
            "Warning", "Input and output file cannot be the same."
        )

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

    def test_enable_ui_restores_comboboxes_to_readonly_not_normal(self):
        """format_combobox/quality_mode_combobox are built with
        state='readonly' specifically so users can't type into them --
        format_var.get() flows straight into the output filename. Restoring
        them to 'normal' on conversion completion would make that string
        user-editable and let a typo become the literal output extension."""
        combobox = MagicMock(spec=ttk.Combobox)
        button = MagicMock(spec=ttk.Button)
        manager = ConversionManager()

        manager.enable_ui([combobox, button])

        combobox.config.assert_called_once_with(state="readonly")
        button.config.assert_called_once_with(state="normal")

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

    @unittest.skipUnless(sys.platform == 'win32', "Windows platform required")
    @patch('src.conversion.subprocess.Popen')
    @patch('src.utils.subprocess.STARTUPINFO')
    def test_start_ffmpeg_process_windows(self, mock_startupinfo_cls, mock_popen):
        """Test start_ffmpeg_process on Windows platforms."""
        startupinfo_instance = MagicMock()
        startupinfo_instance.dwFlags = 0
        mock_startupinfo_cls.return_value = startupinfo_instance

        with patch('sys.platform', 'win32'):
            manager = ConversionManager()
            cmd = ['ffmpeg', '-i', 'input.mp4', 'output.mkv']
            mock_popen.return_value = MagicMock()

            manager.start_ffmpeg_process(cmd)
            mock_popen.assert_called_once_with(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                universal_newlines=True,
                startupinfo=startupinfo_instance,
                encoding='utf-8',
                errors='replace',
                creationflags=ANY,
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

        expected_filter = FFMPEG_CONVERT_FILTER.format(
            gamma=gamma, tonemapper=tonemapper, lut_path=get_lut_filter_path())
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
        manager._on_complete = MagicMock()
        manager.handle_completion(self._gui(), [], MagicMock(), 'out.mkv', False, [], 0)
        manager._on_complete.assert_called_once_with(True)
        mock_mb.showinfo.assert_not_called()  # no per-file success dialog

    @patch('src.conversion.messagebox')
    def test_on_complete_called_with_false_on_failure(self, mock_mb):
        manager = ConversionManager()
        manager.cancelled = False
        manager._on_complete = MagicMock()
        manager.handle_completion(self._gui(), [], MagicMock(), 'out.mkv', False, ['err'], 1)
        manager._on_complete.assert_called_once_with(False)
        mock_mb.showerror.assert_not_called()  # no per-file error dialog

    @patch('src.conversion.messagebox')
    def test_on_complete_does_not_enable_ui_between_files(self, mock_mb):
        # Between queued files the UI must stay disabled and the cancel button shown.
        manager = ConversionManager()
        manager._on_complete = MagicMock()
        cancel = MagicMock()
        with patch.object(manager, 'enable_ui') as mock_enable:
            manager.handle_completion(self._gui(), ['e'], cancel, 'out.mkv', False, [], 0)
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


class TestStartConversionSignalsFailureOnEarlyReturn(unittest.TestCase):
    """Every guard in start_conversion that bails out before launching ffmpeg
    must still report failure to the caller: return False, and -- when an
    on_complete callback was supplied (the batch/queue path) -- call it with
    False. Without this, a batch item that hits one of these guards is left
    stuck at 'Converting' forever (nothing ever advances the queue), and a
    single-file caller has no way to tell the attempt never started.

    In batch mode these guards must also not pop a blocking messagebox --
    _start_next_batch_item does no pre-validation of its own, so any of
    these guards is reachable mid-unattended-batch-run; a modal dialog
    nobody's watching for stalls the whole queue until someone clicks it,
    defeating "queue it and walk away" semantics. Interactive (single-file)
    mode keeps the dialog, since a human is right there to see it."""

    _PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000, "codec_name": 'h264',
        "frame_rate": 30.0, "audio_codec": 'aac', "audio_bit_rate": 128000,
        "duration": 120.0, "subtitle_streams": [],
    }

    @patch('src.conversion.messagebox.showwarning')
    def test_invalid_paths_signals_failure(self, mock_warn):
        manager = ConversionManager()
        done = MagicMock()
        result = manager.start_conversion(
            '', 'output.mkv', 2.2, False, MagicMock(), [], MagicMock(),
            False, MagicMock(), on_complete=done)
        self.assertFalse(result)
        done.assert_called_once_with(False)
        mock_warn.assert_not_called()  # batch mode: no blocking dialog

    @patch('src.conversion.messagebox.showwarning')
    def test_bit_depth_incompatibility_signals_failure(self, mock_warn):
        manager = ConversionManager()
        done = MagicMock()
        result = manager.start_conversion(
            'in.mp4', 'out.m4v', 2.2, False, MagicMock(), [], MagicMock(),
            False, MagicMock(), bit_depth=10, on_complete=done)
        self.assertFalse(result)
        done.assert_called_once_with(False)
        mock_warn.assert_not_called()  # batch mode: no blocking dialog

    @patch('src.conversion.messagebox.showwarning')
    @patch('src.conversion.get_video_properties', return_value=None)
    def test_missing_properties_signals_failure(self, mock_props, mock_warn):
        manager = ConversionManager()
        done = MagicMock()
        result = manager.start_conversion(
            'in.mp4', 'out.mkv', 2.2, False, MagicMock(), [], MagicMock(),
            False, MagicMock(), on_complete=done)
        self.assertFalse(result)
        done.assert_called_once_with(False)
        mock_warn.assert_not_called()  # batch mode: no blocking dialog

    @patch('src.conversion.messagebox.showwarning')
    @patch('src.conversion.get_video_properties')
    def test_zero_duration_signals_failure(self, mock_props, mock_warn):
        mock_props.return_value = dict(self._PROPS, duration=0)
        manager = ConversionManager()
        done = MagicMock()
        result = manager.start_conversion(
            'in.mp4', 'out.mkv', 2.2, False, MagicMock(), [], MagicMock(),
            False, MagicMock(), on_complete=done)
        self.assertFalse(result)
        done.assert_called_once_with(False)
        mock_warn.assert_not_called()  # batch mode: no blocking dialog

    @patch('src.conversion.messagebox.showwarning')
    def test_early_return_without_on_complete_does_not_crash(self, mock_warn):
        """The single-file path passes no on_complete -- the None default
        must not be called, and (unlike batch mode) the dialog still shows
        since a human is present to see it."""
        manager = ConversionManager()
        result = manager.start_conversion(
            '', 'output.mkv', 2.2, False, MagicMock(), [], MagicMock(),
            False, MagicMock())
        self.assertFalse(result)
        mock_warn.assert_called_once()

    @patch('src.conversion.subprocess.Popen')
    @patch('src.conversion.get_video_properties')
    def test_successful_launch_returns_true(self, mock_props, mock_popen):
        mock_props.return_value = dict(self._PROPS)
        proc = MagicMock()
        proc.stderr = iter([])
        mock_popen.return_value = proc
        mock_gui = MagicMock()
        mock_gui.root.after = MagicMock()
        manager = ConversionManager()
        result = manager.start_conversion(
            'in.mp4', 'out.mkv', 2.2, False, MagicMock(), [], mock_gui,
            False, MagicMock())
        self.assertTrue(result)


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

    def test_is_gpu_available_uses_cached_encoder_without_reprobing(self):
        """Once detect_gpu_encoder has run, is_gpu_available must reuse the
        cached self._gpu_encoder instead of re-spawning nvidia-smi/ffmpeg on
        every call (e.g. every GPU-accel checkbox toggle)."""
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        with patch.object(m, 'detect_gpu_encoder') as mock_detect:
            self.assertTrue(m.is_gpu_available())
            mock_detect.assert_not_called()


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

    @patch('src.conversion.get_video_properties', return_value={'duration': 10.0})
    def test_construct_ffmpeg_command_failure_reenables_ui(self, mock_get_props):
        """If construct_ffmpeg_command raises after the UI has already been
        disabled and the cancel button gridded (e.g. the GPU-only-tonemapper
        safety net firing), the single-file path must re-enable the UI and
        hide the cancel button before letting the exception propagate --
        otherwise every control is permanently stuck disabled with no way to
        recover short of restarting the app."""
        manager = ConversionManager()
        mock_gui = MagicMock()
        progress_var = MagicMock()
        cancel_button = MagicMock()
        elements = [MagicMock(), MagicMock()]

        with patch.object(manager, 'construct_ffmpeg_command',
                           side_effect=ValueError('boom')):
            with self.assertRaises(ValueError):
                manager.start_conversion(
                    'in.mp4', 'out.mp4', 1.0, False,
                    progress_var, elements, mock_gui, False, cancel_button)

        for el in elements:
            el.config.assert_any_call(state='normal')
        cancel_button.grid_remove.assert_called_once()


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


class TestGpuOnlyTonemapperSafetyNet(unittest.TestCase):
    """BT.2390/Spline have no CPU implementation. If per-item settings force
    the CPU branch anyway (e.g. 12-bit output, which always forces CPU
    regardless of the GPU toggle), construct_ffmpeg_command must raise a
    clear error instead of building an invalid zscale filter string."""

    _PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000,
        "frame_rate": 24.0, "audio_codec": "aac", "audio_bit_rate": 128000,
        "duration": 30.0, "subtitle_streams": [], "codec_name": "hevc",
    }

    def test_raises_when_forced_cpu_with_gpu_only_tonemapper(self):
        m = ConversionManager()
        with self.assertRaises(ValueError) as ctx:
            m.construct_ffmpeg_command(
                'in.mp4', 'out.mkv', 1.0, self._PROPS,
                use_gpu=True, tonemapper='BT.2390', bit_depth=12,
            )
        message = str(ctx.exception)
        self.assertIn('bt.2390', message)
        self.assertIn('GPU', message)

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=False)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_gpu_path_bt2390_unaffected(self, _avail, _interop):
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='BT.2390'))
        self.assertIn('tonemapping=bt.2390', cmd)

    @patch('src.conversion.vulkan_cuda_interop_available', return_value=False)
    @patch('src.conversion.vulkan_libplacebo_available', return_value=True)
    def test_gpu_path_spline_unaffected(self, _avail, _interop):
        m = ConversionManager()
        m._gpu_encoder = 'h264_nvenc'
        cmd = ' '.join(m.construct_ffmpeg_command(
            'in.mp4', 'out.mp4', 1.0, self._PROPS,
            use_gpu=True, tonemapper='Spline'))
        self.assertIn('tonemapping=spline', cmd)


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

            def wait(self) -> None:
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

    def test_success_survives_process_nulled_before_handle_runs(self) -> None:
        """A third race, deeper than (a)/(b) above: monitor_progress captures
        `proc` locally and no longer crashes, but handle_completion's
        after(0)-scheduled _handle still re-reads self.process.returncode
        independently -- not the value monitor_progress already computed.
        If cancel_conversion (main thread) sets self.process = None in the
        window between monitor_progress finishing and _handle actually
        running, a conversion that succeeded (returncode 0) gets reported
        as failed/cancelled.

        RED today   — _handle reads self.process.returncode, sees None,
                      reports success=False.
        GREEN after — handle_completion receives monitor_progress's already
                      -captured returncode and never touches self.process.
        """
        manager = ConversionManager()
        manager.cancelled = False
        manager.use_gpu = False
        manager._on_complete = MagicMock()

        mock_proc = MagicMock()
        mock_proc.stderr = iter([])  # empty -> loop exits immediately
        mock_proc.returncode = 0
        manager.process = mock_proc

        gui = MagicMock()
        captured = {}
        gui.root.after = MagicMock(
            side_effect=lambda delay, func: captured.setdefault('cb', func))

        with patch('src.conversion.messagebox'):
            manager.monitor_progress(
                MagicMock(), 10.0, gui, [], MagicMock(), 'out.mkv', False, 2.2)
            # cancel_conversion races in right after monitor_progress decided
            # the process succeeded, but before the scheduled handler runs.
            manager.process = None
            captured['cb']()

        manager._on_complete.assert_called_once_with(True)


class TestGpuErrorDetectionFalsePositive(unittest.TestCase):
    """gpu_error_detected must not fire just because the input/output filename
    happens to contain a GPU-vendor substring -- ffmpeg echoes both paths
    verbatim in its banner lines ("Input #0, ..., from '<path>':"), so a file
    named e.g. 'cuda_test.mp4' or 'amf_demo.mkv' must not make an unrelated
    failure (bad codec, full disk) get misdiagnosed as a GPU error and
    silently retried on CPU, doubling the work and hiding the real cause."""

    def _gui(self):
        gui = MagicMock()
        gui.root.after = MagicMock(side_effect=lambda delay, func: func())
        return gui

    def _run(self, stderr_lines, returncode=1):
        manager = ConversionManager()
        manager.cancelled = False
        manager.use_gpu = True
        mock_proc = MagicMock()
        mock_proc.stderr = iter(stderr_lines)
        mock_proc.returncode = returncode
        manager.process = mock_proc
        gui = self._gui()
        # The false-positive cases fall through to the real handle_completion
        # (correctly showing the genuine error instead of retrying) -- mock
        # messagebox so that doesn't pop a real, click-blocking dialog here.
        with patch.object(manager, '_retry_with_cpu') as mock_retry, \
             patch('src.conversion.messagebox'):
            manager.monitor_progress(
                MagicMock(), 10.0, gui, [], MagicMock(), 'out.mkv', False, 2.2)
        return mock_retry

    def test_gpu_keyword_in_input_banner_line_does_not_trigger_retry(self):
        mock_retry = self._run([
            "Input #0, mov,mp4,m4a, from 'cuda_test.mp4':\n",
            "Error: codec not found\n",
        ])
        mock_retry.assert_not_called()

    def test_gpu_keyword_in_output_banner_line_does_not_trigger_retry(self):
        mock_retry = self._run([
            "Output #0, mp4, to 'amf_demo_out.mp4':\n",
            "Error: disk full\n",
        ])
        mock_retry.assert_not_called()

    def test_genuine_gpu_failure_line_still_triggers_retry(self):
        mock_retry = self._run([
            "Input #0, mov,mp4,m4a, from 'movie.mp4':\n",
            "Cannot load nvcuda.dll\n",
        ])
        mock_retry.assert_called_once()


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


class TestDolbyVisionTierCommands(unittest.TestCase):
    """License-tier guardrails for Dolby Vision sources.

    Pro: audio passes through completely untouched (direct stream copy, every
    audio stream mapped, no channel manipulation). Free: audio is explicitly
    restricted to the first stream and downmixed to 2-channel stereo AAC.
    Non-DoVi sources keep the pre-existing behavior on both tiers.
    """

    def setUp(self):
        # Pin the GPU tonemap probe off; the profile-5 tests below re-patch it
        # on explicitly where the RPU-aware libplacebo path is the subject.
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _dovi_props(profile=8, audio='truehd'):
        return {
            "width": 3840, "height": 2160, "bit_rate": 20000000,
            "codec_name": 'hevc', "frame_rate": 24.0, "duration": 600.0,
            "audio_codec": audio, "audio_bit_rate": 3000000,
            "subtitle_streams": [], "bit_depth": 10,
            "is_dolby_vision": True, "dovi_profile": profile,
        }

    # ── Audio tier split ────────────────────────────────────────────────────

    def test_pro_dovi_audio_is_untouched_copy(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, self._dovi_props(), False,
            tonemapper='reinhard', licensed=True)
        self.assertEqual(cmd[cmd.index('-c:a') + 1], 'copy')
        self.assertIn('0:a?', cmd)     # every audio stream mapped
        self.assertNotIn('-ac', cmd)   # multi-channel layout untouched
        self.assertNotIn('aac', cmd)   # nothing transcoded

    def test_free_dovi_audio_forces_two_channel_stereo(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, self._dovi_props(), False,
            tonemapper='reinhard', licensed=False)
        self.assertEqual(cmd[cmd.index('-c:a') + 1], 'aac')
        self.assertEqual(cmd[cmd.index('-ac') + 1], '2')

    def test_free_dovi_audio_restricted_to_first_stream(self):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, self._dovi_props(), False,
            tonemapper='reinhard', licensed=False)
        self.assertIn('0:a:0?', cmd)
        self.assertNotIn('0:a?', cmd)

    def test_licensed_flag_defaults_to_free_tier(self):
        """Omitting licensed= must behave as Free — the restrictive default is
        the safe one for a premium gate."""
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, self._dovi_props(), False,
            tonemapper='reinhard')
        self.assertEqual(cmd[cmd.index('-c:a') + 1], 'aac')
        self.assertEqual(cmd[cmd.index('-ac') + 1], '2')

    def test_free_non_dovi_source_keeps_full_copy_passthrough(self):
        """The stereo restriction is a DoVi-conversion rule only: a plain
        HDR10 file on the Free tier keeps the existing copy-everything path."""
        manager = ConversionManager()
        props = self._dovi_props()
        props['is_dolby_vision'] = False
        props['dovi_profile'] = None
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, props, False,
            tonemapper='reinhard', licensed=False)
        self.assertEqual(cmd[cmd.index('-c:a') + 1], 'copy')
        self.assertIn('0:a?', cmd)
        self.assertNotIn('-ac', cmd)

    def test_pro_dovi_mp4_transcodes_incompatible_audio_without_downmix(self):
        """MP4 can't hold TrueHD, so Pro passthrough must still fall back to
        the container-mandated AAC transcode — but with the full channel
        layout preserved (no -ac), unlike the Free downmix."""
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mp4', 1.0, self._dovi_props(audio='truehd'), False,
            tonemapper='reinhard', licensed=True)
        self.assertEqual(cmd[cmd.index('-c:a') + 1], 'aac')
        self.assertNotIn('-ac', cmd)
        self.assertIn('0:a?', cmd)

    # ── Video: RPU-aware tonemapping per DoVi profile ───────────────────────

    def test_dovi_profile5_uses_rpu_aware_libplacebo_even_on_cpu(self):
        """Profile 5 (IPTPQc2) has no HDR10-compatible base layer — the zscale
        chain would render wrong colors. When libplacebo is available it must
        be used (it applies the DoVi RPU) even with the GPU toggle off."""
        manager = ConversionManager()
        with patch('src.conversion.vulkan_libplacebo_available', return_value=True):
            cmd = manager.construct_ffmpeg_command(
                'in.mkv', 'out.mkv', 1.0, self._dovi_props(profile=5), False,
                tonemapper='reinhard', licensed=True)
        fc = cmd[cmd.index('-filter_complex') + 1]
        self.assertIn('libplacebo', fc)
        self.assertIn('-init_hw_device', cmd)   # Vulkan device for the filter
        self.assertIn('libx265', cmd)           # encode itself stays on CPU

    @patch('src.conversion.messagebox.showwarning')
    def test_dovi_profile5_without_libplacebo_falls_back_to_cpu_chain(self, mock_warn):
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, self._dovi_props(profile=5), False,
            tonemapper='reinhard', licensed=True)
        fc = cmd[cmd.index('-filter_complex') + 1]
        self.assertIn('zscale', fc)
        self.assertNotIn('libplacebo', fc)

    @patch('src.conversion.messagebox.showwarning')
    def test_dovi_profile5_without_libplacebo_warns_about_wrong_colors(self, mock_warn):
        """The zscale fallback for profile 5 has no RPU applied and renders
        wrong colors (green/purple cast per the code's own comment) -- the
        conversion must not exit silently with no indication anything is off."""
        manager = ConversionManager()
        manager.construct_ffmpeg_command(
            'in.mkv', 'out.mkv', 1.0, self._dovi_props(profile=5), False,
            tonemapper='reinhard', licensed=True)
        mock_warn.assert_called_once()
        warning_text = mock_warn.call_args[0][1].lower()
        self.assertIn('dolby vision', warning_text)

    @patch('src.conversion.messagebox.showwarning')
    def test_dovi_profile5_with_libplacebo_does_not_warn(self, mock_warn):
        """When libplacebo IS available, the RPU is correctly applied --
        no warning should fire."""
        manager = ConversionManager()
        with patch('src.conversion.vulkan_libplacebo_available', return_value=True):
            manager.construct_ffmpeg_command(
                'in.mkv', 'out.mkv', 1.0, self._dovi_props(profile=5), False,
                tonemapper='reinhard', licensed=True)
        mock_warn.assert_not_called()

    def test_dovi_profile8_keeps_standard_hdr10_cpu_chain(self):
        """Profiles 7/8 carry an HDR10-compatible base layer, so the existing
        tonemap chain is already correct — the GPU toggle alone decides
        whether libplacebo runs."""
        manager = ConversionManager()
        with patch('src.conversion.vulkan_libplacebo_available', return_value=True):
            cmd = manager.construct_ffmpeg_command(
                'in.mkv', 'out.mkv', 1.0, self._dovi_props(profile=8), False,
                tonemapper='reinhard', licensed=True)
        fc = cmd[cmd.index('-filter_complex') + 1]
        self.assertIn('zscale', fc)
        self.assertNotIn('libplacebo', fc)

    # ── licensed flag plumbing ──────────────────────────────────────────────

    @patch('src.conversion.get_video_properties')
    def test_start_conversion_threads_licensed_flag(self, mock_props):
        mock_props.return_value = self._dovi_props()
        manager = ConversionManager()
        mock_gui = MagicMock()
        with patch.object(manager, 'construct_ffmpeg_command',
                          return_value=['ffmpeg']) as mock_build, \
             patch.object(manager, 'start_ffmpeg_process', return_value=MagicMock()), \
             patch.object(manager, 'monitor_progress'):
            manager.start_conversion(
                'in.mkv', 'out.mkv', 1.0, False, MagicMock(), [], mock_gui,
                False, MagicMock(), licensed=True)
        self.assertIs(mock_build.call_args.kwargs.get('licensed'), True)

    def test_cpu_retry_preserves_licensed_flag(self):
        """A GPU-failure retry must not silently demote a Pro user to the
        Free stereo downmix."""
        manager = ConversionManager()
        manager._licensed = True  # as remembered by start_conversion(licensed=True)
        mock_gui = MagicMock()
        with patch.object(manager, 'start_conversion') as mock_start, \
             patch('src.conversion.messagebox.showwarning'):
            manager._retry_with_cpu(mock_gui, [], MagicMock(), MagicMock(),
                                    False, 1.0, 'reinhard')
        self.assertIs(mock_start.call_args.kwargs.get('licensed'), True)

    def test_start_conversion_threads_quality_mode(self):
        manager = ConversionManager()
        mock_gui = MagicMock()
        with patch('src.conversion.get_video_properties',
                   return_value={'duration': 10.0, 'bit_rate': 4000000}), \
             patch.object(manager, 'construct_ffmpeg_command',
                          return_value=['ffmpeg']) as mock_build, \
             patch.object(manager, 'start_ffmpeg_process', return_value=MagicMock()), \
             patch.object(manager, 'monitor_progress'):
            manager.start_conversion(
                'in.mkv', 'out.mkv', 1.0, False, MagicMock(), [], mock_gui,
                False, MagicMock(), quality=30000, quality_mode='bitrate')
        self.assertEqual(mock_build.call_args.kwargs.get('quality_mode'), 'bitrate')
        self.assertEqual(manager._quality_mode, 'bitrate')

    def test_cpu_retry_preserves_quality_mode(self):
        """A GPU-failure retry must keep targeting the same bitrate/CQ mode,
        not silently fall back to Constant Quality."""
        manager = ConversionManager()
        manager._quality_mode = 'bitrate'  # as remembered by start_conversion(quality_mode='bitrate')
        manager._quality = 30000
        mock_gui = MagicMock()
        with patch.object(manager, 'start_conversion') as mock_start, \
             patch('src.conversion.messagebox.showwarning'):
            manager._retry_with_cpu(mock_gui, [], MagicMock(), MagicMock(),
                                    False, 1.0, 'reinhard')
        self.assertEqual(mock_start.call_args.kwargs.get('quality_mode'), 'bitrate')
        self.assertEqual(mock_start.call_args.kwargs.get('quality'), 30000)

    def test_cpu_retry_calls_on_complete_when_construct_ffmpeg_command_raises(self):
        """A CPU retry that still can't build a command (e.g. a GPU-only
        tonemapper with no CPU implementation) must still call on_complete,
        or the batch item is stuck at 'Converting' forever -- unlike a first
        (non-retry) attempt, nothing else in this call path (there's no
        surrounding try/except the way _start_next_batch_item wraps its own
        call to start_conversion) catches the exception."""
        manager = ConversionManager()
        manager._on_complete = MagicMock()
        mock_gui = MagicMock()
        mock_gui.input_path_var.get.return_value = 'in.mkv'
        mock_gui.output_path_var.get.return_value = 'out.mkv'
        with patch.object(manager, 'construct_ffmpeg_command',
                          side_effect=ValueError('bt.2390 requires GPU tonemapping')), \
             patch('src.conversion.get_video_properties', return_value={'duration': 10.0}), \
             patch('src.conversion.messagebox'):
            manager._retry_with_cpu(mock_gui, [], MagicMock(), MagicMock(),
                                    False, 1.0, 'bt.2390')
        manager._on_complete.assert_called_once_with(False)

    def test_cpu_retry_writes_back_gpu_accel_off_to_current_batch_item(self):
        """gpu_accel_var.set(False) alone doesn't persist -- it's a raw
        Variable.set(), which doesn't fire the checkbutton's command= callback
        that normally triggers a settings write-back. Without an explicit
        write-back here, reselecting the batch item this retry ran for would
        restore the stale (pre-failure) gpu_accel=True and silently re-enable
        GPU on a file that just proved it fails on GPU."""
        manager = ConversionManager()
        mock_gui = MagicMock()
        with patch.object(manager, 'start_conversion'), \
             patch('src.conversion.messagebox.showwarning'):
            manager._retry_with_cpu(mock_gui, [], MagicMock(), MagicMock(),
                                    False, 1.0, 'reinhard')
        mock_gui._write_back_current_settings.assert_called_once()


class TestBitrateModeCommandConstruction(unittest.TestCase):
    """quality_mode='bitrate' switches every encoder from its constant-quality
    flag (-cq/-qp_*/-global_quality/-crf) to target-average-capped-burst
    bitrate flags: -b:v=T, -maxrate=1.5xT, -bufsize=2xT, where T = quality(kbps)*1000."""

    def setUp(self):
        patcher = patch('src.conversion.vulkan_libplacebo_available', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    _BASE_PROPS = {
        "width": 1920, "height": 1080, "bit_rate": 4000000,
        "frame_rate": 30.0, "audio_codec": "aac", "audio_bit_rate": 128000,
        "subtitle_streams": [],
    }

    def _cmd(self, encoder=None, use_gpu=False, quality=20000, quality_mode='bitrate'):
        m = ConversionManager()
        if encoder:
            m._gpu_encoder = encoder
        return m.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 1.0, self._BASE_PROPS,
            use_gpu=use_gpu, tonemapper='reinhard',
            quality=quality, quality_mode=quality_mode,
        )

    def test_nvenc_bitrate_mode_has_no_cq_flag(self):
        cmd = self._cmd('h264_nvenc', use_gpu=True, quality=20000)
        self.assertNotIn('-cq', cmd)
        self.assertEqual(cmd[cmd.index('-b:v') + 1], '20000000')
        self.assertEqual(cmd[cmd.index('-maxrate') + 1], '30000000')
        self.assertEqual(cmd[cmd.index('-bufsize') + 1], '40000000')
        self.assertEqual(cmd[cmd.index('-rc') + 1], 'vbr')

    def test_amf_bitrate_mode_uses_vbr_peak_not_cqp(self):
        cmd = self._cmd('h264_amf', use_gpu=True, quality=20000)
        self.assertNotIn('-qp_i', cmd)
        self.assertNotIn('-qp_p', cmd)
        self.assertNotIn('-qp_b', cmd)
        self.assertEqual(cmd[cmd.index('-rc') + 1], 'vbr_peak')
        self.assertEqual(cmd[cmd.index('-b:v') + 1], '20000000')
        self.assertEqual(cmd[cmd.index('-maxrate') + 1], '30000000')
        self.assertEqual(cmd[cmd.index('-bufsize') + 1], '40000000')

    def test_qsv_bitrate_mode_has_no_global_quality_flag(self):
        cmd = self._cmd('h264_qsv', use_gpu=True, quality=20000)
        self.assertNotIn('-global_quality', cmd)
        self.assertEqual(cmd[cmd.index('-b:v') + 1], '20000000')
        self.assertEqual(cmd[cmd.index('-maxrate') + 1], '30000000')
        self.assertEqual(cmd[cmd.index('-bufsize') + 1], '40000000')

    def test_libx264_bitrate_mode_has_no_crf_flag(self):
        cmd = self._cmd(None, use_gpu=False, quality=20000)
        self.assertIn('libx264', cmd)
        self.assertNotIn('-crf', cmd)
        self.assertEqual(cmd[cmd.index('-b:v') + 1], '20000000')
        self.assertEqual(cmd[cmd.index('-maxrate') + 1], '30000000')
        self.assertEqual(cmd[cmd.index('-bufsize') + 1], '40000000')

    def test_libx265_bitrate_mode_has_no_crf_flag(self):
        """want_libx265 is forced whenever the source is already HEVC."""
        props = {**self._BASE_PROPS, 'codec_name': 'hevc'}
        m = ConversionManager()
        cmd = m.construct_ffmpeg_command(
            'in.mp4', 'out.mkv', 1.0, props, use_gpu=False, tonemapper='reinhard',
            quality=20000, quality_mode='bitrate')
        self.assertIn('libx265', cmd)
        self.assertNotIn('-crf', cmd)
        self.assertEqual(cmd[cmd.index('-b:v') + 1], '20000000')

    def test_constant_quality_mode_is_unaffected(self):
        """Default quality_mode='cq' must still produce -crf, not -b:v."""
        cmd = self._cmd(None, use_gpu=False, quality=20, quality_mode='cq')
        self.assertIn('-crf', cmd)
        self.assertNotIn('-b:v', cmd)


if __name__ == '__main__':
    unittest.main()

