"""Auto-update helper: queries GitHub releases and downloads the installer."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from typing import Callable

APP_VERSION = "3.1.4"
_GITHUB_API = "https://api.github.com/repos/TORlN/HDR-to-SDR/releases/latest"
_ASSET_NAME = "HDR_to_SDR_Setup.exe"
RELEASES_URL = "https://github.com/TORlN/HDR-to-SDR/releases"
_HEADERS = {
    "User-Agent": "HDR-to-SDR-Updater/1.0",
    "Accept": "application/vnd.github+json",
}


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def check_for_update() -> tuple[str, str, str] | None:
    """Return (new_version_str, download_url, release_url) if a newer release
    exists, else None. release_url points to the GitHub releases page, for
    linking to the changelog.

    Silently returns None on any network or parse error so callers never crash.
    """
    try:
        req = urllib.request.Request(_GITHUB_API, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag: str = data.get("tag_name", "")
        if not tag:
            return None
        if _version_tuple(tag) <= _version_tuple(APP_VERSION):
            return None
        assets: list[dict] = data.get("assets", [])
        url = next(
            (a["browser_download_url"] for a in assets if a["name"] == _ASSET_NAME),
            None,
        )
        if not url:
            return None
        return tag.lstrip("v"), url, RELEASES_URL
    except Exception:
        return None


def download_installer(
    url: str,
    dest_path: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    """Download *url* to *dest_path*, calling *progress_cb(downloaded, total)* each chunk."""
    req = urllib.request.Request(url, headers={"User-Agent": "HDR-to-SDR-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


def launch_installer(path: str) -> None:
    """Launch the installer detached so it survives the parent process exiting."""
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    subprocess.Popen([path], **kwargs)
