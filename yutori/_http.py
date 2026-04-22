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


def apply_chat_extra_body(kwargs: dict[str, Any], **fields: Any) -> None:
    """Merge non-None ``fields`` into ``kwargs["extra_body"]`` in place.

    Pops any user-provided ``extra_body`` from ``kwargs``, overlays the
    non-None ``fields`` on top of it, and writes the result back under
    ``extra_body`` — but only if the merged dict is non-empty. Used by
    ChatCompletions.create to compose the ``extra_body`` kwarg forwarded
    to the OpenAI client without letting sync and async implementations
    drift out of sync.
    """
    extra_body = kwargs.pop("extra_body", None) or {}
    for key, value in fields.items():
        if value is not None:
            extra_body[key] = value
    if extra_body:
        kwargs["extra_body"] = extra_body


class _BaseNamespace:
    """Shared base for SDK namespace classes (sync and async).

    Stores the HTTP client, base URL, and a precomputed auth header dict
    so namespace methods can reference ``self._headers`` directly instead
    of rebuilding headers on every request.
    """

    def __init__(self, client: Any, base_url: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._headers = build_headers(api_key)
