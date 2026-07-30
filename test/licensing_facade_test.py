"""The public licensing façade must degrade to free-edition behavior.

These tests simulate a Community Edition build -- a checkout or installer with
no src/pro/ package -- by making `pro.licensing` unimportable, then reloading
the façade.
"""
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
    """Reload src.licensing with `pro.licensing` forced to ImportError.

    src/licensing.py reaches `pro.licensing` via
    `importlib.import_module(...)` (deliberately, to avoid a pyright bug --
    see that module's own comment). importlib.import_module calls
    `_bootstrap._gcd_import()` directly and never routes through
    `builtins.__import__`, so patching `builtins.__import__` (the previous
    approach here) never actually intercepted it -- confirmed empirically:
    `importlib.import_module('os.path')` still succeeds even with
    `builtins.__import__` patched to reject it. That left every test in this
    module silently exercising the real Pro licensing backend instead of the
    free-edition fallback.

    Setting `sys.modules['pro.licensing'] = None` instead works with both
    import mechanisms: Python's import system raises ImportError for any
    dotted import whose exact sys.modules entry is None, which
    `importlib.import_module` honors just as `__import__` does.
    """
    import src.licensing as facade
    with patch.dict(sys.modules, {'pro.licensing': None}):
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

    def test_activate_raises_invalid_key_without_attempting_network_activation(self):
        """Raising InvalidKeyError alone doesn't distinguish this stub from
        the real Pro backend -- a real activation call against a garbage key
        also raises InvalidKeyError, just after a genuine Lemon Squeezy round
        trip (this is exactly how the old, broken `builtins.__import__`
        patch masked itself: it never blocked the real backend, and the
        real backend's honest "key not found" response happened to produce
        the same exception type). The free-edition stub must raise
        immediately, with no network attempt at all."""
        facade = _reload_facade_without_pro()
        with patch('urllib.request.urlopen') as mock_urlopen:
            with self.assertRaises(facade.InvalidKeyError):
                facade.activate_license('ANY-KEY')
        mock_urlopen.assert_not_called()

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
