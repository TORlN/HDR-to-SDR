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
import io
import json
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch, call

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
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_response(body: bytes, content_length: int | None = None) -> MagicMock:
    """Fake urllib response context manager."""
    resp = MagicMock()
    resp.read.side_effect = [body, b'']
    headers = {}
    if content_length is not None:
        headers['Content-Length'] = str(content_length)
    resp.headers = headers
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _github_payload(tag: str, asset_name: str = 'HDR_to_SDR_Setup.exe',
                    url: str = 'https://example.com/HDR_to_SDR_Setup.exe') -> bytes:
    return json.dumps({
        'tag_name': tag,
        'assets': [{'name': asset_name, 'browser_download_url': url}],
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

    def test_equal_versions(self):
        self.assertEqual(_version_tuple('3.0.0'), _version_tuple('3.0.0'))


# ── check_for_update ───────────────────────────────────────────────────────────

class TestCheckForUpdate(unittest.TestCase):

    def _patch_urlopen(self, payload: bytes):
        return patch('urllib.request.urlopen',
                     return_value=_make_response(payload))

    def test_newer_version_returns_version_and_url(self):
        newer_tag = 'v99.0.0'
        expected_url = 'https://example.com/HDR_to_SDR_Setup.exe'
        with self._patch_urlopen(_github_payload(newer_tag, url=expected_url)):
            result = check_for_update()
        self.assertIsNotNone(result)
        assert result is not None
        new_ver, url, release_url = result
        self.assertEqual(new_ver, '99.0.0')
        self.assertEqual(url, expected_url)
        self.assertEqual(release_url, updater.RELEASES_URL)

    def test_same_version_returns_none(self):
        with self._patch_urlopen(_github_payload(f'v{APP_VERSION}')):
            result = check_for_update()
        self.assertIsNone(result)

    def test_older_version_returns_none(self):
        with self._patch_urlopen(_github_payload('v0.0.1')):
            result = check_for_update()
        self.assertIsNone(result)

    def test_network_error_returns_none(self):
        with patch('urllib.request.urlopen', side_effect=OSError('no route')):
            result = check_for_update()
        self.assertIsNone(result)

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

    def test_malformed_json_returns_none(self):
        resp = MagicMock()
        resp.read.return_value = b'not json'
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        with patch('urllib.request.urlopen', return_value=resp):
            result = check_for_update()
        self.assertIsNone(result)

    def test_timeout_is_set(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError) as m:
            check_for_update()
        _, kwargs = m.call_args
        self.assertEqual(kwargs.get('timeout'), 10)


# ── download_installer ─────────────────────────────────────────────────────────

class TestDownloadInstaller(unittest.TestCase):

    def _fake_urlopen(self, data: bytes):
        """Produces a urlopen mock that yields *data* in one chunk."""
        resp = MagicMock()
        resp.read.side_effect = [data, b'']
        resp.headers = {'Content-Length': str(len(data))}
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return patch('urllib.request.urlopen', return_value=resp)

    def test_writes_content_to_file(self):
        content = b'fake installer binary'
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as tmp:
            tmp_path = tmp.name
        try:
            with self._fake_urlopen(content):
                download_installer('https://example.com/HDR_to_SDR_Setup.exe', tmp_path)
            with open(tmp_path, 'rb') as f:
                self.assertEqual(f.read(), content)
        finally:
            os.unlink(tmp_path)

    def test_progress_callback_called(self):
        content = b'x' * 200
        calls: list[tuple[int, int]] = []
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as tmp:
            tmp_path = tmp.name
        try:
            with self._fake_urlopen(content):
                download_installer(
                    'https://example.com/HDR_to_SDR_Setup.exe',
                    tmp_path,
                    progress_cb=lambda d, t: calls.append((d, t)),
                )
            self.assertTrue(len(calls) > 0)
            self.assertEqual(calls[-1][0], len(content))  # final downloaded == total
        finally:
            os.unlink(tmp_path)

    def test_no_progress_callback_is_ok(self):
        content = b'data'
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as tmp:
            tmp_path = tmp.name
        try:
            with self._fake_urlopen(content):
                download_installer('https://example.com/HDR_to_SDR_Setup.exe', tmp_path)
        finally:
            os.unlink(tmp_path)


# ── launch_installer ───────────────────────────────────────────────────────────

class TestLaunchInstaller(unittest.TestCase):

    @unittest.skipUnless(sys.platform == "win32", "Windows-only creationflags")
    def test_calls_popen_with_detached_flags(self):
        with patch('subprocess.Popen') as mock_popen:
            launch_installer(r'C:\tmp\HDR_to_SDR_Setup.exe')
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], [r'C:\tmp\HDR_to_SDR_Setup.exe'])
        flags = kwargs.get('creationflags', 0)
        import subprocess
        self.assertTrue(flags & subprocess.DETACHED_PROCESS)
        self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)

    def test_close_fds_true(self):
        with patch('subprocess.Popen') as mock_popen:
            launch_installer('setup.exe')
        _, kwargs = mock_popen.call_args
        self.assertTrue(kwargs.get('close_fds'))


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
        with patch('src.gui._UpdateDialog') as MockDialog:
            gui._show_update_dialog('3.0.0', '4.0.0', 'https://example.com/setup.exe',
                                     updater.RELEASES_URL)
        MockDialog.assert_called_once_with(gui.root, '3.0.0', '4.0.0', 'https://example.com/setup.exe',
                                            updater.RELEASES_URL)

    def test_start_update_check_schedules_dialog_when_update_available(self):
        gui, _ = self._make_gui()
        found_event = threading.Event()

        def fake_after(delay, cb):
            cb()
            found_event.set()

        gui.root.after = fake_after

        release_url = updater.RELEASES_URL
        with patch('src.updater.check_for_update',
                   return_value=('4.0.0', 'https://example.com/setup.exe', release_url)):
            with patch('src.gui._UpdateDialog') as MockDialog:
                gui._start_update_check()
                found_event.wait(timeout=2)

        MockDialog.assert_called_once_with(gui.root, APP_VERSION, '4.0.0',
                                            'https://example.com/setup.exe', release_url)

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
