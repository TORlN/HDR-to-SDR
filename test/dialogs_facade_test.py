"""The free-edition _LicenseDialog fallback must satisfy gui.py's contract.

gui._open_license_dialog constructs it, calls wait_window on it, and reads
.activated -- so a no-op stub would crash the Upgrade button.
"""
import builtins
import importlib
import os
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)


def _reload_dialogs_without_pro():
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith('pro.'):
            raise ImportError(f'simulated: no {name}')
        return real_import(name, *args, **kwargs)

    import src.dialogs as dialogs
    with patch.object(builtins, '__import__', side_effect=fake_import):
        return importlib.reload(dialogs)


class TestFreeEditionLicenseDialog(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def tearDown(self):
        import src.dialogs
        importlib.reload(src.dialogs)

    def test_fallback_dialog_constructs(self):
        dialogs = _reload_dialogs_without_pro()
        dlg = dialogs._LicenseDialog(self.root)
        self.addCleanup(dlg.destroy)
        self.assertIsInstance(dlg, tk.Toplevel)

    def test_fallback_dialog_reports_not_activated(self):
        """gui._open_license_dialog reads .activated -- it must exist and be False."""
        dialogs = _reload_dialogs_without_pro()
        dlg = dialogs._LicenseDialog(self.root)
        self.addCleanup(dlg.destroy)
        self.assertFalse(dlg.activated)

    def test_update_dialog_still_available_without_pro(self):
        """_UpdateDialog is not Pro and must survive the split."""
        dialogs = _reload_dialogs_without_pro()
        self.assertTrue(hasattr(dialogs, '_UpdateDialog'))

    def test_activate_license_still_exported(self):
        """7 existing tests patch dialogs.activate_license."""
        dialogs = _reload_dialogs_without_pro()
        self.assertTrue(callable(dialogs.activate_license))
