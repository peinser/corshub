r"""
Generic cryptographic utilities for use in authentication, security, and related contexts.

Functions:
---------

- otp(digits: int = 6) -> str
    Generates a numeric One-Time Password (OTP) of the specified number of digits (default: 6).
    The OTP is zero-padded to the requested length.

Usage:
-----

    code = otp()         # 6-digit OTP
    code = otp(8)        # 8-digit OTP

This module is intended for use in authentication flows, multi-factor verification, and other security-sensitive operations.
"""

from __future__ import annotations

import secrets


def otp(digits: int = 6) -> str:
    """
    Generate a numeric One-Time Password (OTP) of the specified length.

    Args:
        digits (int): Number of digits for the OTP (default: 6).

    Returns:
        str: A zero-padded string representing the OTP.

    Raises:
        ValueError: If digits is less than or equal to zero.
    """
    if digits <= 0:
        raise ValueError("At least a single digit is required.")

    return str(secrets.randbelow(10**digits)).zfill(digits)
