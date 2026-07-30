"""The free-edition _LicenseDialog fallback must satisfy gui.py's contract.

gui._open_license_dialog constructs it, calls wait_window on it, and reads
.activated -- so a no-op stub would crash the Upgrade button.
"""
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
    """Reload src.dialogs with `pro.license_dialog` forced unimportable.

    src/dialogs.py reaches `pro.license_dialog` via
    `importlib.import_module(...)` (required to avoid a pyright bug where a
    named `from pro.X import Y` shadows the except-ImportError fallback --
    see src/dialogs.py's own comment). importlib.import_module calls
    `_bootstrap._gcd_import()` directly and never routes through
    `builtins.__import__`, so patching `builtins.__import__` (the previous
    approach here) never actually intercepts it -- confirmed empirically:
    `importlib.import_module('os.path')` still succeeds even with
    `builtins.__import__` patched to reject it. That left every test in this
    module silently exercising the real Pro dialog instead of the fallback.

    Setting `sys.modules['pro.license_dialog'] = None` instead works with
    both import mechanisms: Python's import system raises ImportError for
    any dotted import whose exact sys.modules entry is None, which
    `importlib.import_module` honors just as `__import__` does.
    """
    import src.dialogs as dialogs
    with patch.dict(sys.modules, {'pro.license_dialog': None}):
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

    def test_fallback_dialog_has_no_key_entry_field(self):
        """The real Pro dialog IS a Toplevel with .activated starting False
        too, so those two assertions alone can't tell fallback from Pro --
        this is the one visible difference the brief calls out: the free
        build has nothing to activate against, so it must carry no key-entry
        widget at all (see src/dialogs.py's fallback docstring)."""
        dialogs = _reload_dialogs_without_pro()
        dlg = dialogs._LicenseDialog(self.root)
        self.addCleanup(dlg.destroy)

        def _descendants(widget):
            kids = widget.winfo_children()
            found = list(kids)
            for k in kids:
                found.extend(_descendants(k))
            return found

        entries = [w for w in _descendants(dlg) if isinstance(w, tk.Entry)]
        self.assertEqual(entries, [], msg=f'fallback dialog must have no key '
                          f'entry field, found: {entries}')

    def test_update_dialog_still_available_without_pro(self):
        """_UpdateDialog is not Pro and must survive the split."""
        dialogs = _reload_dialogs_without_pro()
        self.assertTrue(hasattr(dialogs, '_UpdateDialog'))

    def test_activate_license_still_exported(self):
        """7 existing tests patch dialogs.activate_license."""
        dialogs = _reload_dialogs_without_pro()
        self.assertTrue(callable(dialogs.activate_license))
