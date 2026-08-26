"""Tests for the custom-tool hooks ``navigator_n1_5.py`` exposes to its subclasses.

``navigator_n1_5.Agent`` sends ``self.custom_tools`` alongside the built-in tool set and
consults ``_dispatch_custom_tool`` before running any built-in browser action. The two
custom-tool scripts subclass it and fill both in; these tests pin that contract and the
subclass wiring (registered schema names matching the dispatch branches, ``replay_prefix``,
and ``Config`` fields lining up with ``Agent`` kwargs).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import require_examples_extra

require_examples_extra()

# The example scripts import each other as top-level modules (``from _common import ...``,
# ``from navigator_n1_5 import Agent``), so put examples/ on sys.path the way running one of
# the scripts would. Note this yields a *separate* ``_common`` module object from the
# ``examples._common`` the other test modules import.
EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import navigator_n1_5  # noqa: E402
import navigator_n1_5_custom_tools  # noqa: E402
import navigator_n1_5_memo  # noqa: E402


def _make_agent(agent_cls: type = navigator_n1_5.Agent, **kwargs):
    """Build an example ``Agent`` with the page and page-ready wait stubbed out."""
    agent = agent_cls(**kwargs)
    agent._page = MagicMock(url="https://example.com/x")
    agent._wait_for_page_ready = AsyncMock()
    return agent


def _tool_call(name: str, arguments: str) -> MagicMock:
    tool_call = MagicMock()
    tool_call.function.name = name
    tool_call.function.arguments = arguments
    return tool_call


def _async_cm(yielded: object) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=yielded)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---- custom_tools -> the request's ``tools`` field ---------------------------------------


async def test_call_llm_with_retries_omits_tools_when_no_custom_tools() -> None:
    agent = _make_agent()
    agent._trim_request_messages = MagicMock(return_value=["trimmed"])
    agent._call_llm = AsyncMock(return_value="response")

    result = await agent._call_llm_with_retries()

    assert agent.custom_tools == []
    assert result == "response"
    agent._call_llm.assert_awaited_once_with(
        ["trimmed"],
        extra_fields={"tool_set": agent.tool_set, "disable_tools": None, "json_schema": None},
    )


async def test_call_llm_with_retries_sends_custom_tools_when_set() -> None:
    agent = _make_agent()
    agent.custom_tools = [{"type": "function", "function": {"name": "my_tool"}}]
    agent._trim_request_messages = MagicMock(return_value=[])
    agent._call_llm = AsyncMock()

    await agent._call_llm_with_retries()

    extra_fields = agent._call_llm.await_args.kwargs["extra_fields"]
    assert extra_fields["tools"] is agent.custom_tools
    assert extra_fields["tool_set"] == agent.tool_set


# ---- _execute dispatch ordering ------------------------------------------------------------


async def test_execute_short_circuits_on_custom_tool_result() -> None:
    agent = _make_agent()
    agent._dispatch_custom_tool = AsyncMock(return_value="custom result")
    agent._resolve_coordinates = AsyncMock()

    result = await agent._execute(_tool_call("left_click", '{"coordinates": [1, 2]}'))

    assert result == "custom result"
    agent._dispatch_custom_tool.assert_awaited_once_with("left_click", {"coordinates": [1, 2]})
    agent._resolve_coordinates.assert_not_awaited()


async def test_execute_treats_an_empty_string_result_as_handled() -> None:
    agent = _make_agent()
    agent._dispatch_custom_tool = AsyncMock(return_value="")

    result = await agent._execute(_tool_call("some_custom_tool", "{}"))

    assert result == ""


async def test_execute_falls_through_to_builtin_action_when_dispatch_declines() -> None:
    """The base ``_dispatch_custom_tool`` declines, so a built-in action still runs."""
    agent = _make_agent()
    agent._page.keyboard.type = AsyncMock()

    with patch("navigator_n1_5.asyncio.sleep", AsyncMock()):
        result = await agent._execute(_tool_call("type", '{"text": "hi"}'))

    assert result == "Typed 2 characters"
    agent._page.keyboard.type.assert_awaited_once_with("hi")
    agent._wait_for_page_ready.assert_awaited_once()


async def test_execute_falls_through_to_unknown_action_when_dispatch_declines() -> None:
    agent = _make_agent()

    result = await agent._execute(_tool_call("not_a_real_action", "{}"))

    assert result == "[ERROR] Unknown action: not_a_real_action"


async def test_execute_reports_a_custom_tool_exception_as_an_error_string() -> None:
    agent = _make_agent()
    agent._dispatch_custom_tool = AsyncMock(side_effect=ValueError("Question index 3 not found"))

    result = await agent._execute(_tool_call("add_options", '{"question_index": 3, "options": []}'))

    assert result == "[ERROR] Error executing add_options: Question index 3 not found"


# ---- replay_prefix class attribute -> run() -------------------------------------------------


@pytest.mark.parametrize(
    ("agent_cls", "expected_prefix"),
    [
        (navigator_n1_5.Agent, "navigator_1_5"),
        (navigator_n1_5_custom_tools.Agent, "n1_5_custom"),
        (navigator_n1_5_memo.Agent, "n1_5_memo"),
    ],
)
async def test_run_uses_the_class_replay_prefix(agent_cls: type, expected_prefix: str) -> None:
    agent = _make_agent(agent_cls)
    agent._page.goto = AsyncMock()
    agent._page.wait_for_load_state = AsyncMock()
    agent._start_run = MagicMock()
    agent._init_browser = AsyncMock()
    agent._persist_replay = AsyncMock()
    agent._close_browser = AsyncMock()
    response = MagicMock(content="done", tool_calls=[])
    response.model_dump.return_value = {"role": "assistant", "content": "done"}
    agent._predict = AsyncMock(return_value=response)

    with (
        patch("navigator_n1_5.AsyncYutoriClient", return_value=_async_cm(MagicMock())),
        patch("navigator_n1_5.async_playwright", return_value=_async_cm(MagicMock())),
    ):
        result = await agent.run("do it", "https://example.com")

    assert result == "done"
    assert agent._start_run.call_args.kwargs["replay_prefix"] == expected_prefix
    agent._close_browser.assert_awaited_once()


# ---- subclass wiring: registered schemas vs. dispatch branches --------------------------------


def test_custom_tools_agent_registers_its_extract_tool() -> None:
    agent = navigator_n1_5_custom_tools.Agent()

    assert [tool["function"]["name"] for tool in agent.custom_tools] == ["extract_content_and_links"]


async def test_custom_tools_agent_dispatches_extract_tool_and_declines_others() -> None:
    agent = _make_agent(navigator_n1_5_custom_tools.Agent)
    agent._extract_content_and_links_tool = AsyncMock(return_value="links!")

    assert await agent._dispatch_custom_tool("extract_content_and_links", {}) == "links!"
    agent._extract_content_and_links_tool.assert_awaited_once_with(agent._page)
    agent._wait_for_page_ready.assert_awaited_once()
    assert await agent._dispatch_custom_tool("left_click", {}) is None


def test_memo_agent_registers_its_three_tools() -> None:
    agent = navigator_n1_5_memo.Agent()

    assert [tool["function"]["name"] for tool in agent.custom_tools] == [
        "add_question",
        "add_options",
        "list_records",
    ]


async def test_memo_agent_dispatch_round_trips_through_the_memo_file(tmp_path) -> None:
    agent = _make_agent(navigator_n1_5_memo.Agent)
    # The Agent builds a MemoToolSuite writing to a timestamped path under examples/; redirect it.
    agent._memo_tool_suite = navigator_n1_5_memo.MemoToolSuite(file_path=str(tmp_path / "memo.jsonl"))

    added_question = await agent._dispatch_custom_tool("add_question", {"index": 1, "question": "Q1"})
    added_options = await agent._dispatch_custom_tool("add_options", {"question_index": 1, "options": ["a", "b"]})
    listing = await agent._dispatch_custom_tool("list_records", {})

    assert added_question == "Successfully added question 1"
    assert added_options == "Successfully added options to question 1"
    assert '"question": "Q1"' in listing
    assert '"options": ["a", "b"]' in listing
    assert await agent._dispatch_custom_tool("left_click", {}) is None


# ---- Config -> Agent kwargs (run_example_agent maps the fields 1:1) ----------------------------


@pytest.mark.parametrize("module", [navigator_n1_5_custom_tools, navigator_n1_5_memo])
def test_base_config_fields_map_onto_agent_kwargs(module) -> None:
    config_fields = module.Config().model_dump(exclude={"task", "start_url"})

    agent = module.Agent(**config_fields)

    for name, value in config_fields.items():
        assert getattr(agent, name) == value
