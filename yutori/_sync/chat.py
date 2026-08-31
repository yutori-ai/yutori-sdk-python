"""Chat namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from .._http import apply_chat_extra_body
from ..config import DEFAULT_MAX_RETRIES
from ..navigator.models import NAVIGATOR_N1_5_MODEL


class ChatCompletions:
    """OpenAI-compatible chat completions for the Navigator API."""

    def __init__(self, openai_client: OpenAI) -> None:
        self._client = openai_client

    def create(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str = NAVIGATOR_N1_5_MODEL,
        tool_set: str | None = None,
        disable_tools: list[str] | None = None,
        json_schema: dict | None = None,
        prev_request_id: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a chat completion using the Navigator API.

        Args:
            messages: List of messages following OpenAI Chat format.
            model: Model to use (default: ``"n1.5-latest"`` -- Navigator n1.5).
            tool_set: Navigator n1.5 browser tool set to use, e.g.
                ``"browser_tools_core-20260403"`` or
                ``"browser_tools_expanded-20260403"``.
            disable_tools: List of Navigator n1.5 browser tool names to remove
                from the selected tool set.
            json_schema: JSON Schema for structured output.
                When provided, the model returns a ``parsed_json`` field
                on the response.
            prev_request_id: The ``request_id`` returned by the previous call
                in this conversation; echo it to link the calls into one
                conversation for usage reporting.
            **kwargs: Additional parameters (e.g., temperature).

        Returns:
            ChatCompletion object.
        """
        apply_chat_extra_body(
            kwargs,
            tool_set=tool_set,
            disable_tools=disable_tools,
            json_schema=json_schema,
            prev_request_id=prev_request_id,
        )

        return self._client.chat.completions.create(model=model, messages=messages, **kwargs)


class ChatNamespace:
    """Namespace for Navigator API operations (pixels-to-actions LLM).

    Requests go through the bundled OpenAI client, which retries failures
    (connection errors, timeouts, 429/5xx) with exponential backoff, honoring
    a ``Retry-After`` header when the server sends one. The depth is
    ``max_retries`` (default :data:`~yutori.config.DEFAULT_MAX_RETRIES`), set
    on the client. The other SDK namespaces never retry.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._openai_client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.completions = ChatCompletions(self._openai_client)

    def close(self) -> None:
        self._openai_client.close()
