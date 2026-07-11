"""Characterization tests for ``examples._common.BrowserAgentMixin._execute``.

Pins the exact argument-parsing / custom-tool-dispatch / n1-primitive-fallback /
page-ready-wait / catch-all-error behavior before/after extracting it out of
``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``
(which previously each carried a byte-for-byte identical ``_execute`` body, differing
only in which custom tools -- if any -- they checked before falling back to
:func:`examples._common.execute_n1_primitive_action`). ``navigator_n1_5.py`` defines its
own, differently-shaped ``_execute`` and is out of scope here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402


def _make_agent() -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent.viewport_width = 1280
    agent.viewport_height = 800
    agent._page = MagicMock()
    agent._page.wait_for_load_state = AsyncMock()
    agent._wait_for_page_ready = AsyncMock()
    return agent


def _tool_call(name: str, arguments: str) -> MagicMock:
    tool_call = MagicMock()
    tool_call.function.name = name
    tool_call.function.arguments = arguments
    return tool_call


async def test_execute_returns_error_on_invalid_json() -> None:
    agent = _make_agent()

    with patch("examples._common.execute_n1_primitive_action", AsyncMock()) as primitive:
        result = await agent._execute(_tool_call("left_click", "not json"))

    assert result == (False, "[ERROR] Failed to parse arguments: not json")
    primitive.assert_not_awaited()


async def test_execute_falls_back_to_primitive_action_when_dispatch_declines() -> None:
    agent = _make_agent()

    with patch("examples._common.execute_n1_primitive_action", AsyncMock(return_value=True)) as primitive:
        result = await agent._execute(_tool_call("left_click", '{"coordinates": [1, 2]}'))

    assert result == (False, None)
    primitive.assert_awaited_once_with(agent._page, "left_click", {"coordinates": [1, 2]}, 1280, 800)
    agent._page.wait_for_load_state.assert_awaited_once_with("domcontentloaded", timeout=3000)
    agent._wait_for_page_ready.assert_awaited_once()


async def test_execute_returns_error_for_unknown_action() -> None:
    agent = _make_agent()

    with patch("examples._common.execute_n1_primitive_action", AsyncMock(return_value=False)):
        result = await agent._execute(_tool_call("not_a_real_action", "{}"))

    assert result == (False, "[ERROR] Unknown action: not_a_real_action")
    agent._wait_for_page_ready.assert_not_awaited()


async def test_execute_uses_custom_dispatch_result_directly() -> None:
    agent = _make_agent()
    agent._dispatch_custom_tool = AsyncMock(return_value=(True, "custom result"))

    with patch("examples._common.execute_n1_primitive_action", AsyncMock()) as primitive:
        result = await agent._execute(_tool_call("my_custom_tool", '{"foo": "bar"}'))

    assert result == (True, "custom result")
    agent._dispatch_custom_tool.assert_awaited_once_with("my_custom_tool", {"foo": "bar"})
    primitive.assert_not_awaited()


async def test_execute_ignores_load_state_timeout() -> None:
    agent = _make_agent()
    agent._page.wait_for_load_state = AsyncMock(side_effect=TimeoutError("no navigation"))

    with patch("examples._common.execute_n1_primitive_action", AsyncMock(return_value=True)):
        result = await agent._execute(_tool_call("left_click", "{}"))

    assert result == (False, None)
    agent._wait_for_page_ready.assert_awaited_once()


async def test_execute_catches_exceptions_and_returns_error() -> None:
    agent = _make_agent()

    with patch("examples._common.execute_n1_primitive_action", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await agent._execute(_tool_call("left_click", "{}"))

    assert result == (False, "[ERROR] Error executing left_click: boom")


async def test_default_dispatch_custom_tool_always_declines() -> None:
    agent = _make_agent()

    result = await agent._dispatch_custom_tool("anything", {"foo": "bar"})

    assert result is None
