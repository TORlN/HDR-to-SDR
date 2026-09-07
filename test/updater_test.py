"""Tests for src/updater.py — auto-update logic.

Covers:
  - Version comparison (_version_tuple)
  - check_for_update: new version found, same version, older version, network
    error, missing asset, malformed response
  - download_installer: writes data, calls progress callback
  - launch_installer: calls subprocess.Popen with detached flags
  - GUI integration: _show_update_dialog constructs _UpdateDialog,
    _start_update_check calls the dialog on the main thread when an update exists
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# gui.py uses bare imports (from dark_theme import ...) resolved from src/.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import src.updater as updater
# Ensure the bare name 'updater' (used by gui.py's _worker) and 'src.updater' are
# the same object in sys.modules so patches applied to one are visible to the other.
sys.modules.setdefault('updater', updater)
from src.updater import (
    APP_VERSION,
    _version_tuple,
    check_for_update,
    download_installer,
    launch_installer,
    _verify_installer_signature,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

_DOWNLOAD_URL = (
    'https://github.com/TORlN/HDR-to-SDR/releases/download/'
    'v99.0.0/HDR_to_SDR_Setup.exe'
)
_FINAL_URL = (
    'https://release-assets.githubusercontent.com/github-production-release-asset/'
    '123/HDR_to_SDR_Setup.exe'
)


def _make_response(body: bytes, content_length: int | None = None,
                   final_url: str = _FINAL_URL) -> MagicMock:
    """Fake urllib response context manager."""
    resp = MagicMock()
    resp.read.side_effect = [body, b'']
    headers = {}
    if content_length is not None:
        headers['Content-Length'] = str(content_length)
    resp.headers = headers
    resp.geturl.return_value = final_url
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _github_payload(tag: str, asset_name: str = 'HDR_to_SDR_Setup.exe',
                    url: str = _DOWNLOAD_URL, size: int = 123,
                    digest: str = 'sha256:' + 'a' * 64,
                    state: str = 'uploaded') -> bytes:
    return json.dumps({
        'tag_name': tag,
        'assets': [{
            'name': asset_name,
            'browser_download_url': url,
            'state': state,
            'size': size,
            'digest': digest,
        }],
    }).encode()


# ── _version_tuple ─────────────────────────────────────────────────────────────

class TestVersionTuple(unittest.TestCase):

    def test_parses_semver(self):
        self.assertEqual(_version_tuple('3.0.0'), (3, 0, 0))

    def test_strips_v_prefix_via_caller(self):
        # check_for_update strips 'v' before returning; _version_tuple itself
        # just extracts digits, so 'v3.1.0' still works.
        self.assertEqual(_version_tuple('v3.1.0'), (3, 1, 0))

    def test_patch_bump(self):
        self.assertGreater(_version_tuple('3.0.1'), _version_tuple('3.0.0'))

    def test_minor_bump(self):
        self.assertGreater(_version_tuple('3.1.0'), _version_tuple('3.0.9'))

    def test_major_bump(self):
        self.assertGreater(_version_tuple('4.0.0'), _version_tuple('3.99.99'))


# ── check_for_update ───────────────────────────────────────────────────────────

class TestCheckForUpdate(unittest.TestCase):

    def _patch_urlopen(self, payload: bytes):
        return patch('urllib.request.urlopen',
                     return_value=_make_response(
                         payload, final_url=updater._GITHUB_API))

    def test_newer_version_returns_version_and_url(self):
        newer_tag = 'v99.0.0'
        expected_url = _DOWNLOAD_URL
        with self._patch_urlopen(_github_payload(newer_tag, url=expected_url)):
            result = check_for_update()
        self.assertIsNotNone(result)
        assert result is not None
        new_ver, url, release_url, size, digest = result
        self.assertEqual(new_ver, '99.0.0')
        self.assertEqual(url, expected_url)
        self.assertEqual(release_url, updater.RELEASES_URL)
        self.assertEqual(size, 123)
        self.assertEqual(digest, 'a' * 64)

    def test_untrusted_asset_metadata_is_rejected(self):
        invalid_payloads = (
            _github_payload('v99.0.0', state='new'),
            _github_payload('v99.0.0', size=0),
            _github_payload('v99.0.0', size=updater._MAX_INSTALLER_BYTES + 1),
            _github_payload('v99.0.0', digest='sha256:not-a-digest'),
            _github_payload('v99.0.0', url='http://github.com/unsafe.exe'),
            _github_payload('v99.0.0', url='https://evil.example/setup.exe'),
            _github_payload('v99.0.0', url=_DOWNLOAD_URL + '?token=unexpected'),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self._patch_urlopen(payload):
                    self.assertIsNone(check_for_update())

    def test_oversized_metadata_response_is_rejected(self):
        payload = b'{' + b'x' * updater._MAX_METADATA_BYTES
        with self._patch_urlopen(payload):
            self.assertIsNone(check_for_update())

    def test_metadata_redirect_to_untrusted_host_is_rejected(self):
        response = _make_response(
            _github_payload('v99.0.0'),
            final_url='https://evil.example/releases/latest',
        )
        with patch('urllib.request.urlopen', return_value=response):
            self.assertIsNone(check_for_update())

    def test_same_version_returns_none(self):
        with self._patch_urlopen(_github_payload(f'v{APP_VERSION}')):
            result = check_for_update()
        self.assertIsNone(result)

    def test_older_version_returns_none(self):
        with self._patch_urlopen(_github_payload('v0.0.1')):
            result = check_for_update()
        self.assertIsNone(result)

    def test_network_error_returns_none_and_logs_warning(self):
        with patch('urllib.request.urlopen', side_effect=OSError('no route')):
            with patch.object(updater, 'logger') as mock_logger:
                result = check_for_update()
        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()

    def test_missing_asset_returns_none(self):
        payload = json.dumps({
            'tag_name': 'v99.0.0',
            'assets': [],
        }).encode()
        with self._patch_urlopen(payload):
            result = check_for_update()
        self.assertIsNone(result)

    def test_wrong_asset_name_returns_none(self):
        payload = _github_payload('v99.0.0', asset_name='OtherApp.exe')
        with self._patch_urlopen(payload):
            result = check_for_update()
        self.assertIsNone(result)

    def test_missing_tag_returns_none(self):
        payload = json.dumps({'assets': []}).encode()
        with self._patch_urlopen(payload):
            result = check_for_update()
        self.assertIsNone(result)

    def test_malformed_json_returns_none_and_logs_warning(self):
        resp = MagicMock()
        resp.read.return_value = b'not json'
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        with patch('urllib.request.urlopen', return_value=resp):
            with patch.object(updater, 'logger') as mock_logger:
                result = check_for_update()
        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()

    def test_timeout_is_set(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError) as m:
            check_for_update()
        _, kwargs = m.call_args
        self.assertEqual(kwargs.get('timeout'), 10)

    def _http_error(self, code: int, reason: str, headers: dict | None = None):
        import urllib.error
        import email.message
        hdrs = email.message.Message()
        for k, v in (headers or {}).items():
            hdrs.add_header(k, v)
        return urllib.error.HTTPError('https://api.github.com/x', code, reason, hdrs, None)

    def test_rate_limit_error_logs_warning(self):
        err = self._http_error(403, 'rate limit exceeded', {
            'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1784742169',
        })
        with patch('urllib.request.urlopen', side_effect=err):
            with patch.object(updater, 'logger') as mock_logger:
                result = check_for_update()
        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()
        self.assertIn('rate limit', mock_logger.warning.call_args[0][0].lower())

    def test_other_http_error_logs_warning(self):
        err = self._http_error(500, 'Internal Server Error')
        with patch('urllib.request.urlopen', side_effect=err):
            with patch.object(updater, 'logger') as mock_logger:
                result = check_for_update()
        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()
        message = mock_logger.warning.call_args[0]
        self.assertIn(500, message)


# ── download_installer ─────────────────────────────────────────────────────────

class TestDownloadInstaller(unittest.TestCase):

    def _fake_urlopen(self, data: bytes, *, content_length: int | None = None,
                      final_url: str = _FINAL_URL, content_encoding: str | None = None):
        """Produces a urlopen mock that yields *data* in one chunk."""
        resp = _make_response(
            data,
            len(data) if content_length is None else content_length,
            final_url,
        )
        if content_encoding is not None:
            resp.headers['Content-Encoding'] = content_encoding
        return patch('urllib.request.urlopen', return_value=resp)

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def test_writes_content_to_file(self):
        content = b'fake installer binary'
        opened = mock_open()
        with patch('builtins.open', opened):
            with self._fake_urlopen(content):
                with patch.object(updater, '_verify_installer_signature') as verify:
                    download_installer(
                        _DOWNLOAD_URL, 'setup.exe', len(content), self._digest(content))
        opened().write.assert_called_once_with(content)
        verify.assert_called_once_with('setup.exe')

    def test_progress_callback_called(self):
        content = b'x' * 200
        calls: list[tuple[int, int]] = []
        with patch('builtins.open', mock_open()):
            with self._fake_urlopen(content):
                with patch.object(updater, '_verify_installer_signature'):
                    download_installer(
                        _DOWNLOAD_URL, 'setup.exe', len(content), self._digest(content),
                        progress_cb=lambda d, t: calls.append((d, t)),
                    )
        self.assertTrue(len(calls) > 0)
        self.assertEqual(calls[-1], (len(content), len(content)))

    def test_no_progress_callback_is_ok(self):
        content = b'data'
        with patch('builtins.open', mock_open()):
            with self._fake_urlopen(content):
                with patch.object(updater, '_verify_installer_signature'):
                    download_installer(
                        _DOWNLOAD_URL, 'setup.exe', len(content), self._digest(content))

    def test_rejects_untrusted_redirect_destination(self):
        content = b'data'
        with self._fake_urlopen(content, final_url='https://evil.example/setup.exe'):
            with patch('builtins.open', mock_open()) as opened:
                with self.assertRaisesRegex(ValueError, 'redirect'):
                    download_installer(
                        _DOWNLOAD_URL, 'setup.exe', len(content), self._digest(content))
        opened.assert_not_called()

    def test_rejects_mismatched_content_length(self):
        content = b'data'
        with self._fake_urlopen(content, content_length=len(content) + 1):
            with patch('builtins.open', mock_open()) as opened:
                with self.assertRaisesRegex(ValueError, 'Content-Length'):
                    download_installer(
                        _DOWNLOAD_URL, 'setup.exe', len(content), self._digest(content))
        opened.assert_not_called()

    def test_rejects_encoded_response(self):
        content = b'data'
        with self._fake_urlopen(content, content_encoding='gzip'):
            with patch('builtins.open', mock_open()) as opened:
                with self.assertRaisesRegex(ValueError, 'encoding'):
                    download_installer(
                        _DOWNLOAD_URL, 'setup.exe', len(content), self._digest(content))
        opened.assert_not_called()

    def test_rejects_truncated_oversized_and_modified_downloads(self):
        expected = b'expected installer'
        cases = (
            (expected[:-1], self._digest(expected), 'size'),
            (expected + b'x', self._digest(expected), 'size'),
            (expected, '0' * 64, 'SHA-256'),
        )
        for content, digest, message in cases:
            with self.subTest(message=message):
                with self._fake_urlopen(content, content_length=len(expected)):
                    with patch('builtins.open', mock_open()):
                        with patch('os.remove') as remove:
                            with self.assertRaisesRegex(ValueError, message):
                                download_installer(
                                    _DOWNLOAD_URL, 'setup.exe', len(expected), digest)
                remove.assert_called_once_with('setup.exe')

    def test_rejects_invalid_expected_download_contract_before_network(self):
        cases = (
            (0, 'a' * 64),
            (updater._MAX_INSTALLER_BYTES + 1, 'a' * 64),
            (1, 'not-a-digest'),
        )
        for size, digest in cases:
            with self.subTest(size=size, digest=digest):
                with patch('urllib.request.urlopen') as urlopen:
                    with self.assertRaises(ValueError):
                        download_installer(_DOWNLOAD_URL, 'setup.exe', size, digest)
                urlopen.assert_not_called()

    def test_signature_rejection_removes_complete_download(self):
        content = b'complete but untrusted installer'
        with self._fake_urlopen(content):
            with patch('builtins.open', mock_open()):
                with patch.object(
                        updater, '_verify_installer_signature',
                        side_effect=ValueError('invalid signature')):
                    with patch('os.remove') as remove:
                        with self.assertRaisesRegex(ValueError, 'invalid signature'):
                            download_installer(
                                _DOWNLOAD_URL, 'setup.exe', len(content),
                                self._digest(content),
                            )
        remove.assert_called_once_with('setup.exe')


# ── launch_installer ───────────────────────────────────────────────────────────

class TestLaunchInstaller(unittest.TestCase):

    @unittest.skipUnless(sys.platform == "win32", "Windows-only creationflags")
    def test_calls_popen_with_detached_flags(self):
        with patch.object(updater, '_verify_installer_signature') as verify:
            with patch('subprocess.Popen') as mock_popen:
                launch_installer(r'C:\tmp\HDR_to_SDR_Setup.exe')
        verify.assert_called_once_with(r'C:\tmp\HDR_to_SDR_Setup.exe')
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], [r'C:\tmp\HDR_to_SDR_Setup.exe'])
        flags = kwargs.get('creationflags', 0)
        import subprocess
        self.assertTrue(flags & subprocess.DETACHED_PROCESS)
        self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)

    def test_close_fds_true(self):
        with patch.object(updater, '_verify_installer_signature'):
            with patch('subprocess.Popen') as mock_popen:
                launch_installer('setup.exe')
        _, kwargs = mock_popen.call_args
        self.assertTrue(kwargs.get('close_fds'))

    def test_signature_failure_prevents_launch(self):
        with patch.object(
                updater, '_verify_installer_signature',
                side_effect=ValueError('invalid signature')):
            with patch('subprocess.Popen') as mock_popen:
                with self.assertRaisesRegex(ValueError, 'invalid signature'):
                    launch_installer('setup.exe')
        mock_popen.assert_not_called()


class TestStaleUpdateCleanup(unittest.TestCase):

    def test_removes_only_app_owned_temp_directories(self):
        stale_dir = MagicMock()
        stale_dir.name = 'hdr_to_sdr_update_stale'
        stale_dir.path = r'C:\Temp\hdr_to_sdr_update_stale'
        stale_dir.is_dir.return_value = True
        stale_dir.stat.return_value.st_mtime = 0
        active_dir = MagicMock()
        active_dir.name = 'hdr_to_sdr_update_active'
        active_dir.path = r'C:\Temp\hdr_to_sdr_update_active'
        active_dir.is_dir.return_value = True
        active_dir.stat.return_value.st_mtime = 200_000
        link = MagicMock()
        link.name = 'hdr_to_sdr_update_link'
        link.path = r'C:\Temp\hdr_to_sdr_update_link'
        link.is_dir.return_value = False
        unrelated_dir = MagicMock()
        unrelated_dir.name = 'other_app_update_123'
        unrelated_dir.path = r'C:\Temp\other_app_update_123'

        with patch('tempfile.gettempdir', return_value=r'C:\Temp'):
            with patch('os.scandir') as scandir:
                scandir.return_value.__enter__.return_value = [
                    stale_dir, active_dir, link, unrelated_dir,
                ]
                with patch('time.time', return_value=200_000):
                    with patch('shutil.rmtree') as rmtree:
                        updater.cleanup_stale_update_directories()

        stale_dir.is_dir.assert_called_once_with(follow_symlinks=False)
        active_dir.is_dir.assert_called_once_with(follow_symlinks=False)
        link.is_dir.assert_called_once_with(follow_symlinks=False)
        rmtree.assert_called_once_with(stale_dir.path, ignore_errors=True)


@unittest.skipUnless(sys.platform == 'win32', 'Authenticode is Windows-only')
class TestAuthenticodeVerification(unittest.TestCase):

    def _completed(self, status: str = 'Valid',
                   subject: str = updater._EXPECTED_PUBLISHER) -> MagicMock:
        return MagicMock(stdout=json.dumps({'Status': status, 'Subject': subject}))

    def test_accepts_valid_expected_publisher(self):
        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.run', return_value=self._completed()) as run:
                _verify_installer_signature('setup.exe')
        args, kwargs = run.call_args
        self.assertTrue(os.path.isabs(args[0][0]))
        self.assertNotIn('setup.exe', args[0])
        script = args[0][-1]
        self.assertIn("$ErrorActionPreference='Stop'", script)
        self.assertIn('Import-Module', script)
        self.assertIn('Microsoft.PowerShell.Security.psd1', script)
        self.assertEqual(kwargs['env']['HDRSDR_INSTALLER_PATH'],
                         os.path.abspath('setup.exe'))
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)
        self.assertFalse(kwargs.get('shell', False))

    def test_rejects_invalid_status_or_wrong_publisher(self):
        cases = (
            self._completed(status='HashMismatch'),
            self._completed(subject='CN=Unexpected Publisher'),
        )
        for completed in cases:
            with self.subTest(stdout=completed.stdout):
                with patch('os.path.isfile', return_value=True):
                    with patch('subprocess.run', return_value=completed):
                        with self.assertRaisesRegex(ValueError, 'signature'):
                            _verify_installer_signature('setup.exe')

    def test_subprocess_failure_is_fail_closed(self):
        with patch('os.path.isfile', return_value=True):
            with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('pwsh', 30)):
                with self.assertRaisesRegex(ValueError, 'signature'):
                    _verify_installer_signature('setup.exe')


# ── GUI integration (unit-level, no real Tk needed) ───────────────────────────

class TestGuiUpdateIntegration(unittest.TestCase):
    """Test _start_update_check and _show_update_dialog without a live Tk."""

    def _make_gui(self):
        """Return a bare HDRConverterGUI instance bypassing __init__."""
        from src.gui import HDRConverterGUI, _UpdateDialog
        gui = object.__new__(HDRConverterGUI)
        gui.root = MagicMock()
        return gui, _UpdateDialog

    def test_show_update_dialog_constructs_dialog(self):
        gui, _UpdateDialog = self._make_gui()
        gui._shutdown_for_update = MagicMock()
        with patch('src.gui._UpdateDialog') as MockDialog:
            gui._show_update_dialog(
                '3.0.0', '4.0.0', _DOWNLOAD_URL, updater.RELEASES_URL,
                123, 'a' * 64,
            )
        MockDialog.assert_called_once_with(
            gui.root, '3.0.0', '4.0.0', _DOWNLOAD_URL,
            updater.RELEASES_URL, 123, 'a' * 64, gui._shutdown_for_update,
        )

    def test_successful_installer_launch_uses_shutdown_callback(self):
        from src.dialogs import _UpdateDialog
        dialog = object.__new__(_UpdateDialog)
        dialog._tmp_dir = None
        dialog._on_download_error = MagicMock()
        dialog.master = MagicMock()
        dialog._shutdown_callback = MagicMock()

        with patch('src.updater.launch_installer'):
            dialog._launch_and_close('setup.exe')

        dialog._shutdown_callback.assert_called_once_with()
        dialog.master.destroy.assert_not_called()

    def test_failed_installer_launch_does_not_shutdown(self):
        from src.dialogs import _UpdateDialog
        dialog = object.__new__(_UpdateDialog)
        dialog._tmp_dir = None
        dialog._on_download_error = MagicMock()
        dialog.master = MagicMock()
        dialog._shutdown_callback = MagicMock()

        with patch('src.updater.launch_installer', side_effect=OSError('blocked')):
            dialog._launch_and_close('setup.exe')

        dialog._shutdown_callback.assert_not_called()
        dialog.master.destroy.assert_not_called()

    @patch('src.gui.conversion_manager')
    def test_idle_update_shutdown_saves_and_closes_immediately(self, manager):
        gui, _ = self._make_gui()
        gui._save_current_settings = MagicMock()
        gui._preview_pool = MagicMock()
        manager.process = None

        gui._shutdown_for_update()

        manager.cancel_conversion.assert_not_called()
        gui._save_current_settings.assert_called_once_with()
        gui._preview_pool.shutdown.assert_called_once_with(
            wait=False, cancel_futures=True)
        gui.root.destroy.assert_called_once_with()

    @patch('src.gui.conversion_manager')
    def test_update_shutdown_waits_for_active_conversion_to_reap(self, manager):
        gui, _ = self._make_gui()
        gui._save_current_settings = MagicMock()
        gui._preview_pool = MagicMock()
        manager.process = MagicMock()
        manager.ready_for_shutdown.side_effect = [False, True]

        gui._shutdown_for_update()

        manager.cancel_conversion.assert_called_once_with()
        gui._save_current_settings.assert_not_called()
        gui.root.destroy.assert_not_called()
        check_reaped = gui.root.after.call_args.args[1]
        manager.process = None
        check_reaped()
        gui._save_current_settings.assert_called_once_with()
        gui._preview_pool.shutdown.assert_called_once_with(
            wait=False, cancel_futures=True)
        gui.root.destroy.assert_called_once_with()

    @patch('src.gui.conversion_manager')
    def test_update_shutdown_is_idempotent(self, manager):
        gui, _ = self._make_gui()
        gui._save_current_settings = MagicMock()
        gui._preview_pool = MagicMock()
        manager.process = MagicMock()
        manager.ready_for_shutdown.return_value = False

        gui._shutdown_for_update()
        gui._shutdown_for_update()

        manager.cancel_conversion.assert_called_once_with()
        self.assertEqual(gui.root.after.call_count, 1)

    @patch('src.gui.conversion_manager')
    def test_update_shutdown_waits_for_monitor_acknowledgement(self, manager):
        gui, _ = self._make_gui()
        gui._save_current_settings = MagicMock()
        gui._preview_pool = MagicMock()
        manager.process = None
        manager.ready_for_shutdown.side_effect = [False, True]

        gui._shutdown_for_update()

        gui._save_current_settings.assert_not_called()
        gui.root.destroy.assert_not_called()
        gui.root.after.call_args.args[1]()
        gui._save_current_settings.assert_called_once_with()
        gui.root.destroy.assert_called_once_with()

    def test_start_update_check_schedules_dialog_when_update_available(self):
        gui, _ = self._make_gui()
        found_event = threading.Event()

        def fake_after(delay, cb):
            cb()
            found_event.set()

        gui.root.after = fake_after

        release_url = updater.RELEASES_URL
        with patch('src.updater.check_for_update',
                   return_value=('4.0.0', _DOWNLOAD_URL, release_url, 123, 'a' * 64)):
            with patch('src.gui._UpdateDialog') as MockDialog:
                gui._start_update_check()
                found_event.wait(timeout=2)

        MockDialog.assert_called_once_with(
            gui.root, APP_VERSION, '4.0.0', _DOWNLOAD_URL, release_url,
            123, 'a' * 64, gui._shutdown_for_update,
        )

    def test_start_update_check_no_dialog_when_current(self):
        gui, _ = self._make_gui()
        dialog_called = threading.Event()

        def fake_after(delay, cb):
            cb()
            dialog_called.set()

        gui.root.after = fake_after

        with patch('src.updater.check_for_update', return_value=None):
            with patch('src.gui._UpdateDialog') as MockDialog:
                gui._start_update_check()
                # give the background thread a moment
                import time; time.sleep(0.3)

        MockDialog.assert_not_called()


# ── Version sync guard ─────────────────────────────────────────────────────────

class TestVersionSync(unittest.TestCase):
    """APP_VERSION in updater.py and #define AppVersion in installer.iss must always match.

    When releasing a new version, both must be bumped together:
      1. src/updater.py  — APP_VERSION = "X.Y.Z"
      2. installer.iss   — #define AppVersion  "X.Y.Z"
    This test fails the suite immediately if they drift apart.
    """

    _ISS = os.path.join(os.path.dirname(__file__), '..', 'installer.iss')

    def _iss_version(self) -> str:
        import re
        with open(self._ISS) as f:
            content = f.read()
        m = re.search(r'#define\s+AppVersion\s+"([^"]+)"', content)
        if not m:
            self.fail("Could not find '#define AppVersion' in installer.iss")
        return m.group(1)

    def test_updater_and_installer_versions_match(self):
        iss_ver = self._iss_version()
        self.assertEqual(
            APP_VERSION, iss_ver,
            f"Version mismatch: updater.py APP_VERSION={APP_VERSION!r} "
            f"but installer.iss AppVersion={iss_ver!r}. "
            f"Bump both files together when cutting a release."
        )


if __name__ == '__main__':
    unittest.main()
