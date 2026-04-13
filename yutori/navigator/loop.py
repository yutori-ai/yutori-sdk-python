"""Higher-level helpers for screenshot-heavy navigator agent loops."""

from __future__ import annotations

import copy
from typing import Any, Iterable, Protocol

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from .models import N1_5_MODEL
from .payload import (
    DEFAULT_KEEP_RECENT_SCREENSHOTS,
    DEFAULT_MAX_REQUEST_BYTES,
    estimate_messages_size_bytes,
    trim_images_to_fit,
    trimmed_messages_to_fit,
)


class SupportsSyncChatCompletionsCreate(Protocol):
    """The subset of the sync chat completions interface needed by loop helpers."""

    def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = "n1.5-latest",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a sync chat completion."""


class SupportsAsyncChatCompletionsCreate(Protocol):
    """The subset of the async chat completions interface needed by loop helpers."""

    async def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = "n1.5-latest",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create an async chat completion."""


def update_trimmed_history(
    messages: list[dict[str, Any]],
    request_messages: list[dict[str, Any]] | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
) -> tuple[list[dict[str, Any]], int, int]:
    """Update a request-history copy without mutating the caller's full history.

    This mirrors the pattern used by long-lived browser loops that want to keep
    a complete replayable `messages` list while trimming a separate request copy
    before sending it to the API.
    """

    if request_messages is None or len(messages) < len(request_messages):
        request_messages = copy.deepcopy(messages)
    elif len(messages) > len(request_messages):
        request_messages.extend(copy.deepcopy(messages[len(request_messages) :]))

    size_bytes = estimate_messages_size_bytes(request_messages)
    removed = 0
    if size_bytes > max_bytes:
        size_bytes, removed = trim_images_to_fit(
            request_messages,
            max_bytes=max_bytes,
            keep_recent=keep_recent,
        )
    return request_messages, size_bytes, removed


def create_trimmed(
    completions: SupportsSyncChatCompletionsCreate,
    messages: list[dict[str, Any]],
    *,
    model: str = N1_5_MODEL,
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
    **kwargs: Any,
) -> ChatCompletion:
    """Create a sync chat completion from a trimmed copy of *messages*."""

    trimmed_messages, _, _ = trimmed_messages_to_fit(
        messages,
        max_bytes=max_bytes,
        keep_recent=keep_recent,
    )
    return completions.create(trimmed_messages, model=model, **kwargs)


async def acreate_trimmed(
    completions: SupportsAsyncChatCompletionsCreate,
    messages: list[dict[str, Any]],
    *,
    model: str = N1_5_MODEL,
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
    **kwargs: Any,
) -> ChatCompletion:
    """Create an async chat completion from a trimmed copy of *messages*."""

    trimmed_messages, _, _ = trimmed_messages_to_fit(
        messages,
        max_bytes=max_bytes,
        keep_recent=keep_recent,
    )
    return await completions.create(trimmed_messages, model=model, **kwargs)
