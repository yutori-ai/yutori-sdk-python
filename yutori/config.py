"""Configuration helpers for the Yutori SDK."""

from __future__ import annotations

DEFAULT_BASE_URL = "https://api.yutori.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0


def sanitize_base_url(url: str) -> str:
    """Ensure the base URL never ends with a trailing slash."""

    return url.rstrip("/")
