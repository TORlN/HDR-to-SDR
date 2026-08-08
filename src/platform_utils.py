"""OS-specific primitives: subprocess startup flags, app data directories,
DPI awareness, and GPU-name probing. Every sys.platform branch in the app
lives here, so a future macOS build has one file to open, not five.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def _startupinfo():
    """Return (startupinfo, creationflags) that suppress the console window on Windows."""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si, subprocess.CREATE_NO_WINDOW
    return None, 0


def settings_dir() -> str:
    """Where the app's settings directory lives, per OS."""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'HDR-to-SDR')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Application Support/HDR-to-SDR')
    return os.path.join(os.path.expanduser('~'), 'HDR-to-SDR')


def log_dir() -> str:
    """Where the app's log directory lives, per OS."""
    if sys.platform == 'win32':
        base = os.getenv('LOCALAPPDATA') or tempfile.gettempdir()
        return os.path.join(base, 'HDR to SDR')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Logs/HDR to SDR')
    return os.path.join(tempfile.gettempdir(), 'HDR to SDR')


def setup_dpi_awareness() -> None:
    """Enable Per-Monitor DPI awareness so Windows doesn't bitmap-scale the window."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


# gpu_name()'s vendor-encoder-name -> AdapterCompatibility substring(s), for
# scoping the WMI fallback to a known vendor.
_WMI_VENDOR_PATTERNS = {
    'h264_amf': 'AMD|Advanced Micro Devices',
    'h264_qsv': 'Intel',
}


def _nvidia_smi_name() -> str | None:
    """Return the NVIDIA GPU's name via nvidia-smi, or None if unusable."""
    try:
        si, flags = _startupinfo()
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            startupinfo=si,
            creationflags=flags,
            timeout=5,
        )
        first_line = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ''
        return first_line or None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _wmi_query(vendor_pattern: str | None) -> str | None:
    where_clause = "$_.Name -notmatch 'Virtual'"
    if vendor_pattern:
        where_clause += f" -and $_.AdapterCompatibility -match '{vendor_pattern}'"
    try:
        si, flags = _startupinfo()
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f"(Get-CimInstance Win32_VideoController | "
             f"Where-Object {{ {where_clause} }} | "
             "Select-Object -First 1 -ExpandProperty Name)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            startupinfo=si,
            creationflags=flags,
            timeout=5,
        )
        return result.stdout.strip() or None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _wmi_gpu_name(gpu_encoder: str | None) -> str | None:
    vendor_pattern = _WMI_VENDOR_PATTERNS.get(gpu_encoder or '')
    if vendor_pattern:
        name = _wmi_query(vendor_pattern)
        if name:
            return name
    return _wmi_query(None)


def gpu_name(nvidia_present: bool, gpu_encoder: str | None) -> str | None:
    """Return this machine's primary GPU name, or None if this platform or
    probe can't determine one. Windows only today -- see
    docs/superpowers/specs/2026-08-08-platform-utils-seam-design.md for why
    other platforms return None rather than a guess."""
    if sys.platform != 'win32':
        return None
    if nvidia_present:
        name = _nvidia_smi_name()
        if name:
            return name
    return _wmi_gpu_name(gpu_encoder)
