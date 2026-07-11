"""Characterization tests for ``examples._common.BrowserAgentMixin._call_llm_with_tools``.

Pins the exact request-building / replay-step-recording behavior of this helper before/after
extracting it out of ``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py``, each of
which previously carried a byte-for-byte identical ``_call_llm_with_retries`` body, differing
only in the fixed ``tools`` list passed to the chat completions call. ``navigator_n1.py`` and
``navigator_n1_5.py`` additionally trim message history and pass model-specific fields, so
they keep their own ``_call_llm_with_retries`` and are out of scope here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402


def _make_agent() -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent.model = "n1-latest"
    agent.temperature = 0.3
    agent._messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    agent._step_count = 3
    agent._step_payloads = []

    response = MagicMock()
    response.model_dump.return_value = {"role": "assistant", "content": "done"}
    agent._client = MagicMock()
    agent._client.chat.completions.create = AsyncMock(return_value=response)
    return agent


async def test_call_llm_with_tools_passes_tools_and_fields_to_create() -> None:
    agent = _make_agent()
    tools = [{"type": "function", "function": {"name": "my_tool"}}]

    response = await agent._call_llm_with_tools(tools)

    agent._client.chat.completions.create.assert_awaited_once_with(
        model="n1-latest",
        messages=agent._messages,
        temperature=0.3,
        tools=tools,
    )
    assert response is agent._client.chat.completions.create.return_value


async def test_call_llm_with_tools_records_sanitized_step_payload() -> None:
    agent = _make_agent()
    tools = [{"type": "function", "function": {"name": "my_tool"}}]

    await agent._call_llm_with_tools(tools)

    assert len(agent._step_payloads) == 1
    payload = agent._step_payloads[0]
    assert payload["step_num"] == 3
    assert payload["request"] == {
        "model": "n1-latest",
        "messages": agent._messages,
        "temperature": 0.3,
        "tools": tools,
    }
    assert payload["response"] == {"role": "assistant", "content": "done"}


async def test_call_llm_with_tools_appends_without_clearing_prior_payloads() -> None:
    agent = _make_agent()
    agent._step_payloads = [{"step_num": 1}]

    await agent._call_llm_with_tools([])

    assert len(agent._step_payloads) == 2
    assert agent._step_payloads[0] == {"step_num": 1}
