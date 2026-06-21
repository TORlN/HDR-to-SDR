"""Tests for node-locked licensing: fingerprinting, API validation, offline grace period."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch, call

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)  # needed so src/licensing.py can do: from settings import ...

from src.licensing import (
    GRACE_PERIOD_SECONDS,
    DeviceLimitError,
    InvalidKeyError,
    LicenseError,
    NetworkError,
    activate_license,
    check_license,
    get_hardware_fingerprint,
    load_license_token,
    save_license_token,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _urlopen_mock(body: dict) -> MagicMock:
    """Context-manager mock for urllib.request.urlopen returning JSON body."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── Hardware fingerprint ───────────────────────────────────────────────────────

class TestHardwareFingerprint(unittest.TestCase):

    def test_fingerprint_is_deterministic(self):
        """Same machine must produce the same fingerprint every call."""
        fp1 = get_hardware_fingerprint()
        fp2 = get_hardware_fingerprint()
        self.assertEqual(fp1, fp2)

    def test_fingerprint_is_hex_sha256(self):
        """Fingerprint must be a 64-character lowercase hex string (SHA-256)."""
        fp = get_hardware_fingerprint()
        self.assertEqual(len(fp), 64)
        self.assertRegex(fp, r'^[0-9a-f]{64}$')


# ── API network layer ──────────────────────────────────────────────────────────

class TestLicenseActivation(unittest.TestCase):

    def test_license_activation_success(self):
        """Valid API response stores a local token and does not raise."""
        api_resp = {'meta': {'valid': True, 'detail': 'is valid', 'code': 'VALID'}}
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('AAAA-BBBB-CCCC-DDDD')
            self.assertTrue(os.path.exists(lic_file), "Token file must be written on success")

    def test_license_invalid_key(self):
        """NOT_FOUND API response must raise InvalidKeyError."""
        api_resp = {'meta': {'valid': False, 'detail': 'is invalid', 'code': 'NOT_FOUND'}}
        with patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)):
            with self.assertRaises(InvalidKeyError):
                activate_license('BAD-KEY-XXXX')

    def test_license_device_limit_exceeded(self):
        """TOO_MANY_MACHINES API response must raise DeviceLimitError."""
        api_resp = {
            'meta': {'valid': False, 'detail': 'has too many machines', 'code': 'TOO_MANY_MACHINES'}
        }
        with patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)):
            with self.assertRaises(DeviceLimitError):
                activate_license('LIMIT-KEY-5678')

    def test_network_failure_raises_network_error(self):
        """URLError from the network layer must surface as NetworkError."""
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
            with self.assertRaises(NetworkError):
                activate_license('ANY-KEY-1234')

    def test_empty_key_raises_without_network_call(self):
        """Blank key must be rejected locally before making any network call."""
        with patch('urllib.request.urlopen') as mock_net:
            with self.assertRaises(InvalidKeyError):
                activate_license('   ')
            mock_net.assert_not_called()


# ── Offline grace period ───────────────────────────────────────────────────────

class TestOfflineGracePeriod(unittest.TestCase):

    def test_offline_grace_period_valid(self):
        """Token validated 1 hour ago is within the grace window — no network call."""
        fresh_payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 3600,  # 1 hour ago
        }
        with patch('src.licensing.load_license_token', return_value=fresh_payload), \
             patch('urllib.request.urlopen') as mock_net:
            result = check_license()
        self.assertTrue(result)
        mock_net.assert_not_called()

    def test_offline_grace_period_expired(self):
        """Token older than 72 hours with no network access must return False."""
        old_payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - (GRACE_PERIOD_SECONDS + 3600),  # 73 h ago
        }
        with patch('src.licensing.load_license_token', return_value=old_payload), \
             patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            result = check_license()
        self.assertFalse(result)

    def test_expired_token_refreshed_when_api_succeeds(self):
        """Token older than 72 hours is renewed if the API call succeeds."""
        old_payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - (GRACE_PERIOD_SECONDS + 3600),
        }
        api_resp = {'meta': {'valid': True, 'detail': 'is valid', 'code': 'VALID'}}
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=old_payload), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                result = check_license()
        self.assertTrue(result)

    def test_missing_token_returns_false(self):
        """No local token means unlicensed — must return False."""
        with patch('src.licensing.load_license_token', return_value=None):
            result = check_license()
        self.assertFalse(result)


# ── Token storage ──────────────────────────────────────────────────────────────

class TestTokenStorage(unittest.TestCase):

    def test_token_round_trip(self):
        """save_license_token then load_license_token must return the same key."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('MY-LICENSE-KEY')
                payload = load_license_token()
        self.assertIsNotNone(payload)
        self.assertEqual(payload['key'], 'MY-LICENSE-KEY')  # type: ignore[index]
        self.assertEqual(payload['fingerprint'], get_hardware_fingerprint())  # type: ignore[index]

    def test_tampered_token_rejected(self):
        """Modifying the stored token payload must cause load_license_token to return None."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('MY-LICENSE-KEY')

            # Tamper with the payload inside the token file
            with open(lic_file, 'r') as f:
                token = json.load(f)
            inner = json.loads(token['payload'])
            inner['key'] = 'CRACKED-KEY'
            token['payload'] = json.dumps(inner)
            with open(lic_file, 'w') as f:
                json.dump(token, f)

            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                payload = load_license_token()
        self.assertIsNone(payload)

    def test_missing_token_file_returns_none(self):
        """load_license_token returns None when the file does not exist."""
        with patch('src.licensing.LICENSE_FILE', '/nonexistent/path/license.dat'):
            self.assertIsNone(load_license_token())


if __name__ == '__main__':
    unittest.main()
