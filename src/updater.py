"""Auto-update helper: queries GitHub releases and downloads the installer."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

APP_VERSION = "3.2.3"
_GITHUB_API = "https://api.github.com/repos/TORlN/HDR-to-SDR/releases/latest"
_ASSET_NAME = "HDR_to_SDR_Setup.exe"
_MAX_METADATA_BYTES = 1_048_576
_MAX_INSTALLER_BYTES = 512 * 1_048_576
_STALE_UPDATE_DIRECTORY_SECONDS = 24 * 60 * 60
_EXPECTED_PUBLISHER = "CN=Torin Nelson, O=Torin Nelson, L=Irvine, S=ca, C=US"
_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
RELEASES_URL = "https://github.com/TORlN/HDR-to-SDR/releases"
_HEADERS = {
    "User-Agent": "HDR-to-SDR-Updater/1.0",
    "Accept": "application/vnd.github+json",
    "Accept-Encoding": "identity",
    "X-GitHub-Api-Version": "2022-11-28",
}


def cleanup_stale_update_directories() -> None:
    """Remove leftover installers from completed or abandoned updates."""
    try:
        with os.scandir(tempfile.gettempdir()) as entries:
            for entry in entries:
                if (
                    entry.name.startswith("hdr_to_sdr_update_")
                    and entry.is_dir(follow_symlinks=False)
                    and time.time() - entry.stat(follow_symlinks=False).st_mtime
                    >= _STALE_UPDATE_DIRECTORY_SECONDS
                ):
                    shutil.rmtree(entry.path, ignore_errors=True)
    except OSError:
        pass

logger = logging.getLogger(__name__)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _validate_initial_download_url(url: str, tag: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    expected_path = (
        f"/TORlN/HDR-to-SDR/releases/download/"
        f"{urllib.parse.quote(tag, safe='')}/{_ASSET_NAME}"
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("untrusted installer download URL")


def _validate_response_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise ValueError("installer download followed an untrusted redirect")


def check_for_update() -> tuple[str, str, str, int, str] | None:
    """Return trusted update metadata for a newer release, else None.

    Silently returns None on any network or parse error so callers never crash.
    """
    try:
        req = urllib.request.Request(_GITHUB_API, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.geturl() != _GITHUB_API:
                raise ValueError("update metadata came from an untrusted URL")
            body = resp.read(_MAX_METADATA_BYTES + 1)
        if len(body) > _MAX_METADATA_BYTES:
            raise ValueError("GitHub release metadata is too large")
        data = json.loads(body)
        tag: str = data.get("tag_name", "")
        if not tag:
            return None
        if _version_tuple(tag) <= _version_tuple(APP_VERSION):
            return None
        assets: list[dict] = data.get("assets", [])
        asset = next((a for a in assets if a.get("name") == _ASSET_NAME), None)
        if asset is None:
            return None
        url = asset.get("browser_download_url")
        size = asset.get("size")
        digest = asset.get("digest")
        if (
            asset.get("state") != "uploaded"
            or not isinstance(url, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 < size <= _MAX_INSTALLER_BYTES
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) is None
        ):
            return None
        _validate_initial_download_url(url, tag)
        return tag.lstrip("v"), url, RELEASES_URL, size, digest[7:].lower()
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            logger.warning(
                "Update check skipped: GitHub API rate limit exceeded (resets at %s)",
                e.headers.get("X-RateLimit-Reset"),
            )
        else:
            logger.warning("Update check failed: HTTP %s %s", e.code, e.reason)
        return None
    except Exception as e:
        logger.warning("Update check failed: %s", e)
        return None


def download_installer(
    url: str,
    dest_path: str,
    expected_size: int,
    expected_sha256: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    """Download and authenticate an installer before returning."""
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or not 0 < expected_size <= _MAX_INSTALLER_BYTES
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None
    ):
        raise ValueError("invalid expected installer size or SHA-256")
    _validate_initial_download_url(url, urllib.parse.unquote(
        urllib.parse.urlsplit(url).path.split('/')[-2]
    ))
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "HDR-to-SDR-Updater/1.0",
            "Accept-Encoding": "identity",
        },
    )
    created = False
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            _validate_response_url(resp.geturl())
            content_encoding = resp.headers.get("Content-Encoding")
            if content_encoding and content_encoding.lower() != "identity":
                raise ValueError("unexpected installer response encoding")
            content_length = resp.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid installer Content-Length") from exc
                if declared_size != expected_size:
                    raise ValueError("installer Content-Length does not match metadata")

            downloaded = 0
            hasher = hashlib.sha256()
            created = True
            with open(dest_path, "wb") as output:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > expected_size:
                        raise ValueError("installer size exceeds metadata")
                    output.write(chunk)
                    hasher.update(chunk)
                    if progress_cb:
                        progress_cb(downloaded, expected_size)
            if downloaded != expected_size:
                raise ValueError("installer size does not match metadata")
            if hasher.hexdigest() != expected_sha256.lower():
                raise ValueError("installer SHA-256 does not match metadata")
        _verify_installer_signature(dest_path)
    except Exception:
        if created:
            try:
                os.remove(dest_path)
            except OSError:
                pass
        raise


def _verify_installer_signature(path: str) -> None:
    """Require a valid Authenticode signature from the expected publisher."""
    if sys.platform != "win32":
        raise ValueError("installer signature verification requires Windows")
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise ValueError("installer signature verification is unavailable")
    powershell = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    if not os.path.isfile(powershell):
        raise ValueError("installer signature verification is unavailable")
    script = (
        "$ErrorActionPreference='Stop';"
        "$module=Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\"
        "Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1';"
        "Import-Module -Name $module -ErrorAction Stop;"
        "$s=Get-AuthenticodeSignature -LiteralPath $env:HDRSDR_INSTALLER_PATH;"
        "$subject=if($null -ne $s.SignerCertificate){"
        "$s.SignerCertificate.Subject}else{$null};"
        "[pscustomobject]@{Status=[string]$s.Status;Subject=$subject}|"
        "ConvertTo-Json -Compress"
    )
    environment = os.environ.copy()
    environment["HDRSDR_INSTALLER_PATH"] = os.path.abspath(path)
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=environment,
        )
        signature = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("installer signature verification failed") from exc
    if (
        signature.get("Status") != "Valid"
        or signature.get("Subject") != _EXPECTED_PUBLISHER
    ):
        raise ValueError("installer signature or publisher is invalid")


def launch_installer(path: str) -> None:
    """Launch the installer detached so it survives the parent process exiting."""
    _verify_installer_signature(path)
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    subprocess.Popen([path], **kwargs)
