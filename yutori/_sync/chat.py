"""Chat namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from typing import Any, Iterable

from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


class ChatCompletions:
    """OpenAI-compatible chat completions for n1 API."""

    def __init__(self, openai_client: OpenAI) -> None:
        self._client = openai_client

    def create(
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
        return self._client.chat.completions.create(model=model, messages=messages, **kwargs)


class ChatNamespace:
    """Namespace for n1 API operations (pixels-to-actions LLM)."""

    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        self._openai_client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.completions = ChatCompletions(self._openai_client)

    def close(self) -> None:
        self._openai_client.close()
