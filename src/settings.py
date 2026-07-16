import json
import logging
import os

SETTINGS_DIR = os.path.join(
    os.environ.get('APPDATA') or os.path.expanduser('~'),
    'HDR-to-SDR',
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, 'settings.json')

DEFAULTS = {
    'gamma': 1.0,
    'tonemapper': 'Mobius',
    'gpu_accel': False,
    'open_after_conversion': False,
    'display_preview': True,
    'quality': 23,  # encoder quality (CRF for CPU / CQ for GPU); lower = better
    'quality_mode': 'cq',          # 'cq' (Constant Quality) | 'bitrate' (Target Bitrate)
    'quality_bitrate_kbps': 8000,  # last chosen Target Bitrate value, in kbps
    'filetype': 'MP4',
}


def load_settings():
    """Return saved settings, filling any missing keys with defaults."""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {**DEFAULTS, **{k: data[k] for k in DEFAULTS if k in data}}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
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
