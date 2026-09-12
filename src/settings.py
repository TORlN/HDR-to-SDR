import json
import logging
import math
import os

from platform_utils import settings_dir

SETTINGS_DIR = settings_dir()
SETTINGS_FILE = os.path.join(SETTINGS_DIR, 'settings.json')

DEFAULTS = {
    'gamma': 1.0,
    'tonemapper': 'Mobius',
    'open_after_conversion': False,
    'display_preview': True,
    'quality': 23,  # encoder quality (CRF for CPU / CQ for GPU); lower = better
    'quality_mode': 'cq',          # 'cq' (Constant Quality) | 'bitrate' (Target Bitrate)
    'quality_bitrate_kbps': 8000,  # last chosen Target Bitrate value, in kbps
    'filetype': 'MP4',
    # BT.2020->BT.709 gamut LUT on GPU exports (~2x slower at 4K, see
    # build_libplacebo_filter); no effect on CPU exports, which always apply it.
    'lut_enabled': True,
}

_TONEMAPPERS = frozenset({'Reinhard', 'Mobius', 'Hable', 'BT.2390', 'Spline'})
_QUALITY_MODES = frozenset({'cq', 'bitrate'})
_FILETYPES = frozenset({'MP4', 'MKV', 'MOV'})


def _is_finite_number(value, minimum, maximum):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def _is_valid_setting(key, value):
    if key == 'gamma':
        return _is_finite_number(value, 0.1, 3.0)
    if key == 'tonemapper':
        return type(value) is str and value in _TONEMAPPERS
    if key in {'open_after_conversion', 'display_preview', 'lut_enabled'}:
        return type(value) is bool
    if key == 'quality':
        return type(value) is int and 15 <= value <= 30
    if key == 'quality_mode':
        return type(value) is str and value in _QUALITY_MODES
    if key == 'quality_bitrate_kbps':
        return type(value) is int and value >= 1000
    if key == 'filetype':
        return type(value) is str and value in _FILETYPES
    return False


def load_settings():
    """Return saved settings, filling any missing keys with defaults."""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        return {
            key: data[key] if key in data and _is_valid_setting(key, data[key]) else default
            for key, default in DEFAULTS.items()
        }
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    """Write settings to disk, silently ignoring I/O errors.

    Writes to a temp file in the same directory and atomically replaces the
    real file, so a crash or serialization error mid-write can't leave a
    truncated settings.json -- load_settings would otherwise treat that as
    corrupt and silently wipe every saved preference back to defaults.
    """
    tmp_file = SETTINGS_FILE + '.tmp'
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        payload = {k: settings[k] for k in DEFAULTS if k in settings}
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_file, SETTINGS_FILE)
    except (OSError, TypeError, ValueError) as e:
        logging.warning("Could not save settings: %s", e)
        try:
            os.remove(tmp_file)
        except OSError:
            pass
