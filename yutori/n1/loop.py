"""Higher-level helpers for screenshot-heavy n1 agent loops."""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from .payload import (
    DEFAULT_KEEP_RECENT_SCREENSHOTS,
    DEFAULT_MAX_REQUEST_BYTES,
    trimmed_messages_to_fit,
)


class SupportsSyncChatCompletionsCreate(Protocol):
    """The subset of the sync chat completions interface needed by loop helpers."""

    def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = "n1-latest",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a sync n1 chat completion."""


class SupportsAsyncChatCompletionsCreate(Protocol):
    """The subset of the async chat completions interface needed by loop helpers."""

    async def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = "n1-latest",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create an async n1 chat completion."""


def create_trimmed(
    completions: SupportsSyncChatCompletionsCreate,
    messages: list[dict[str, Any]],
    *,
    model: str = "n1-latest",
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
    **kwargs: Any,
) -> ChatCompletion:
    """Create a sync n1 chat completion from a trimmed copy of *messages*."""

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
    model: str = "n1-latest",
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
    **kwargs: Any,
) -> ChatCompletion:
    """Create an async n1 chat completion from a trimmed copy of *messages*."""

    trimmed_messages, _, _ = trimmed_messages_to_fit(
        messages,
        max_bytes=max_bytes,
        keep_recent=keep_recent,
    )
    return await completions.create(trimmed_messages, model=model, **kwargs)
