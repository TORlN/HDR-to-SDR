"""Tests for src/platform_utils.py: OS-specific primitives.

TestSetupDpiAwareness relocated verbatim from test/utils_test.py.
TestGpuName relocated from test/conversion_test.py's TestGpuName, edited
to call the free function directly instead of going through a
ConversionManager instance -- see
docs/superpowers/specs/2026-08-08-platform-utils-seam-design.md.
"""
import os
import sys
import subprocess
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.platform_utils import gpu_name


class TestSetupDpiAwareness(unittest.TestCase):
    """setup_dpi_awareness() should enable Per-Monitor DPI awareness on Windows."""

    @patch('sys.platform', 'win32')
    def test_calls_set_process_dpi_awareness_on_windows(self):
        mock_shcore = MagicMock()
        with patch.dict('sys.modules', {'ctypes': MagicMock(windll=MagicMock(shcore=mock_shcore))}):
            import importlib
            import src.platform_utils as _pu
            importlib.reload(_pu)
            _pu.setup_dpi_awareness()
        mock_shcore.SetProcessDpiAwareness.assert_called_once_with(1)

    @patch('sys.platform', 'darwin')
    def test_no_op_on_non_windows(self):
        mock_shcore = MagicMock()
        with patch.dict('sys.modules', {'ctypes': MagicMock(windll=MagicMock(shcore=mock_shcore))}):
            import importlib
            import src.platform_utils as _pu
            importlib.reload(_pu)
            _pu.setup_dpi_awareness()
        mock_shcore.SetProcessDpiAwareness.assert_not_called()

    @patch('sys.platform', 'win32')
    def test_swallows_exceptions(self):
        mock_shcore = MagicMock()
        mock_shcore.SetProcessDpiAwareness.side_effect = OSError("unavailable")
        with patch.dict('sys.modules', {'ctypes': MagicMock(windll=MagicMock(shcore=mock_shcore))}):
            import importlib
            import src.platform_utils as _pu
            importlib.reload(_pu)
            _pu.setup_dpi_awareness()  # must not raise


class TestGpuName(unittest.TestCase):
    """gpu_name() feeds ConversionManager.gpu_name(), which feeds the GPU
    status label's hover tooltip."""

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_prefers_nvidia_smi_name_when_nvidia_present(self, mock_run):
        mock_run.return_value = MagicMock(stdout='NVIDIA GeForce RTX 4090\n')
        self.assertEqual(gpu_name(nvidia_present=True, gpu_encoder=None),
                          'NVIDIA GeForce RTX 4090')
        self.assertEqual(mock_run.call_args.args[0][0], 'nvidia-smi')

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_falls_back_to_wmi_when_nvidia_absent(self, mock_run):
        mock_run.return_value = MagicMock(stdout='Intel UHD Graphics\n')
        self.assertEqual(gpu_name(nvidia_present=False, gpu_encoder=None),
                          'Intel UHD Graphics')
        self.assertEqual(mock_run.call_args.args[0][0], 'powershell')

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_wmi_query_excludes_virtual_adapters(self, mock_run):
        """Regression: a VR headset's link driver (e.g. Meta Quest Link)
        registers a "Meta Virtual Monitor" adapter that WMI otherwise lists
        ahead of the real GPU -- the query itself must filter it out."""
        mock_run.return_value = MagicMock(stdout='NVIDIA GeForce RTX 4090\n')
        gpu_name(nvidia_present=False, gpu_encoder=None)
        command = mock_run.call_args.args[0]
        self.assertIn("-notmatch 'Virtual'", command[-1])

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_wmi_scopes_to_amd_when_amf_encoder_detected(self, mock_run):
        mock_run.return_value = MagicMock(stdout='AMD Radeon RX 7900 XTX\n')
        self.assertEqual(gpu_name(nvidia_present=False, gpu_encoder='h264_amf'),
                          'AMD Radeon RX 7900 XTX')
        command = mock_run.call_args.args[0]
        self.assertIn("AdapterCompatibility -match 'AMD|Advanced Micro Devices'", command[-1])

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_wmi_scopes_to_intel_when_qsv_encoder_detected(self, mock_run):
        mock_run.return_value = MagicMock(stdout='Intel Arc A770\n')
        self.assertEqual(gpu_name(nvidia_present=False, gpu_encoder='h264_qsv'),
                          'Intel Arc A770')
        command = mock_run.call_args.args[0]
        self.assertIn("AdapterCompatibility -match 'Intel'", command[-1])

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_wmi_falls_back_to_unscoped_query_when_vendor_scoped_yields_nothing(self, mock_run):
        """A laptop's AdapterCompatibility string might not literally say
        "AMD"/"Intel" -- if the vendor-scoped query comes up empty, still
        report whatever real (non-virtual) adapter WMI does find rather
        than giving up and returning None."""
        mock_run.side_effect = [
            MagicMock(stdout='   \n'),  # vendor-scoped: nothing matched
            MagicMock(stdout='AMD Radeon RX 6600\n'),  # unscoped fallback
        ]
        self.assertEqual(gpu_name(nvidia_present=False, gpu_encoder='h264_amf'),
                          'AMD Radeon RX 6600')
        self.assertEqual(mock_run.call_count, 2)
        unscoped_command = mock_run.call_args_list[1].args[0]
        self.assertNotIn('AdapterCompatibility', unscoped_command[-1])

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_falls_back_to_wmi_when_nvidia_smi_output_is_blank(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout='   \n'),
            MagicMock(stdout='NVIDIA GeForce RTX 4090\n'),
        ]
        self.assertEqual(gpu_name(nvidia_present=True, gpu_encoder=None),
                          'NVIDIA GeForce RTX 4090')
        self.assertEqual(mock_run.call_count, 2)

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run', side_effect=FileNotFoundError())
    def test_returns_none_when_both_sources_fail(self, _mock_run):
        self.assertIsNone(gpu_name(nvidia_present=True, gpu_encoder=None))

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run', side_effect=subprocess.TimeoutExpired('powershell', 5))
    def test_returns_none_on_timeout(self, _mock_run):
        self.assertIsNone(gpu_name(nvidia_present=False, gpu_encoder=None))

    @patch('sys.platform', 'win32')
    @patch('src.platform_utils.subprocess.run')
    def test_returns_none_when_wmi_output_blank(self, mock_run):
        mock_run.return_value = MagicMock(stdout='   \n')
        self.assertIsNone(gpu_name(nvidia_present=False, gpu_encoder=None))

    @patch('sys.platform', 'darwin')
    def test_returns_none_on_non_windows(self):
        self.assertIsNone(gpu_name(nvidia_present=True, gpu_encoder=None))


if __name__ == '__main__':
    unittest.main()
