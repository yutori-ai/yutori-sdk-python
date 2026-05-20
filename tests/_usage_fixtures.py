"""Shared usage-endpoint response fixtures for sync/async client tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

# Mirrors the current server dual-emit: both navigator_* and n1_* keys
# are present with equal values. See yutori.codex PR #8174.
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


def make_mock_usage_response(period: str = "24h") -> MagicMock:
    """Build a mocked 200 OK :class:`httpx.Response` for ``GET /usage``."""
    data = {**USAGE_RESPONSE, "activity": {**USAGE_RESPONSE["activity"], "period": period}}
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = json.dumps(data).encode()
    mock_response.json.return_value = data
    return mock_response
