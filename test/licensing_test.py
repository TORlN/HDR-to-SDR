"""Tests for node-locked licensing: fingerprinting, API validation, offline grace period."""
import io
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


def _activate_machine_mock() -> MagicMock:
    """Context-manager mock for the POST /machines call (returns 201 with empty body)."""
    resp = MagicMock()
    resp.read.return_value = b'{}'
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error_mock(code: int, body: dict) -> urllib.error.HTTPError:
    """HTTPError whose .read() returns the given JSON body."""
    fp = io.BytesIO(json.dumps(body).encode())
    return urllib.error.HTTPError(url='', code=code, msg='', hdrs=None, fp=fp)  # type: ignore[arg-type]


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

    def test_fingerprint_mismatch_activates_machine_then_succeeds(self):
        """FINGERPRINT_SCOPE_MISMATCH triggers machine registration, then re-validates."""
        mismatch_resp = {
            'meta': {'valid': False, 'detail': 'is not activated', 'code': 'FINGERPRINT_SCOPE_MISMATCH'},
            'data': {'id': 'lic-uuid-123', 'type': 'licenses'},
        }
        valid_resp = {'meta': {'valid': True, 'detail': 'is valid', 'code': 'VALID'}}
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('urllib.request.urlopen', side_effect=[
                    _urlopen_mock(mismatch_resp),  # validate-key → not activated
                    _activate_machine_mock(),       # POST /machines → 201
                    _urlopen_mock(valid_resp),      # re-validate → VALID
                ]), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('AAAA-BBBB-CCCC-DDDD')
            self.assertTrue(os.path.exists(lic_file), "Token file must be written after machine activation")

    def test_machine_limit_exceeded_raises_device_limit_error(self):
        """If POST /machines returns MACHINE_LIMIT_EXCEEDED, DeviceLimitError is raised."""
        mismatch_resp = {
            'meta': {'valid': False, 'detail': 'is not activated', 'code': 'FINGERPRINT_SCOPE_MISMATCH'},
            'data': {'id': 'lic-uuid-123', 'type': 'licenses'},
        }
        limit_err = _http_error_mock(422, {
            'errors': [{'code': 'MACHINE_LIMIT_EXCEEDED', 'detail': 'machine limit exceeded'}]
        })
        with patch('urllib.request.urlopen', side_effect=[
                _urlopen_mock(mismatch_resp),
                limit_err,
            ]):
            with self.assertRaises(DeviceLimitError):
                activate_license('AAAA-BBBB-CCCC-DDDD')


# ── Online/offline behaviour ───────────────────────────────────────────────────

class TestLicenseCheck(unittest.TestCase):

    def test_valid_token_accepted_offline(self):
        """A hardware-bound token is accepted even when the server is unreachable."""
        payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 3600,  # 1 hour ago — within 30-day cooldown
        }
        with patch('src.licensing.load_license_token', return_value=payload), \
             patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            result = check_license()
        self.assertTrue(result)

    def test_recent_token_skips_api_call(self):
        """Token validated within 30 days must not make any API call."""
        payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 3600,  # 1 hour ago
        }
        with patch('src.licensing.load_license_token', return_value=payload), \
             patch('urllib.request.urlopen') as mock_net:
            result = check_license()
        self.assertTrue(result)
        mock_net.assert_not_called()

    def test_stale_token_triggers_api_refresh(self):
        """Token older than 30 days must attempt an API refresh."""
        payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 31 * 24 * 3600,  # 31 days ago
        }
        api_resp = {'meta': {'valid': True, 'detail': 'is valid', 'code': 'VALID'}}
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=payload), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)) as mock_net, \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                result = check_license()
        self.assertTrue(result)
        mock_net.assert_called_once()

    def test_valid_token_refreshed_when_online(self):
        """When the server responds OK on a stale token, the local token timestamp is updated."""
        payload = {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 31 * 24 * 3600,  # 31 days ago — past cooldown
        }
        api_resp = {'meta': {'valid': True, 'detail': 'is valid', 'code': 'VALID'}}
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=payload), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                result = check_license()
        self.assertTrue(result)

    def test_revoked_key_removes_token_and_returns_false(self):
        """When the server explicitly rejects the key, the local token is deleted."""
        payload = {
            'key': 'REVOKED-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 31 * 24 * 3600,  # 31 days ago — past cooldown
        }
        api_resp = {'meta': {'valid': False, 'detail': 'is suspended', 'code': 'SUSPENDED'}}
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            # Write a real token file so the delete path is exercised.
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('REVOKED-KEY')
            with patch('src.licensing.load_license_token', return_value=payload), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)), \
                 patch('src.licensing.LICENSE_FILE', lic_file):
                result = check_license()
        self.assertFalse(result)
        self.assertFalse(os.path.exists(lic_file))

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


# ── load_license_token edge cases ─────────────────────────────────────────────

class TestLoadLicenseTokenEdgeCases(unittest.TestCase):

    def test_corrupted_payload_json_returns_none(self):
        """If the stored payload_json is not valid JSON, return None."""
        import json as _json
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            token = {'payload': '{not valid json', 'sig': 'fake-sig'}
            with open(lic_file, 'w') as f:
                _json.dump(token, f)
            import hmac as _hmac
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch.object(_hmac, 'compare_digest', return_value=True):
                result = load_license_token()
        self.assertIsNone(result)

    def test_fingerprint_mismatch_in_payload_returns_none(self):
        """Payload with a different fingerprint is rejected even with valid HMAC."""
        import json as _json
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            payload_json = _json.dumps({'key': 'K', 'fingerprint': 'wrong-fp', 'validated_at': 0})
            token = {'payload': payload_json, 'sig': 'fake-sig'}
            with open(lic_file, 'w') as f:
                _json.dump(token, f)
            import hmac as _hmac
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch.object(_hmac, 'compare_digest', return_value=True):
                result = load_license_token()
        self.assertIsNone(result)


