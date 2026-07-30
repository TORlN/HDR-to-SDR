"""The shared dialog theme must be importable independently of the Pro dialog."""
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)


class TestDialogTheme(unittest.TestCase):
    def test_theme_constants_present(self):
        import dialog_theme as theme
        for name in ('_BG', '_FG', '_ENTRY_BG', '_ACCENT', '_ERROR_FG',
                     '_FONT', '_FONT_BOLD', '_FONT_SM'):
            self.assertTrue(hasattr(theme, name),
                            msg=f'dialog_theme is missing {name}')

    def test_center_helper_present(self):
        import dialog_theme as theme
        self.assertTrue(callable(theme._center_over_master))

    def test_dialogs_reexports_theme(self):
        """dialogs.py must keep exposing these names -- tests patch them there."""
        import dialog_theme as theme
        import dialogs as dialogs_mod
        self.assertIs(dialogs_mod._BG, theme._BG)
        self.assertIs(dialogs_mod._center_over_master, theme._center_over_master)
