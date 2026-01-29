"""Shared HTTP request utilities for sync and async clients."""

from __future__ import annotations

from typing import Any

import httpx

from .exceptions import APIError, AuthenticationError


def build_headers(api_key: str, auth_type: str = "api_key") -> dict[str, str]:
    """Build request headers with appropriate authentication."""
    headers = {"Content-Type": "application/json"}

    if auth_type == "api_key":
        headers["x-api-key"] = api_key
    elif auth_type == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        raise ValueError(f"Unsupported auth type: {auth_type}")

    return headers


def handle_response(response: httpx.Response) -> dict[str, Any]:
    """Process HTTP response, raising appropriate errors for failures."""
    if response.status_code == 401:
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
