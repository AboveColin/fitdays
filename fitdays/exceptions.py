"""
Custom exception classes for the Fitdays package.

A small hierarchy so callers (including the Home Assistant integration) can
tell authentication problems apart from transient network/API errors.
"""

from __future__ import annotations

from typing import Optional


class FitdaysError(Exception):
    """Base exception for all Fitdays errors."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class FitdaysAPIError(FitdaysError):
    """A non-success response from the Fitdays API."""

    def __init__(self, message: str, code: Optional[int] = None,
                 status_code: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def __str__(self) -> str:
        bits = []
        if self.code is not None:
            bits.append(f"code {self.code}")
        if self.status_code is not None:
            bits.append(f"HTTP {self.status_code}")
        if bits:
            return f"{self.message} ({', '.join(bits)})"
        return self.message


class FitdaysAuthError(FitdaysError):
    """Login failed, or the stored token is no longer accepted."""


class FitdaysNetworkError(FitdaysError):
    """Network-level failure talking to the Fitdays cloud."""


class FitdaysValidationError(FitdaysError):
    """The caller passed something the client cannot work with."""
