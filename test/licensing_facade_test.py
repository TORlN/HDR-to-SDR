"""The public licensing façade must degrade to free-edition behavior.

These tests simulate a Community Edition build -- a checkout or installer with
no src/pro/ package -- by making `pro.licensing` unimportable, then reloading
the façade.
"""
import builtins
import importlib
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)


def _reload_facade_without_pro():
    """Reload src.licensing with `pro.licensing` forced to ImportError."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'pro.licensing' or name.startswith('pro.'):
            raise ImportError(f'simulated: no {name}')
        return real_import(name, *args, **kwargs)

    import src.licensing as facade
    with patch.object(builtins, '__import__', side_effect=fake_import):
        return importlib.reload(facade)


class TestFreeEditionFacade(unittest.TestCase):
    def tearDown(self):
        # Restore the real module so later tests in the run are unaffected.
        import src.licensing
        importlib.reload(src.licensing)

    def test_check_license_returns_false(self):
        facade = _reload_facade_without_pro()
        self.assertFalse(facade.check_license())

    def test_check_license_nonblocking_returns_false(self):
        facade = _reload_facade_without_pro()
        self.assertFalse(facade.check_license_nonblocking())

    def test_check_license_nonblocking_accepts_on_change_kwarg(self):
        """main.pyw calls this with on_change=; the stub must accept it."""
        facade = _reload_facade_without_pro()
        self.assertFalse(facade.check_license_nonblocking(on_change=lambda _: None))

    def test_activate_raises_invalid_key(self):
        facade = _reload_facade_without_pro()
        with self.assertRaises(facade.InvalidKeyError):
            facade.activate_license('ANY-KEY')

    def test_deactivate_returns_false(self):
        facade = _reload_facade_without_pro()
        self.assertFalse(facade.deactivate_license())

    def test_exceptions_still_exported(self):
        facade = _reload_facade_without_pro()
        # Bare import, not `src.license_errors`: src/licensing.py resolves its
        # `from license_errors import ...` via sys.path as the bare top-level
        # module (matching gui.py/dialogs.py's real bare imports), so that is
        # the module identity that must match here too. Comparing against the
        # dotted `src.license_errors` would check a second, distinct module
        # object created only by this test's own dual sys.path bootstrap --
        # always a different class, regardless of the façade's correctness.
        import license_errors as errors
        self.assertIs(facade.InvalidKeyError, errors.InvalidKeyError)
        self.assertIs(facade.LicenseError, errors.LicenseError)
