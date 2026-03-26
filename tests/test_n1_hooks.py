from __future__ import annotations

import inspect

import pytest

from yutori.n1 import RunHooksBase


class RecordingHooks(RunHooksBase):
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def on_agent_end(self, *, output=None):  # type: ignore[override]
        self.events.append(("agent_end", output))
        await super().on_agent_end(output=output)


@pytest.mark.asyncio
async def test_base_hooks_are_awaitable_and_no_op() -> None:
    hooks = RunHooksBase()
    assert inspect.iscoroutinefunction(hooks.on_agent_start)
    assert inspect.iscoroutinefunction(hooks.on_llm_start)
    assert inspect.iscoroutinefunction(hooks.on_llm_end)
    assert inspect.iscoroutinefunction(hooks.on_tool_start)
    assert inspect.iscoroutinefunction(hooks.on_tool_end)
    assert inspect.iscoroutinefunction(hooks.on_agent_end)

    assert await hooks.on_agent_start(messages=[]) is None
    assert await hooks.on_llm_start(messages=[], tools=None) is None
    assert await hooks.on_llm_end(response={"message": "ok"}) is None
    assert await hooks.on_tool_start(name="extract_content", arguments={}) is None
    assert await hooks.on_tool_end(
        name="extract_content",
        arguments={},
        output="hello",
        trace="extract_content()",
    ) is None
    assert await hooks.on_agent_end(output=None) is None


@pytest.mark.asyncio
async def test_hooks_subclass_cleanly() -> None:
    hooks = RecordingHooks()
    await hooks.on_agent_end(output={"status": "done"})

    assert hooks.events == [("agent_end", {"status": "done"})]
