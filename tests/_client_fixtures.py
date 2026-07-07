"""Shared response fixtures for sync/async client tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

    from openai.types.chat import ChatCompletion

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


def make_json_response(data: Any, *, status_code: int = 200) -> MagicMock:
    """Build a mocked :class:`httpx.Response` whose ``.content`` and ``.json()`` both reflect `data`.

    ``data`` is usually a dict, but any JSON-serializable value works — some
    callers use this to simulate a 2xx response with a non-dict body (e.g. a
    backend bug returning a bare list or string).
    """
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.content = json.dumps(data).encode()
    mock_response.json.return_value = data
    return mock_response


def make_status_response(
    status_code: int, text: str = "", *, content: bytes = b"", headers: dict | None = None
) -> MagicMock:
    """Build a mocked :class:`httpx.Response` with ``status_code``/``text`` set.

    Used for error-path tests (401/403/400/500, ...) that only need
    ``handle_response`` to see a non-2xx status and body text, not a JSON payload.
    ``content``/``headers`` are only needed by callers exercising the redirect
    (3xx) or 2xx-non-JSON-body paths of ``handle_response``.
    """
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.text = text
    mock_response.content = content
    mock_response.headers = headers if headers is not None else {}
    return mock_response


def make_mock_usage_response(period: str = "24h") -> MagicMock:
    """Build a mocked 200 OK :class:`httpx.Response` for ``GET /usage``."""
    data = {**USAGE_RESPONSE, "activity": {**USAGE_RESPONSE["activity"], "period": period}}
    return make_json_response(data)


def make_mock_chat_completion(
    *, content: str = "click", model: str = "n1-latest", completion_id: str = "chatcmpl-123"
) -> ChatCompletion:
    """Build a minimal :class:`openai.types.chat.ChatCompletion` for mocking chat.completions.create.

    Every Navigator chat-completions test only cares about ``choices[0].message.content``
    (and sometimes ``model``); the rest of the ChatCompletion fields are fixed
    boilerplate required by the pydantic model. Centralizing them here keeps the
    sync/async client test suites from re-declaring the same
    ChatCompletion/Choice/ChatCompletionMessage construction in every test.
    """
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice

    return ChatCompletion(
        id=completion_id,
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
            )
        ],
        created=1234567890,
        model=model,
        object="chat.completion",
    )


@contextmanager
def mocked_sync_openai_client(mock_completion: ChatCompletion) -> Iterator[MagicMock]:
    """Patch ``yutori._sync.chat.OpenAI`` so ``chat.completions.create()`` returns `mock_completion`.

    Yields the mocked OpenAI client instance so callers can assert on
    ``mock_openai_client.chat.completions.create.call_args``.
    """
    with patch("yutori._sync.chat.OpenAI") as MockOpenAI:
        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create.return_value = mock_completion
        MockOpenAI.return_value = mock_openai_client
        yield mock_openai_client


@contextmanager
def mocked_async_openai_client(mock_completion: ChatCompletion) -> Iterator[MagicMock]:
    """Patch ``yutori._async.chat.AsyncOpenAI`` so ``chat.completions.create()`` returns `mock_completion`.

    Yields the mocked AsyncOpenAI client instance so callers can assert on
    ``mock_openai_client.chat.completions.create.call_args``.
    """
    with patch("yutori._async.chat.AsyncOpenAI") as MockAsyncOpenAI:
        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_openai_client.close = AsyncMock()
        MockAsyncOpenAI.return_value = mock_openai_client
        yield mock_openai_client
