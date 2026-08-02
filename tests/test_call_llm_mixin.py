"""Characterization tests for ``examples._common.BrowserAgentMixin._call_llm``.

Pins the exact request-building / replay-step-recording behavior of this helper, extracted
out of ``_call_llm_with_tools`` (fixed ``tools`` list) and the trimmed-history
``_call_llm_with_retries`` overrides in ``navigator_n1.py`` (no extra fields) and
``navigator_n1_5.py`` (``tool_set``/``disable_tools``/``json_schema``), which each previously
built this same request-payload-dict / timeout-guarded-create / sanitized-step-payload body
independently.
"""

from __future__ import annotations

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402
from yutori.navigator import NAVIGATOR_N1_MODEL  # noqa: E402

from ._call_llm_agent_fixtures import make_call_llm_agent  # noqa: E402


def _make_agent() -> BrowserAgentMixin:
    return make_call_llm_agent()


async def test_call_llm_without_extra_fields_passes_only_base_fields() -> None:
    agent = _make_agent()
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]

    response = await agent._call_llm(messages)

    agent._client.chat.completions.create.assert_awaited_once_with(
        model=NAVIGATOR_N1_MODEL,
        messages=messages,
        temperature=0.3,
    )
    assert response is agent._client.chat.completions.create.return_value


async def test_call_llm_merges_extra_fields_into_the_call() -> None:
    agent = _make_agent()
    messages = [{"role": "user", "content": []}]

    await agent._call_llm(messages, extra_fields={"tool_set": "n1_5", "disable_tools": None})

    agent._client.chat.completions.create.assert_awaited_once_with(
        model=NAVIGATOR_N1_MODEL,
        messages=messages,
        temperature=0.3,
        tool_set="n1_5",
        disable_tools=None,
    )


async def test_call_llm_records_sanitized_step_payload_matching_the_actual_call() -> None:
    agent = _make_agent()
    messages = [{"role": "user", "content": []}]

    await agent._call_llm(messages, extra_fields={"tools": [{"type": "function"}]})

    assert len(agent._step_payloads) == 1
    payload = agent._step_payloads[0]
    assert payload["step_num"] == 3
    assert payload["request"] == {
        "model": NAVIGATOR_N1_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "tools": [{"type": "function"}],
    }
    assert payload["response"] == {"role": "assistant", "content": "done"}


async def test_call_llm_appends_without_clearing_prior_payloads() -> None:
    agent = _make_agent()
    agent._step_payloads = [{"step_num": 1}]

    await agent._call_llm([{"role": "user", "content": []}])

    assert len(agent._step_payloads) == 2
    assert agent._step_payloads[0] == {"step_num": 1}
