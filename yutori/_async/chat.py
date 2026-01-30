"""Chat namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import Any, Iterable

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


class AsyncChatCompletions:
    """OpenAI-compatible chat completions for n1 API (async)."""

    def __init__(self, openai_client: AsyncOpenAI) -> None:
        self._client = openai_client

    async def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = "n1-latest",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a chat completion using the n1 API.

        Args:
            messages: List of messages following OpenAI Chat format.
            model: Model to use (default: "n1-latest").
            **kwargs: Additional parameters (e.g., temperature).

        Returns:
            ChatCompletion object.
        """
        return await self._client.chat.completions.create(model=model, messages=messages, **kwargs)


class AsyncChatNamespace:
    """Async namespace for n1 API operations (pixels-to-actions LLM)."""

    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        self._openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.completions = AsyncChatCompletions(self._openai_client)

    async def close(self) -> None:
        await self._openai_client.close()
