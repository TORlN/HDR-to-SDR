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

from typing import Callable, Optional

from license_errors import (
    DeviceLimitError,
    InvalidKeyError,
    LicenseError,
    NetworkError,
)

__all__ = [
    'DeviceLimitError',
    'InvalidKeyError',
    'LicenseError',
    'NetworkError',
    'activate_license',
    'check_license',
    'check_license_nonblocking',
    'deactivate_license',
]

try:
    from pro.licensing import (  # type: ignore[import-not-found]
        activate_license,
        check_license,
        check_license_nonblocking,
        deactivate_license,
    )
except ImportError:  # Community Edition — no Pro backend in this build.

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