# ── _call_api error branches ───────────────────────────────────────────────────

class TestCallApiErrorBranches(unittest.TestCase):

    def test_http_error_with_non_json_body_raises_network_error(self):
        """HTTPError whose body is not JSON must raise NetworkError."""
        import io as _io
        fp = _io.BytesIO(b'<html>Service Unavailable</html>')
        err = urllib.error.HTTPError(url='', code=503, msg='', hdrs=None, fp=fp)  # type: ignore[arg-type]
        with patch('urllib.request.urlopen', side_effect=err):
            with self.assertRaises(NetworkError):
                activate_license('ANY-KEY-1234')


# ── _parse_api_response unknown code ──────────────────────────────────────────

class TestParseApiResponseUnknownCode(unittest.TestCase):

    def test_unknown_error_code_raises_license_error(self):
        """An unrecognised error code from the API must raise LicenseError."""
        api_resp = {'meta': {'valid': False, 'detail': 'something wrong', 'code': 'UNKNOWN_CODE'}}
        with patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)):
            with self.assertRaises(LicenseError):
                activate_license('ANY-KEY-1234')


# ── _activate_machine error branches ──────────────────────────────────────────

class TestActivateMachineErrorBranches(unittest.TestCase):

    _MISMATCH_RESP = {
        'meta': {'valid': False, 'detail': 'not activated', 'code': 'FINGERPRINT_SCOPE_MISMATCH'},
        'data': {'id': 'lic-uuid-123', 'type': 'licenses'},
    }

    def test_other_http_error_raises_network_error(self):
        """A non-machine-limit HTTPError from POST /machines raises NetworkError."""
        import io as _io, json as _json
        body = _json.dumps({'errors': [{'code': 'VALIDATION_ERROR', 'detail': 'bad field'}]}).encode()
        fp = _io.BytesIO(body)
        other_err = urllib.error.HTTPError(url='', code=400, msg='', hdrs=None, fp=fp)  # type: ignore[arg-type]
        with patch('urllib.request.urlopen', side_effect=[
                _urlopen_mock(self._MISMATCH_RESP),
                other_err,
            ]):
            with self.assertRaises(NetworkError):
                activate_license('AAAA-BBBB-CCCC-DDDD')

    def test_non_json_http_error_raises_network_error(self):
        """A non-JSON HTTPError body from POST /machines raises NetworkError."""
        import io as _io
        fp = _io.BytesIO(b'Internal Server Error')
        bad_err = urllib.error.HTTPError(url='', code=500, msg='', hdrs=None, fp=fp)  # type: ignore[arg-type]
        with patch('urllib.request.urlopen', side_effect=[
                _urlopen_mock(self._MISMATCH_RESP),
                bad_err,
            ]):
            with self.assertRaises(NetworkError):
                activate_license('AAAA-BBBB-CCCC-DDDD')

    def test_missing_license_id_in_mismatch_response_raises_license_error(self):
        """FINGERPRINT_SCOPE_MISMATCH without a license ID in the response raises LicenseError."""
        mismatch_no_id = {
            'meta': {'valid': False, 'detail': 'not activated', 'code': 'FINGERPRINT_SCOPE_MISMATCH'},
            'data': {},  # no 'id' field
        }
        with patch('urllib.request.urlopen', return_value=_urlopen_mock(mismatch_no_id)):
            with self.assertRaises(LicenseError):
                activate_license('AAAA-BBBB-CCCC-DDDD')

    def test_activate_machine_url_error_raises_network_error(self):
        """URLError during POST /machines raises NetworkError."""
        with patch('urllib.request.urlopen', side_effect=[
                _urlopen_mock(self._MISMATCH_RESP),
                urllib.error.URLError('connection refused'),
            ]):
            with self.assertRaises(NetworkError):
                activate_license('AAAA-BBBB-CCCC-DDDD')


# ── check_license stale-token branches ────────────────────────────────────────

class TestCheckLicenseStaleTokenBranches(unittest.TestCase):

    def _stale_payload(self) -> dict:  # type: ignore[type-arg]
        return {
            'key': 'SOME-KEY',
            'fingerprint': get_hardware_fingerprint(),
            'validated_at': int(time.time()) - 31 * 24 * 3600,  # 31 days ago — past cooldown
        }

    def test_stale_token_network_offline_still_returns_true(self):
        """When the token is stale but the network is down, trust the local token."""
        with patch('src.licensing.load_license_token', return_value=self._stale_payload()), \
             patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            result = check_license()
        self.assertTrue(result)

    def test_revoked_key_oserror_on_file_delete_still_returns_false(self):
        """OSError while removing the revoked token file is swallowed; function returns False."""
        api_resp = {'meta': {'valid': False, 'detail': 'suspended', 'code': 'SUSPENDED'}}
        with patch('src.licensing.load_license_token', return_value=self._stale_payload()), \
             patch('urllib.request.urlopen', return_value=_urlopen_mock(api_resp)), \
             patch('os.remove', side_effect=OSError('permission denied')):
            result = check_license()
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
