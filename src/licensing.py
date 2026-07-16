"""Node-locked software licensing.

A valid local token is trusted indefinitely while offline -- a paid user is
never locked out just because this machine can't reach Lemon Squeezy. Every
30 days (_REFRESH_COOLDOWN) the app makes a best-effort attempt to
re-validate online when it can, so an explicitly revoked/refunded key is
still caught (and unlocked) the next time this machine has connectivity.

Token format (license.dat):
  { "payload": "<JSON string>", "sig": "<HMAC-SHA256 hex>" }

The payload JSON contains the license key, this machine's hardware fingerprint,
the Lemon Squeezy instance_id (returned on activation), and the Unix timestamp
of the last successful online validation.  The HMAC prevents tampering without
requiring any external crypto library.

Lemon Squeezy license API (base: https://api.lemonsqueezy.com/v1/licenses):
  POST .../activate   body: license_key + instance_name  → returns instance.id
  POST .../validate   body: license_key + instance_id    → returns valid bool
  POST .../deactivate body: license_key + instance_id    → frees the slot
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional

from settings import SETTINGS_DIR

logger = logging.getLogger(__name__)

# ── Storage ────────────────────────────────────────────────────────────────────
LICENSE_FILE = os.path.join(SETTINGS_DIR, 'license.dat')

# ── API ────────────────────────────────────────────────────────────────────────
_LS_API_BASE = os.environ.get(
    'LICENSE_API_ENDPOINT',
    'https://api.lemonsqueezy.com/v1/licenses',
)
_API_TIMEOUT = 10
_REFRESH_COOLDOWN = 30 * 24 * 3600  # re-validate against LS every 30 days

_lock = threading.Lock()


# ── Exceptions ─────────────────────────────────────────────────────────────────

class LicenseError(Exception):
    """Base class for all licensing errors."""


class InvalidKeyError(LicenseError):
    """The license key is not recognised or has been revoked."""


class DeviceLimitError(LicenseError):
    """This license key has reached its maximum number of activated devices."""


class NetworkError(LicenseError):
    """The licensing server could not be reached."""


# ── Hardware fingerprint ───────────────────────────────────────────────────────

def get_hardware_fingerprint() -> str:
    """Return a SHA-256 hex digest derived from stable hardware identifiers.

    Uses the primary MAC address, hostname, CPU architecture, and OS family.
    The result is deterministic across calls on the same machine, and serves as
    the instance_name sent to Lemon Squeezy — so the same machine always maps
    to the same identifier and never burns an extra activation slot on re-install.
    """
    parts = [
        str(uuid.getnode()),   # primary MAC address as a 48-bit integer
        platform.node(),       # hostname
        platform.machine(),    # x86_64 / AMD64 / arm64 …
        platform.system(),     # Windows / Linux / Darwin
    ]
    raw = '|'.join(parts).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


# ── Token helpers ──────────────────────────────────────────────────────────────

def _hmac_key(fingerprint: str) -> bytes:
    return hashlib.sha256(fingerprint.encode('utf-8')).digest()


def _sign(payload_json: str, fingerprint: str) -> str:
    return hmac.new(
        _hmac_key(fingerprint),
        payload_json.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def save_license_token(key: str, instance_id: str) -> None:
    """Persist a tamper-proof, machine-locked validation token."""
    fingerprint = get_hardware_fingerprint()
    payload = json.dumps(
        {
            'key': key,
            'fingerprint': fingerprint,
            'instance_id': instance_id,
            'validated_at': int(time.time()),
        },
        separators=(',', ':'),
        sort_keys=True,
    )
    sig = _sign(payload, fingerprint)
    token_str = json.dumps({'payload': payload, 'sig': sig})
    with _lock:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(LICENSE_FILE, 'w', encoding='utf-8') as f:
            f.write(token_str)


def load_license_token() -> Optional[dict]:  # type: ignore[type-arg]
    """Read and cryptographically verify the local token.

    Returns the payload dict on success, or None if the file is absent,
    corrupt, tampered with, bound to a different machine, or missing
    instance_id (i.e. a legacy keygen.sh-era token).
    """
    try:
        with open(LICENSE_FILE, 'r', encoding='utf-8') as f:
            token = json.load(f)
        payload_json: str = token['payload']
        stored_sig: str = token['sig']
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None

    fingerprint = get_hardware_fingerprint()
    expected_sig = _sign(payload_json, fingerprint)

    if not hmac.compare_digest(stored_sig, expected_sig):
        logger.warning("License token HMAC mismatch — file tampered or machine changed")
        return None

    try:
        payload: dict = json.loads(payload_json)  # type: ignore[type-arg]
    except json.JSONDecodeError:
        return None

    if payload.get('fingerprint') != fingerprint:
        logger.warning("License token fingerprint does not match this machine")
        return None

    # Tokens without instance_id are from the legacy keygen.sh scheme — reject them.
    if not payload.get('instance_id'):
        return None

    return payload


# ── Lemon Squeezy API layer ────────────────────────────────────────────────────

def _ls_post(endpoint: str, body: dict) -> dict:  # type: ignore[type-arg]
    """POST form-encoded data to a Lemon Squeezy license endpoint.

    Returns the parsed JSON response dict.
    Raises NetworkError on connectivity or timeout failures.
    LS sometimes returns 4xx with a JSON body (e.g. invalid key) — we parse
    those the same as 2xx so callers handle them uniformly.
    """
    data = urllib.parse.urlencode(body).encode('utf-8')
    req = urllib.request.Request(
        f'{_LS_API_BASE}/{endpoint}',
        data=data,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept':       'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode('utf-8'))
        except json.JSONDecodeError:
            raise NetworkError(f"HTTP {exc.code} with non-JSON body") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise NetworkError(str(exc)) from exc


def _ls_activate(key: str, fingerprint: str) -> str:
    """POST /activate → returns the instance_id string assigned by Lemon Squeezy.

    The hardware fingerprint is used as the instance_name so the same machine
    always maps to the same identifier in the LS dashboard.

    Raises InvalidKeyError, DeviceLimitError, or NetworkError.
    """
    result = _ls_post('activate', {
        'license_key':   key,
        'instance_name': fingerprint,
    })
    if result.get('activated'):
        return result['instance']['id']

    error: str = result.get('error', 'Activation failed')
    if 'exceeded' in error.lower() or 'limit' in error.lower():
        raise DeviceLimitError(error)
    raise InvalidKeyError(error)


def _ls_validate(key: str, instance_id: str) -> None:
    """POST /validate. Raises InvalidKeyError if the key is revoked or expired.

    Raises NetworkError if the server is unreachable.
    """
    result = _ls_post('validate', {
        'license_key': key,
        'instance_id': instance_id,
    })
    if result.get('valid'):
        return
    raise InvalidKeyError(result.get('error', 'License is not valid'))


def _ls_deactivate(key: str, instance_id: str) -> None:
    """POST /deactivate. Best-effort — swallows API errors but raises NetworkError."""
    result = _ls_post('deactivate', {
        'license_key': key,
        'instance_id': instance_id,
    })
    if not result.get('deactivated'):
        logger.warning("LS deactivate returned deactivated=false: %s", result.get('error'))


# ── Public interface ────────────────────────────────────────────────────────────

def activate_license(key: str) -> None:
    """Validate *key* against Lemon Squeezy and persist a local token.

    If this machine already has a valid token for this exact key, the existing
    instance_id is reused and only a validate call is made — no new activation
    slot is consumed.

    Raises:
        InvalidKeyError: key not found, suspended, or expired.
        DeviceLimitError: activation limit reached for this license.
        NetworkError: server unreachable.
    """
    key = key.strip()
    if not key:
        raise InvalidKeyError("License key cannot be empty")

    fingerprint = get_hardware_fingerprint()

    # Reuse existing activation when the same key is re-entered on the same machine.
    with _lock:
        existing = load_license_token()
    if existing and existing.get('key') == key:
        instance_id: str = existing['instance_id']
        try:
            _ls_validate(key, instance_id)
        except LicenseError:
            # The server just told us this key is no longer valid -- clear
            # the stale local token instead of leaving it in place, or
            # check_license() would keep trusting it until the next
            # cooldown-triggered refresh (up to 30 days later).
            with _lock:
                try:
                    os.remove(LICENSE_FILE)
                except OSError:
                    pass
            raise
        save_license_token(key, instance_id)
        return

    instance_id = _ls_activate(key, fingerprint)
    save_license_token(key, instance_id)


def deactivate_license() -> bool:
    """Deactivate this machine's license via the Lemon Squeezy API and clear the local token.

    Returns True if the local token was cleared (regardless of whether the API
    call succeeded — we always remove the local token so the machine is unlocked).
    Returns False if no license token exists.
    """
    with _lock:
        payload = load_license_token()

    if payload is None:
        return False

    try:
        _ls_deactivate(payload['key'], payload['instance_id'])
    except (NetworkError, LicenseError) as exc:
        logger.warning("Could not deactivate with LS (will clear token anyway): %s", exc)

    with _lock:
        try:
            os.remove(LICENSE_FILE)
        except OSError:
            pass
    return True


def check_license() -> bool:
    """Return True when a valid hardware-bound token exists.

    A paid user is never blocked by network failures — the token is accepted
    as long as it is present, HMAC-valid, and bound to this machine.

    When online and the token is older than 30 days, silently refreshes the
    timestamp.  Only returns False when the key has been explicitly
    revoked/invalidated by the server (not merely unreachable).
    """
    if os.environ.get('HDRSDR_DEV_UNLOCK') == '1':
        return True

    with _lock:
        payload = load_license_token()

    if payload is None:
        return False

    age = int(time.time()) - payload.get('validated_at', 0)
    if age < _REFRESH_COOLDOWN:
        return True

    key: str = payload['key']
    instance_id: str = payload['instance_id']

    try:
        _ls_validate(key, instance_id)
        save_license_token(key, instance_id)
    except NetworkError:
        pass  # offline — trust the local token
    except LicenseError:
        with _lock:
            try:
                os.remove(LICENSE_FILE)
            except OSError:
                pass
        return False

    return True
