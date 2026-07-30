"""The licensing façade must re-export the real exception classes."""
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)


class TestExceptionIdentity(unittest.TestCase):
    """The façade must re-export the *same* class objects as license_errors.

    Duplicate exception classes across the two modules would silently break
    every `except InvalidKeyError:` clause in gui.py and dialogs.py -- the
    raised class would not be the caught class.
    """

    def test_facade_reexports_identical_classes(self):
        import license_errors as errors
        import licensing as lic
        for name in ('LicenseError', 'InvalidKeyError',
                     'DeviceLimitError', 'NetworkError'):
            self.assertIs(
                getattr(lic, name), getattr(errors, name),
                msg=f'licensing.{name} is not license_errors.{name}')

    def test_hierarchy_preserved(self):
        import license_errors as errors
        self.assertTrue(issubclass(errors.InvalidKeyError, errors.LicenseError))
        self.assertTrue(issubclass(errors.DeviceLimitError, errors.LicenseError))
        self.assertTrue(issubclass(errors.NetworkError, errors.LicenseError))
        self.assertTrue(issubclass(errors.LicenseError, Exception))
