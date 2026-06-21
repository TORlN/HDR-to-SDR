"""Node-locked software licensing with 72-hour offline grace period.

Token format (license.dat):
  { "payload": "<JSON string>", "sig": "<HMAC-SHA256 hex>" }

The payload JSON contains the license key, machine fingerprint, and the Unix
timestamp of the last successful online validation.  The HMAC key is derived
from the hardware fingerprint, so the token is both machine-locked and
tamper-proof without requiring any external crypto library.

API shape targets Keygen.sh — set LICENSE_API_ENDPOINT via environment variable
to point at your Lemon Squeezy or self-hosted endpoint.
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
import urllib.request
import uuid
from typing import Optional

from settings import SETTINGS_DIR

logger = logging.getLogger(__name__)

# ── Storage ────────────────────────────────────────────────────────────────────
LICENSE_FILE = os.path.join(SETTINGS_DIR, 'license.dat')
GRACE_PERIOD_SECONDS = 72 * 3600  # 72 hours

# ── API ────────────────────────────────────────────────────────────────────────
# Override via env var for staging / different licensing providers.
_API_ENDPOINT = os.environ.get(
    'LICENSE_API_ENDPOINT',
    'https://api.keygen.sh/v1/accounts/YOUR_ACCOUNT_ID/licenses/actions/validate-key',
)
_API_TIMEOUT = 10  # seconds

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
    The result is deterministic across calls on the same machine.
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
    """Derive a 32-byte machine-specific signing key."""
    return hashlib.sha256(fingerprint.encode('utf-8')).digest()


def _sign(payload_json: str, fingerprint: str) -> str:
    """Return HMAC-SHA256 hex digest of *payload_json* keyed to this machine."""
    return hmac.new(
        _hmac_key(fingerprint),
        payload_json.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def save_license_token(key: str) -> None:
    """Persist a tamper-proof, machine-locked validation token.

    The token records the license key, this machine's fingerprint, and the
    current timestamp.  The HMAC prevents modification on a different machine
    or tampering of any field.
    """
    fingerprint = get_hardware_fingerprint()
    payload = json.dumps(
        {
            'key': key,
            'fingerprint': fingerprint,
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
    corrupt, tampered with, or belongs to a different machine.
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

    return payload


# ── API call ───────────────────────────────────────────────────────────────────

def _call_api(key: str, fingerprint: str) -> dict:  # type: ignore[type-arg]
    """POST a validation request to the licensing API.

    Returns the parsed JSON response dict.
    Raises NetworkError on any connectivity or timeout failure.
    For Keygen.sh, 4xx responses carry a JSON body with meta.valid = false;
    we parse those the same as 2xx so _parse_api_response handles them uniformly.
    """
    body = json.dumps(
        {'meta': {'key': key, 'scope': {'fingerprint': fingerprint}}}
    ).encode('utf-8')
    req = urllib.request.Request(
        _API_ENDPOINT,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
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


def _parse_api_response(response: dict) -> None:  # type: ignore[type-arg]
    """Raise the appropriate LicenseError for any non-valid API response."""
    meta = response.get('meta', {})
    if meta.get('valid'):
        return
    code: str = meta.get('code', 'UNKNOWN')
    detail: str = meta.get('detail', 'License validation failed')
    if code in ('TOO_MANY_MACHINES', 'FINGERPRINT_SCOPE_MISMATCH'):
        raise DeviceLimitError(detail)
    if code in ('NOT_FOUND', 'SUSPENDED', 'EXPIRED', 'OVERDUE'):
        raise InvalidKeyError(detail)
    raise LicenseError(f"{code}: {detail}")


# ── Public interface ────────────────────────────────────────────────────────────

def activate_license(key: str) -> None:
    """Validate *key* against the remote API and persist a local token.

    Raises:
        InvalidKeyError: key not found, suspended, or expired.
        DeviceLimitError: machine quota for this license is full.
        NetworkError: server unreachable.
    """
    key = key.strip()
    if not key:
        raise InvalidKeyError("License key cannot be empty")
    fingerprint = get_hardware_fingerprint()
    response = _call_api(key, fingerprint)
    _parse_api_response(response)
    save_license_token(key)


def check_license() -> bool:
    """Return True when a valid license is confirmed.

    Fast path: a local token validated within the last 72 hours is accepted
    without a network call.

    Slow path: if the token is older than 72 hours, re-validates online.
    On network failure the grace period is considered expired and False is
    returned, forcing the user to re-enter their key.
    """
    with _lock:
        payload = load_license_token()

    if payload is None:
        return False

    age = time.time() - float(payload.get('validated_at', 0))
    if age <= GRACE_PERIOD_SECONDS:
        return True

    # Grace period exceeded — require a fresh API check.
    try:
        fingerprint = get_hardware_fingerprint()
        response = _call_api(payload['key'], fingerprint)
        _parse_api_response(response)
        save_license_token(payload['key'])
        return True
    except NetworkError:
        logger.warning(
            "License grace period expired and licensing server is unreachable"
        )
        return False
    except LicenseError:
        return False
