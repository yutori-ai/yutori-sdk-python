"""Shared HTTP request utilities for sync and async clients."""

from __future__ import annotations

from typing import Any

import httpx

from ._schema import resolve_output_schema
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


def build_payload_with_schema(
    *,
    output_schema: object | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Build a JSON payload, resolving ``output_schema`` and filtering ``None`` values.

    Centralizes the ``output_schema=resolve_output_schema(...)`` step used by
    every scout / research / browsing payload builder so namespace methods do
    not need to import :func:`resolve_output_schema` directly. Any field whose
    value is ``None`` after resolution is omitted from the returned dict.
    """
    if output_schema is not None:
        fields["output_schema"] = resolve_output_schema(output_schema)
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


def prepare_scout_update(
    scout_id: str, status: str | None, payload: dict[str, Any]
) -> tuple[str, str, dict[str, Any] | None]:
    """Resolve ``(method, path, json)`` for a ``scouts.update()`` call.

    Centralizes the mutual-exclusion rule between ``status`` and field
    updates so the sync and async namespaces can share a single preflight.

    Raises:
        ValueError: If ``status`` coexists with other fields, or if neither
            ``status`` nor any payload field was provided.
    """
    if status is not None and payload:
        raise ValueError(
            "Cannot update status and other fields simultaneously. "
            "The API requires separate calls: one for status change, another for field updates."
        )
    if status is not None:
        endpoint = resolve_scout_status_endpoint(status)
        return "post", f"/scouting/tasks/{scout_id}/{endpoint}", None
    if not payload:
        raise ValueError("At least one field must be provided for update.")
    return "patch", f"/scouting/tasks/{scout_id}", payload


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

    def _request_kwargs(self, params: Any, json: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"headers": self._headers}
        if params is not None:
            kwargs["params"] = params
        if json is not None:
            kwargs["json"] = json
        return kwargs


class _SyncBaseNamespace(_BaseNamespace):
    """Sync namespace base with a shared request helper.

    Centralizes the ``self._client.<method>(url, headers=..., ...)`` +
    ``handle_response(response)`` boilerplate so concrete namespaces read
    as one-liners.
    """

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
    ) -> dict[str, Any]:
        http_method = getattr(self._client, method)
        response = http_method(
            f"{self._base_url}{path}",
            **self._request_kwargs(params, json),
        )
        return handle_response(response)


class _AsyncBaseNamespace(_BaseNamespace):
    """Async counterpart of :class:`_SyncBaseNamespace`."""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
    ) -> dict[str, Any]:
        http_method = getattr(self._client, method)
        response = await http_method(
            f"{self._base_url}{path}",
            **self._request_kwargs(params, json),
        )
        return handle_response(response)
