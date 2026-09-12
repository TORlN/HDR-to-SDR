"""Public licensing façade.

Delegates to the private ``pro.licensing`` implementation when it is present,
and degrades to Community Edition behavior when it is not. The real Lemon
Squeezy activation flow and token cryptography live in a separate private repo
nested at ``src/pro/`` and are deliberately not published.

A build without ``pro/`` is the free edition: nothing can be activated and
``check_license()`` is always False. ``build_installer.bat`` refuses to produce
such an installer without an explicit typed confirmation.
"""
from __future__ import annotations

import importlib
from typing import Callable, Optional

from license_errors import (
    DeviceLimitError,
    InvalidKeyError,
    LicenseError,
    LicenseStorageError,
    NetworkError,
)

__all__ = [
    'DeviceLimitError',
    'InvalidKeyError',
    'LicenseError',
    'LicenseStorageError',
    'NetworkError',
    'activate_license',
    'check_license',
    'check_license_nonblocking',
    'deactivate_license',
]

# Imported as a module object, not `from pro.licensing import (...)` -- same
# pyright unresolved-import trick as src/gui.py/src/dialogs.py.
try:
    _pro = importlib.import_module('pro.licensing')
except ModuleNotFoundError as exc:
    if exc.name not in {'pro', 'pro.licensing'}:
        raise
    _pro = None

if _pro is not None:
    activate_license = _pro.activate_license
    check_license = _pro.check_license
    check_license_nonblocking = _pro.check_license_nonblocking
    deactivate_license = _pro.deactivate_license
else:

    def check_license() -> bool:
        """Always False: this build contains no licensing backend."""
        return False

    def check_license_nonblocking(
        on_change: Optional[Callable[[bool], None]] = None,
    ) -> bool:
        """Always False. Accepts on_change for signature parity with the Pro
        implementation -- main.pyw always passes it -- and never invokes it,
        because the answer can never change in a free build."""
        return False

    def activate_license(key: str) -> None:
        raise InvalidKeyError(
            'This build does not include Pro licensing. '
            'Get the full version at https://hdrtosdr.com/#pricing'
        )

    def deactivate_license() -> bool:
        return False
