"""Shared HTTP request utilities for sync and async clients."""

from __future__ import annotations

from typing import Any

import httpx

from .exceptions import APIError, AuthenticationError


def build_headers(api_key: str) -> dict[str, str]:
    """Build request headers with API key authentication."""
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }


def handle_response(response: httpx.Response) -> dict[str, Any]:
    """Process HTTP response, raising appropriate errors for failures."""
    if response.status_code in (401, 403):
        raise AuthenticationError("Invalid or missing API key")

    if response.status_code >= 400:
        raise APIError(
            message=response.text or "Yutori API call failed",
            status_code=response.status_code,
            response=response,
        )

    if response.content:
        return response.json()
    return {}


def build_query_params(**kwargs: Any) -> dict[str, Any]:
    """Build query parameters, filtering out None values."""
    return {k: v for k, v in kwargs.items() if v is not None}


def build_payload(**fields: Any) -> dict[str, Any]:
    """Build a JSON request payload, filtering out None values.

    Required fields (always non-None) and optional fields can be passed
    together — any field whose value is ``None`` is omitted.
    """
    return {k: v for k, v in fields.items() if v is not None}


def resolve_api_key_or_raise(api_key: str | None) -> str:
    """Resolve an API key via the standard precedence chain, or raise.

    Uses ``auth.credentials.resolve_api_key`` (parameter > env > config)
    and raises ``AuthenticationError`` with a client-friendly message when
    no real key is found. Imported lazily to keep the auth package off the
    import path for code that does not construct a client.
    """
    from .auth.credentials import resolve_api_key

    resolved = resolve_api_key(api_key)
    if not resolved:
        raise AuthenticationError(
            "No API key provided. Run 'yutori auth login', set YUTORI_API_KEY, or pass api_key."
        )
    return resolved


_SCOUT_STATUS_ENDPOINTS = {
    "paused": "pause",
    "active": "resume",
    "done": "done",
}


def resolve_scout_status_endpoint(status: str) -> str:
    """Return the scout transition endpoint name for a target status.

    Maps the externally-facing status vocabulary ("paused", "active", "done")
    onto the API's dedicated transition endpoints ("pause", "resume", "done").

    Raises:
        ValueError: If ``status`` is not one of the three supported values.
    """
    if status not in _SCOUT_STATUS_ENDPOINTS:
        raise ValueError(f"Invalid status: {status}. Must be 'active', 'paused', or 'done'.")
    return _SCOUT_STATUS_ENDPOINTS[status]
