"""Chat namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from typing import Any, Iterable

from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from .._http import apply_chat_extra_body


class ChatCompletions:
    """OpenAI-compatible chat completions for the Navigator API."""

    def __init__(self, openai_client: OpenAI) -> None:
        self._client = openai_client

    def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = "n1.5-latest",
        tool_set: str | None = None,
        disable_tools: list[str] | None = None,
        json_schema: dict | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a chat completion using the Navigator API.

        Args:
            messages: List of messages following OpenAI Chat format.
            model: Model to use (default: ``"n1.5-latest"`` — Navigator-n1.5).
            tool_set: (Navigator-n1.5 only) Built-in tool set to use, e.g.
                ``"browser_tools_core-20260403"`` or
                ``"browser_tools_expanded-20260403"``.
            disable_tools: (Navigator-n1.5 only) List of tool names to remove
                from the selected tool set.
            json_schema: (Navigator-n1.5 only) JSON Schema for structured output.
                When provided, the model returns a ``parsed_json`` field
                on the response.
            **kwargs: Additional parameters (e.g., temperature).

        Returns:
            ChatCompletion object.
        """
        apply_chat_extra_body(
            kwargs,
            tool_set=tool_set,
            disable_tools=disable_tools,
            json_schema=json_schema,
        )

        return self._client.chat.completions.create(model=model, messages=messages, **kwargs)


class ChatNamespace:
    """Namespace for Navigator API operations (pixels-to-actions LLM)."""

    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        self._openai_client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.completions = ChatCompletions(self._openai_client)

    def close(self) -> None:
        self._openai_client.close()
