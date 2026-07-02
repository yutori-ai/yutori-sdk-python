"""Shared usage-endpoint response fixtures for sync/async client tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

# Mirrors the current server dual-emit: both navigator_* and n1_* keys
# are present with equal values (n1_* is the deprecated alias).
_NAVIGATOR_LIMITS = {
    "requests_today": 50,
    "daily_limit": 50000,
    "remaining_requests": 49950,
    "reset_at": "2026-03-04T00:00:00+00:00",
    "per_second_limit": 20,
}

USAGE_RESPONSE = {
    "num_active_scouts": 2,
    "active_scout_ids": ["id-1", "id-2"],
    "rate_limits": {
        "requests_today": 100,
        "daily_limit": 10000,
        "remaining_requests": 9900,
        "reset_at": "2026-03-04T00:00:00+00:00",
        "status": "available",
    },
    "navigator_rate_limits": _NAVIGATOR_LIMITS,
    "n1_rate_limits": _NAVIGATOR_LIMITS,
    "activity": {
        "period": "24h",
        "scout_runs": 10,
        "browsing_tasks": 3,
        "research_tasks": 2,
        "navigator_calls": 50,
        "n1_calls": 50,
    },
}


def make_json_response(data: dict, *, status_code: int = 200) -> MagicMock:
    """Build a mocked :class:`httpx.Response` whose ``.content`` and ``.json()`` both reflect `data`."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.content = json.dumps(data).encode()
    mock_response.json.return_value = data
    return mock_response


def make_status_response(status_code: int, text: str = "") -> MagicMock:
    """Build a mocked :class:`httpx.Response` with only ``status_code``/``text`` set.

    Used for error-path tests (401/403/400/500, ...) that only need
    ``handle_response`` to see a non-2xx status and body text, not a JSON payload.
    """
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.text = text
    return mock_response


def make_empty_response(status_code: int = 200) -> MagicMock:
    """Build a mocked :class:`httpx.Response` with empty ``.content`` (e.g. a 200 DELETE with no body)."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.content = b""
    return mock_response


def make_mock_usage_response(period: str = "24h") -> MagicMock:
    """Build a mocked 200 OK :class:`httpx.Response` for ``GET /usage``."""
    data = {**USAGE_RESPONSE, "activity": {**USAGE_RESPONSE["activity"], "period": period}}
    return make_json_response(data)
