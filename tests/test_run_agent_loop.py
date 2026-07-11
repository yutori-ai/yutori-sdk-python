"""Characterization tests for ``examples._common.BrowserAgentMixin._run_agent_loop``.

Pins the exact predict/execute step-loop behavior before/after extracting it out of
``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``, each of
which previously carried a byte-for-byte identical version of this loop inside ``run()``.
``navigator_n1_5.py`` diverges (stop-and-summarize handling, URL-suffixed tool results) and
keeps its own loop, so it is out of scope here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402


def _make_agent(*, max_steps: int = 5) -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent.max_steps = max_steps
    agent._step_count = 0
    agent._messages = []
    agent._message_index = 0
    agent._page = MagicMock(url="https://example.com/current")
    agent._persist_replay = AsyncMock()
    return agent


def _response(*, content: str | None = None, tool_calls: list | None = None) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.tool_calls = tool_calls or []
    response.model_dump.return_value = {"role": "assistant", "content": content}
    return response


async def test_loop_stops_when_no_tool_calls_and_returns_final_content() -> None:
    agent = _make_agent()
    agent._predict = AsyncMock(return_value=_response(content="final answer", tool_calls=[]))

    result = await agent._run_agent_loop()

    assert result == "final answer"
    assert agent._step_count == 1
    assert agent._messages == [{"role": "assistant", "content": "final answer"}]
    agent._persist_replay.assert_awaited()


async def test_loop_appends_tool_results_and_continues_to_next_step() -> None:
    agent = _make_agent()
    tool_call = MagicMock(id="call_1")
    first = _response(content=None, tool_calls=[tool_call])
    second = _response(content="done", tool_calls=[])
    agent._predict = AsyncMock(side_effect=[first, second])
    agent._execute = AsyncMock(return_value=(False, "tool result"))

    result = await agent._run_agent_loop()

    assert result == "done"
    assert agent._step_count == 2
    assert agent._messages[1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": [{"type": "text", "text": "tool result"}],
    }
    agent._execute.assert_awaited_once_with(tool_call)


async def test_loop_appends_empty_content_when_tool_result_is_falsy() -> None:
    agent = _make_agent()
    tool_call = MagicMock(id="call_1")
    first = _response(content=None, tool_calls=[tool_call])
    second = _response(content="done", tool_calls=[])
    agent._predict = AsyncMock(side_effect=[first, second])
    agent._execute = AsyncMock(return_value=(False, None))

    await agent._run_agent_loop()

    assert agent._messages[1]["content"] == []


async def test_loop_exits_immediately_when_execute_signals_should_exit() -> None:
    agent = _make_agent()
    tool_call = MagicMock(id="call_1")
    response = _response(content=None, tool_calls=[tool_call])
    agent._predict = AsyncMock(return_value=response)
    agent._execute = AsyncMock(return_value=(True, "early result"))

    result = await agent._run_agent_loop()

    assert result == "early result"
    assert agent._step_count == 1
    # Only the assistant message was appended; the tool-result branch never runs.
    assert agent._messages == [{"role": "assistant", "content": None}]
    agent._persist_replay.assert_awaited()


async def test_loop_stops_at_max_steps_when_tool_calls_never_end() -> None:
    agent = _make_agent(max_steps=2)
    tool_call = MagicMock(id="call_1")
    response = _response(content=None, tool_calls=[tool_call])
    agent._predict = AsyncMock(return_value=response)
    agent._execute = AsyncMock(return_value=(False, "keep going"))

    result = await agent._run_agent_loop()

    assert agent._step_count == 2
    assert agent._predict.await_count == 2
    # response.content was always None, so the final response stays empty.
    assert result == ""
