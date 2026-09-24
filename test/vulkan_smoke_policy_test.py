"""Unit tests for opting into the real Vulkan smoke tests."""

import unittest

from test._vulkan_smoke import is_physical_vulkan_device, resolve_vulkan_smoke


class TestResolveVulkanSmoke(unittest.TestCase):
    def test_skip_does_not_probe_vulkan(self):
        calls = []

        def probe():
            calls.append(True)
            return True

        self.assertFalse(resolve_vulkan_smoke('skip', probe))
        self.assertEqual(calls, [])

    def test_run_returns_probe_result(self):
        calls = []

        def probe():
            calls.append(True)
            return True

        self.assertTrue(resolve_vulkan_smoke('run', probe))
        self.assertEqual(calls, [True])

    def test_run_returns_false_when_probe_fails(self):
        self.assertFalse(resolve_vulkan_smoke('run', lambda: False))

    def test_require_returns_true_when_probe_succeeds(self):
        self.assertTrue(resolve_vulkan_smoke('require', lambda: True))

    def test_require_raises_when_probe_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'Vulkan/libplacebo'):
            resolve_vulkan_smoke('require', lambda: False)

    def test_require_physical_raises_when_probe_finds_no_physical_gpu(self):
        try:
            resolve_vulkan_smoke('require-physical', lambda: False)
        except RuntimeError as error:
            self.assertIn('physical Vulkan GPU', str(error))
        except Exception as error:
            self.fail(f'expected physical-device RuntimeError, got {type(error).__name__}')
        else:
            self.fail('required physical Vulkan GPU was accepted as available')

    def test_physical_device_log_is_recognized(self):
        self.assertTrue(is_physical_vulkan_device(
            '[AVHWDeviceContext] Device 0 selected: NVIDIA GeForce (discrete) (0x2684)'))
        self.assertTrue(is_physical_vulkan_device(
            '[AVHWDeviceContext] Device 0 selected: Intel Graphics (integrated) (0x9a49)'))

    def test_software_virtual_unknown_or_missing_device_is_rejected(self):
        for log in (
            '[Vulkan] Device 0 selected: llvmpipe (software) (0x0)',
            '[Vulkan] Device 0 selected: Virtual GPU (virtual) (0x1234)',
            '[Vulkan] Device 0 selected: Device (unknown) (0x1234)',
            'Vulkan initialization failed',
        ):
            with self.subTest(log=log):
                self.assertFalse(is_physical_vulkan_device(log))

    def test_unknown_mode_raises(self):
        with self.assertRaisesRegex(ValueError, 'HDR_VULKAN_SMOKE_MODE'):
            resolve_vulkan_smoke('sometimes', lambda: False)


if __name__ == '__main__':
    unittest.main()
