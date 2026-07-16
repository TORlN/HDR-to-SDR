"""Tests for node-locked licensing: fingerprinting, Lemon Squeezy API, offline grace period."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SRC)

from src.licensing import (
    DeviceLimitError,
    InvalidKeyError,
    LicenseError,
    NetworkError,
    _clear_local_token,
    activate_license,
    check_license,
    check_license_nonblocking,
    get_hardware_fingerprint,
    load_license_token,
    save_license_token,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _urlopen_mock(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error_mock(code: int, body: dict) -> urllib.error.HTTPError:
    fp = io.BytesIO(json.dumps(body).encode())
    return urllib.error.HTTPError(url='', code=code, msg='', hdrs=None, fp=fp)  # type: ignore[arg-type]


def _fresh_payload(key: str = 'SOME-KEY', instance_id: str = 'inst-uuid-123') -> dict:  # type: ignore[type-arg]
    """Recent (within cooldown) token payload with instance_id."""
    return {
        'key': key,
        'fingerprint': get_hardware_fingerprint(),
        'instance_id': instance_id,
        'validated_at': int(time.time()) - 3600,  # 1 hour ago
    }


def _stale_payload(key: str = 'SOME-KEY', instance_id: str = 'inst-uuid-123') -> dict:  # type: ignore[type-arg]
    """Token payload past the 30-day cooldown."""
    return {
        'key': key,
        'fingerprint': get_hardware_fingerprint(),
        'instance_id': instance_id,
        'validated_at': int(time.time()) - 31 * 24 * 3600,
    }


# Lemon Squeezy response shapes
_LS_ACTIVATE_OK = {
    'activated': True,
    'instance': {'id': 'inst-uuid-123', 'name': 'fingerprint-value'},
    'license_key': {'status': 'active', 'key': 'AAAA-BBBB-CCCC-DDDD'},
}
_LS_ACTIVATE_LIMIT = {
    'activated': False,
    'error': 'This license key has exceeded the maximum number of activations.',
}
_LS_ACTIVATE_INVALID = {
    'activated': False,
    'error': 'The provided license key does not exist.',
}
_LS_VALIDATE_OK = {
    'valid': True,
    'instance': {'id': 'inst-uuid-123'},
    'license_key': {'status': 'active'},
}
_LS_VALIDATE_REVOKED = {
    'valid': False,
    'error': 'This license key is suspended.',
}


# ── Hardware fingerprint ───────────────────────────────────────────────────────

class TestHardwareFingerprint(unittest.TestCase):

    def test_fingerprint_is_deterministic(self):
        fp1 = get_hardware_fingerprint()
        fp2 = get_hardware_fingerprint()
        self.assertEqual(fp1, fp2)

    def test_fingerprint_is_hex_sha256(self):
        fp = get_hardware_fingerprint()
        self.assertEqual(len(fp), 64)
        self.assertRegex(fp, r'^[0-9a-f]{64}$')


# ── Activation — new machine ───────────────────────────────────────────────────

class TestLicenseActivation(unittest.TestCase):

    def test_new_activation_success(self):
        """First activation: POST /activate called, token file written."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_ACTIVATE_OK)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('AAAA-BBBB-CCCC-DDDD')
            self.assertTrue(os.path.exists(lic_file))

    def test_new_activation_stores_instance_id(self):
        """After successful activation the saved token must contain the instance_id from LS."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_ACTIVATE_OK)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('AAAA-BBBB-CCCC-DDDD')
                payload = load_license_token()
        self.assertIsNotNone(payload)
        self.assertEqual(payload['instance_id'], 'inst-uuid-123')  # type: ignore[index]

    def test_activation_uses_fingerprint_as_instance_name(self):
        """The instance_name sent to LS must be this machine's hardware fingerprint."""
        import urllib.request as _urlreq
        captured: list = []
        original_urlopen = _urlreq.urlopen

        def capturing_urlopen(req, **kw):
            import urllib.parse
            body = urllib.parse.parse_qs(req.data.decode('utf-8'))
            captured.append(body)
            return _urlopen_mock(_LS_ACTIVATE_OK).__enter__()

        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('urllib.request.urlopen', side_effect=capturing_urlopen), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('AAAA-BBBB-CCCC-DDDD')

        fp = get_hardware_fingerprint()
        self.assertTrue(any(body.get('instance_name') == [fp] for body in captured),
                        f"Expected instance_name={fp!r} in one of {captured}")

    def test_same_key_reuses_existing_activation(self):
        """Same key re-entered on same machine must validate (not activate) to avoid burning a slot."""
        existing = _fresh_payload(key='AAAA-BBBB-CCCC-DDDD', instance_id='existing-inst-id')
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=existing), \
                 patch('src.licensing._ls_activate') as mock_activate, \
                 patch('src.licensing._ls_validate') as mock_validate, \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('AAAA-BBBB-CCCC-DDDD')
        mock_activate.assert_not_called()
        mock_validate.assert_called_once_with('AAAA-BBBB-CCCC-DDDD', 'existing-inst-id')

    def test_different_key_creates_new_activation(self):
        """Entering a different key must call activate (not validate the old instance)."""
        existing = _fresh_payload(key='OLD-KEY', instance_id='old-inst-id')
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=existing), \
                 patch('src.licensing._ls_activate', return_value='new-inst-id') as mock_activate, \
                 patch('src.licensing._ls_validate') as mock_validate, \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                activate_license('NEW-KEY')
        mock_activate.assert_called_once()
        mock_validate.assert_not_called()

    def test_reactivating_a_revoked_key_clears_the_stale_local_token(self):
        """Re-entering a key that already has a local token, but has since
        been revoked/refunded on the server, must clear that stale token --
        otherwise check_license() keeps trusting it (within the cooldown
        window) even though the dialog just showed 'Invalid license key.'"""
        existing = _fresh_payload(key='AAAA-BBBB-CCCC-DDDD', instance_id='existing-inst-id')
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('AAAA-BBBB-CCCC-DDDD', 'existing-inst-id')
            with patch('src.licensing.load_license_token', return_value=existing), \
                 patch('src.licensing._ls_validate', side_effect=InvalidKeyError('revoked')), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                with self.assertRaises(InvalidKeyError):
                    activate_license('AAAA-BBBB-CCCC-DDDD')
            self.assertFalse(os.path.exists(lic_file))

    def test_activation_device_limit_exceeded(self):
        """LS 'exceeded activations' response must raise DeviceLimitError."""
        with patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_ACTIVATE_LIMIT)):
            with self.assertRaises(DeviceLimitError):
                activate_license('LIMIT-KEY-5678')

    def test_activation_invalid_key(self):
        """LS 'does not exist' response must raise InvalidKeyError."""
        with patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_ACTIVATE_INVALID)):
            with self.assertRaises(InvalidKeyError):
                activate_license('BAD-KEY-XXXX')

    def test_network_failure_raises_network_error(self):
        """URLError from the network layer must surface as NetworkError."""
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
            with self.assertRaises(NetworkError):
                activate_license('ANY-KEY-1234')

    def test_empty_key_raises_without_network_call(self):
        """Blank key must be rejected locally before any network call."""
        with patch('urllib.request.urlopen') as mock_net:
            with self.assertRaises(InvalidKeyError):
                activate_license('   ')
            mock_net.assert_not_called()

    def test_http_error_with_non_json_body_raises_network_error(self):
        """HTTPError whose body is not JSON must raise NetworkError."""
        fp = io.BytesIO(b'<html>Service Unavailable</html>')
        err = urllib.error.HTTPError(url='', code=503, msg='', hdrs=None, fp=fp)  # type: ignore[arg-type]
        with patch('urllib.request.urlopen', side_effect=err):
            with self.assertRaises(NetworkError):
                activate_license('ANY-KEY-1234')

    def test_http_error_with_json_body_parsed_as_api_response(self):
        """An HTTPError whose body is parseable JSON is treated as an API error, not a network error."""
        err = _http_error_mock(422, _LS_ACTIVATE_LIMIT)
        with patch('urllib.request.urlopen', side_effect=err):
            with self.assertRaises(DeviceLimitError):
                activate_license('ANY-KEY-1234')


