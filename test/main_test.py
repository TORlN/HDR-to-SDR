"""The frozen application's noninteractive release smoke mode."""

import os
import runpy
import sys
import types
import unittest
from unittest.mock import patch


_MAIN_PATH = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.pyw')


class TestFrozenSmokeMode(unittest.TestCase):

    def test_smoke_test_imports_the_entry_point_without_starting_tk(self):
        """Removing the early smoke exit would construct Tk during release validation."""
        def unexpected(*_args, **_kwargs):
            self.fail('smoke mode must not initialize application services')

        tkinterdnd2 = types.ModuleType('tkinterdnd2')
        tkinterdnd2.TkinterDnD = types.SimpleNamespace(Tk=unexpected)
        gui = types.ModuleType('gui')
        gui.HDRConverterGUI = unexpected
        licensing = types.ModuleType('licensing')
        licensing.check_license_nonblocking = unexpected
        platform_utils = types.ModuleType('platform_utils')
        platform_utils.setup_dpi_awareness = unexpected
        updater = types.ModuleType('updater')
        updater.cleanup_stale_update_directories = unexpected

        with patch.dict(sys.modules, {
            'tkinterdnd2': tkinterdnd2,
            'gui': gui,
            'licensing': licensing,
            'platform_utils': platform_utils,
            'updater': updater,
        }):
            with patch.object(sys, 'argv', [_MAIN_PATH, '--smoke-test']):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(_MAIN_PATH, run_name='__main__')

        self.assertEqual(raised.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
