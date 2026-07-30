"""Licensing exception hierarchy.

A leaf module with no imports of its own. It exists so that both the public
``licensing`` façade and the private ``pro.licensing`` implementation can import
these classes without creating a circular import between them.
"""
from __future__ import annotations


class LicenseError(Exception):
    """Base class for all licensing errors."""


class InvalidKeyError(LicenseError):
    """The license key is not recognised or has been revoked."""


class DeviceLimitError(LicenseError):
    """This license key has reached its maximum number of activated devices."""


class NetworkError(LicenseError):
    """The licensing server could not be reached."""
