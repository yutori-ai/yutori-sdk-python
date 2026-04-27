"""Agents-inspired lifecycle hooks for chat-completions-based Navigator loops."""

from __future__ import annotations

from typing import Any

from openai.types.chat import ChatCompletionMessageParam


class RunHooksBase:
    """Agents-inspired lifecycle hooks for chat-completions-based Navigator loops.

    This is intentionally not a drop-in replacement for the OpenAI Agents SDK
    RunHooksBase. It mirrors the lifecycle phases, not the exact signatures.
    """

    async def on_agent_start(self, *, messages: list[ChatCompletionMessageParam]) -> None:
        pass

    async def on_llm_start(
        self,
        *,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        pass

    async def on_llm_end(self, *, response: Any) -> None:
        pass

    async def on_tool_start(self, *, name: str, arguments: dict[str, Any]) -> None:
        pass

    async def on_tool_end(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        output: str | None,
        trace: str,
    ) -> None:
        pass

    async def on_agent_end(self, *, output: Any | None = None) -> None:
        pass
