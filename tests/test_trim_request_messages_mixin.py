"""Characterization tests for ``examples._common.BrowserAgentMixin._trim_request_messages``.

Pins the exact trim-and-log behavior of this helper before/after extracting it out of
``navigator_n1_5.py`` (and, before its retirement, ``navigator_n1.py``), which carried a
byte-for-byte identical "call ``update_trimmed_history``, then log the removed-screenshot
count" preamble inside their own ``_call_llm_with_retries``, differing only in the
``extra_fields`` passed to the ``_call_llm`` call that followed.
"""

from __future__ import annotations

from ._client_fixtures import _image_message
from .conftest import require_examples_extra

require_examples_extra()
from loguru import logger  # noqa: E402

from examples._common import BrowserAgentMixin  # noqa: E402


def _make_agent(
    *, messages: list, request_messages: list | None, max_request_bytes: int, keep_recent: int
) -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent._messages = messages
    agent._request_messages = request_messages
    agent.max_request_bytes = max_request_bytes
    agent.keep_recent_screenshots = keep_recent
    return agent


def test_trim_request_messages_returns_full_history_copy_when_under_budget() -> None:
    messages = [_image_message("user", text="one")]
    agent = _make_agent(messages=messages, request_messages=None, max_request_bytes=1_000_000, keep_recent=1)

    result = agent._trim_request_messages()

    assert result == messages
    assert result is not messages
    assert agent._request_messages is result


def test_trim_request_messages_trims_and_logs_when_over_budget(capsys) -> None:
    large_url = "data:image/png;base64," + ("A" * 5000)
    messages = [
        _image_message("user", url=large_url, text="one"),
        _image_message("tool", url=large_url, text="two"),
        _image_message("tool", url=large_url, text="three"),
    ]
    agent = _make_agent(messages=messages, request_messages=None, max_request_bytes=12_000, keep_recent=1)

    sink_id = logger.add(lambda message: print(message, end=""))
    try:
        result = agent._trim_request_messages()
    finally:
        logger.remove(sink_id)

    assert messages[0]["content"][1]["image_url"]["url"] == large_url
    assert result is agent._request_messages
    assert "Trimmed" in capsys.readouterr().out


def test_trim_request_messages_reuses_existing_request_copy() -> None:
    messages = [_image_message("user", text="one")]
    existing_request_messages = [dict(messages[0])]
    agent = _make_agent(
        messages=messages,
        request_messages=existing_request_messages,
        max_request_bytes=1_000_000,
        keep_recent=1,
    )

    result = agent._trim_request_messages()

    assert result is existing_request_messages
