"""Shared response fixtures for sync/async client tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from yutori.navigator import NAVIGATOR_N1_MODEL

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


def patch_async_http(method: str, response: Any = None, *, side_effect: Any = None) -> Any:
    """Patch ``httpx.AsyncClient.<method>`` with an :class:`AsyncMock`.

    Every async transport test has to pass ``new_callable=AsyncMock`` to
    ``patch.object`` — without it the awaited call yields a ``MagicMock``
    instead of the mocked response. That boilerplate was repeated at every
    call site in the async client suite, and was long enough to push several
    of them onto three wrapped lines. Pass ``response`` for the awaited return
    value, or ``side_effect`` to raise a transport error instead.

    Used exactly like ``patch.object``: as a context manager that yields the
    mock, so callers can still assert on ``call_args``.

    The sync suite deliberately keeps plain ``patch.object(httpx.Client, ...)``
    — it needs no ``new_callable`` argument, so a wrapper there would add
    indirection without removing anything.
    """
    mock_kwargs = {"side_effect": side_effect} if side_effect is not None else {"return_value": response}
    return patch.object(httpx.AsyncClient, method, new_callable=AsyncMock, **mock_kwargs)


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


def _image_message(role: str, *, url: str = "data:image/png;base64,abc", text: str | None = None) -> dict[str, Any]:
    """Build a single chat message with an ``image_url`` content block (plus optional text).

    Shared by the trim-request-messages and navigator-replay test suites, which each need
    minimal image-bearing messages to exercise size-based trimming/sanitization logic.
    """
    content: list[dict] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
    return {"role": role, "content": content}


def make_trimmable_messages() -> list[dict[str, Any]]:
    """Build a fresh two-message list with an oversized ``image_url`` block in each message.

    Used by the payload-trimming tests in the sync/async client suites (``create_trimmed``/
    ``acreate_trimmed`` and the standalone ``trimmed_messages_to_fit`` pattern), which each need
    a messages list big enough that trimming with ``max_bytes=100`` actually removes an image.
    Returns a new list on every call so callers can safely mutate or ``deepcopy`` the result
    without affecting other tests.
    """
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Check the page"},
                {"type": "image_url", "image_url": {"url": "A" * 5000}},
            ],
        },
        {
            "role": "tool",
            "content": [
                {"type": "text", "text": "Tool output"},
                {"type": "image_url", "image_url": {"url": "A" * 5000}},
            ],
        },
    ]


def make_mock_chat_completion(
    *, content: str = "click", model: str = NAVIGATOR_N1_MODEL, completion_id: str = "chatcmpl-123"
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
