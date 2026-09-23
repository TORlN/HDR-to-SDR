"""Unit tests for opting into the real Vulkan smoke tests."""

import unittest

from test._vulkan_smoke import resolve_vulkan_smoke


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

    def test_unknown_mode_raises(self):
        with self.assertRaisesRegex(ValueError, 'HDR_VULKAN_SMOKE_MODE'):
            resolve_vulkan_smoke('sometimes', lambda: False)


if __name__ == '__main__':
    unittest.main()
