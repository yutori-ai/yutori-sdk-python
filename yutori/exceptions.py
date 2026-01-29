"""Custom exceptions raised by the Yutori SDK."""

from __future__ import annotations

from typing import Any, Optional


class YutoriSDKError(Exception):
    """Base exception for all SDK specific failures."""


class AuthenticationError(YutoriSDKError):
    """Raised when an API key is missing or rejected by the server."""


class APIError(YutoriSDKError):
    """Raised when the Yutori API returns a non-successful response."""

    def __init__(self, message: str, status_code: int, response: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response

    def __str__(self) -> str:  # pragma: no cover - repr helper
        return f"{self.status_code}: {self.message}"
