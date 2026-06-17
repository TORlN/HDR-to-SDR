import json
import logging
import os

_SETTINGS_DIR = os.path.join(
    os.environ.get('APPDATA') or os.path.expanduser('~'),
    'HDR-to-SDR',
)
SETTINGS_FILE = os.path.join(_SETTINGS_DIR, 'settings.json')

DEFAULTS = {
    'gamma': 1.0,
    'filter': 'Dynamic',
    'tonemapper': 'Mobius',
    'gpu_accel': False,
    'open_after_conversion': False,
    'display_preview': True,
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
    """Write settings to disk, silently ignoring I/O errors."""
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        payload = {k: settings[k] for k in DEFAULTS if k in settings}
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
    except (OSError, TypeError, ValueError) as e:
        logging.warning("Could not save settings: %s", e)