# ── Online / offline behaviour ─────────────────────────────────────────────────

class TestLicenseCheck(unittest.TestCase):

    def test_valid_recent_token_accepted_offline(self):
        """Hardware-bound token within 30-day cooldown is accepted even when the server is unreachable."""
        with patch('src.licensing.load_license_token', return_value=_fresh_payload()), \
             patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            self.assertTrue(check_license())

    def test_recent_token_skips_api_call(self):
        """Token validated within 30 days must not make any API call."""
        with patch('src.licensing.load_license_token', return_value=_fresh_payload()), \
             patch('urllib.request.urlopen') as mock_net:
            self.assertTrue(check_license())
        mock_net.assert_not_called()

    def test_stale_token_triggers_api_refresh(self):
        """Token older than 30 days must POST to /validate."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_VALIDATE_OK)) as mock_net, \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                result = check_license()
        self.assertTrue(result)
        mock_net.assert_called_once()

    def test_valid_token_refreshed_when_online(self):
        """When the server responds OK on a stale token, the token timestamp is updated."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_VALIDATE_OK)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                self.assertTrue(check_license())
            # Reload token and confirm timestamp was refreshed
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                payload = load_license_token()
        self.assertIsNotNone(payload)
        age = int(time.time()) - payload['validated_at']  # type: ignore[index]
        self.assertLess(age, 5, "validated_at must be updated to near-current time")

    def test_revoked_key_removes_token_and_returns_false(self):
        """When the server rejects the key, the local token is deleted and False returned."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('REVOKED-KEY', 'inst-uuid-123')
            with patch('src.licensing.load_license_token', return_value=_stale_payload(key='REVOKED-KEY')), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_VALIDATE_REVOKED)), \
                 patch('src.licensing.LICENSE_FILE', lic_file):
                result = check_license()
        self.assertFalse(result)
        self.assertFalse(os.path.exists(lic_file))

    def test_missing_token_returns_false(self):
        """No local token means unlicensed."""
        with patch('src.licensing.load_license_token', return_value=None):
            self.assertFalse(check_license())

    def test_stale_token_offline_returns_true(self):
        """Stale token + no network → still trust the local token."""
        with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
             patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            self.assertTrue(check_license())

    def test_revoked_key_oserror_on_file_delete_still_returns_false(self):
        """OSError while removing the revoked token file is swallowed; returns False."""
        with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
             patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_VALIDATE_REVOKED)), \
             patch('os.remove', side_effect=OSError('permission denied')):
            self.assertFalse(check_license())


class TestClearLocalToken(unittest.TestCase):
    """_clear_local_token is the single shared implementation behind the three
    call sites (activate_license's revoked-key path, deactivate_license,
    check_license's revoked-key path) that used to each inline their own
    `with _lock: try: os.remove(LICENSE_FILE) except OSError: pass` block."""

    def test_removes_existing_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('SOME-KEY', 'inst-uuid-123')
                self.assertTrue(os.path.exists(lic_file))
                _clear_local_token()
                self.assertFalse(os.path.exists(lic_file))

    def test_swallows_oserror_on_missing_or_locked_file(self):
        with patch('os.remove', side_effect=OSError('permission denied')):
            _clear_local_token()  # must not raise


class TestCheckLicenseNonblocking(unittest.TestCase):
    """check_license_nonblocking must never block app startup on the network --
    it answers from the cached local token immediately and only defers to a
    background thread (never the caller's thread) when a revalidation is due."""

    def test_fresh_token_returns_true_without_starting_background_thread(self):
        with patch('src.licensing.load_license_token', return_value=_fresh_payload()), \
             patch('src.licensing.threading.Thread') as mock_thread:
            result = check_license_nonblocking()
        self.assertTrue(result)
        mock_thread.assert_not_called()

    def test_stale_token_returns_true_immediately_without_blocking_on_network(self):
        """The immediate return must happen before any network call -- the
        refresh is only ever performed on a background thread."""
        with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
             patch('src.licensing.threading.Thread') as mock_thread, \
             patch('urllib.request.urlopen') as mock_net:
            result = check_license_nonblocking()
        self.assertTrue(result)
        mock_net.assert_not_called()
        mock_thread.assert_called_once()
        self.assertTrue(mock_thread.call_args.kwargs.get('daemon'))
        mock_thread.return_value.start.assert_called_once()

    def test_stale_token_background_refresh_invokes_on_change_when_revoked(self):
        """When the deferred revalidation later finds the key revoked, the
        caller-supplied on_change callback must fire with the new (False)
        result so the GUI can be updated after the fact."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_VALIDATE_REVOKED)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.threading.Thread') as mock_thread:
                on_change = MagicMock()
                check_license_nonblocking(on_change=on_change)
                target = mock_thread.call_args.kwargs['target']
                target()
        on_change.assert_called_once_with(False)

    def test_stale_token_background_refresh_skips_on_change_when_still_valid(self):
        """No spurious GUI update when the deferred revalidation agrees with
        the immediate (trusted-offline) answer."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.load_license_token', return_value=_stale_payload()), \
                 patch('urllib.request.urlopen', return_value=_urlopen_mock(_LS_VALIDATE_OK)), \
                 patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp), \
                 patch('src.licensing.threading.Thread') as mock_thread:
                on_change = MagicMock()
                check_license_nonblocking(on_change=on_change)
                target = mock_thread.call_args.kwargs['target']
                target()
        on_change.assert_not_called()

    def test_missing_token_returns_false_without_starting_background_thread(self):
        with patch('src.licensing.load_license_token', return_value=None), \
             patch('src.licensing.threading.Thread') as mock_thread:
            result = check_license_nonblocking()
        self.assertFalse(result)
        mock_thread.assert_not_called()

    def test_dev_unlock_env_var_returns_true_without_starting_background_thread(self):
        with patch.dict(os.environ, {'HDRSDR_DEV_UNLOCK': '1'}), \
             patch('src.licensing.threading.Thread') as mock_thread:
            result = check_license_nonblocking()
        self.assertTrue(result)
        mock_thread.assert_not_called()


# ── Token storage ──────────────────────────────────────────────────────────────

class TestTokenStorage(unittest.TestCase):

    def test_token_round_trip_includes_instance_id(self):
        """save_license_token / load_license_token must preserve key and instance_id."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('MY-LICENSE-KEY', 'my-instance-id')
                payload = load_license_token()
        self.assertIsNotNone(payload)
        self.assertEqual(payload['key'], 'MY-LICENSE-KEY')  # type: ignore[index]
        self.assertEqual(payload['instance_id'], 'my-instance-id')  # type: ignore[index]
        self.assertEqual(payload['fingerprint'], get_hardware_fingerprint())  # type: ignore[index]

    def test_failed_write_does_not_corrupt_existing_token(self):
        """A crash/power-loss/AV-lock mid-write must not leave a truncated
        license.dat -- load_license_token would then treat it as corrupt and
        check_license() would report a previously-activated machine as
        unlicensed. Writing to a temp file and atomically replacing the
        original (like settings.py's save_settings already does) avoids
        this."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('GOOD-KEY', 'good-instance-id')
                with open(lic_file, 'r', encoding='utf-8') as f:
                    original_bytes = f.read()

                with patch('src.licensing.os.replace', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):
                        save_license_token('NEW-KEY', 'new-instance-id')

                with open(lic_file, 'r', encoding='utf-8') as f:
                    after_bytes = f.read()

            self.assertEqual(after_bytes, original_bytes)
            # No stray temp file left behind.
            self.assertEqual(os.listdir(tmp), ['license.dat'])

    def test_tampered_token_rejected(self):
        """Modifying the stored payload causes load_license_token to return None."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                save_license_token('MY-LICENSE-KEY', 'my-instance-id')
            with open(lic_file, 'r') as f:
                token = json.load(f)
            inner = json.loads(token['payload'])
            inner['key'] = 'CRACKED-KEY'
            token['payload'] = json.dumps(inner)
            with open(lic_file, 'w') as f:
                json.dump(token, f)
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch('src.licensing.SETTINGS_DIR', tmp):
                self.assertIsNone(load_license_token())

    def test_missing_token_file_returns_none(self):
        with patch('src.licensing.LICENSE_FILE', '/nonexistent/path/license.dat'):
            self.assertIsNone(load_license_token())

    def test_old_token_without_instance_id_returns_none(self):
        """A legacy token (keygen.sh era, no instance_id) must be treated as absent."""
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            # Write a keygen.sh-style token manually (no instance_id field)
            import hashlib, hmac as _hmac
            fp = get_hardware_fingerprint()
            payload = json.dumps(
                {'key': 'OLD-KEY', 'fingerprint': fp, 'validated_at': int(time.time())},
                separators=(',', ':'), sort_keys=True,
            )
            sig = _hmac.new(
                hashlib.sha256(fp.encode()).digest(),
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()
            with open(lic_file, 'w') as f:
                json.dump({'payload': payload, 'sig': sig}, f)
            with patch('src.licensing.LICENSE_FILE', lic_file):
                result = load_license_token()
        self.assertIsNone(result)


# ── load_license_token edge cases ─────────────────────────────────────────────

class TestLoadLicenseTokenEdgeCases(unittest.TestCase):

    def test_corrupted_payload_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            with open(lic_file, 'w') as f:
                json.dump({'payload': '{not valid json', 'sig': 'fake-sig'}, f)
            import hmac as _hmac
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch.object(_hmac, 'compare_digest', return_value=True):
                self.assertIsNone(load_license_token())

    def test_fingerprint_mismatch_in_payload_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            lic_file = os.path.join(tmp, 'license.dat')
            payload_json = json.dumps(
                {'key': 'K', 'fingerprint': 'wrong-fp', 'instance_id': 'i', 'validated_at': 0}
            )
            with open(lic_file, 'w') as f:
                json.dump({'payload': payload_json, 'sig': 'fake-sig'}, f)
            import hmac as _hmac
            with patch('src.licensing.LICENSE_FILE', lic_file), \
                 patch.object(_hmac, 'compare_digest', return_value=True):
                self.assertIsNone(load_license_token())


if __name__ == '__main__':
    unittest.main()
