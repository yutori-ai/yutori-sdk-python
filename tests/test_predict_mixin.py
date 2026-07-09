"""Characterization tests for ``examples._common.BrowserAgentMixin._predict``.

Pins the exact message-building behavior of ``_predict`` before/after
extracting it out of ``navigator_n1.py``, ``navigator_n1_custom_tools.py``,
and ``navigator_n1_memo.py`` (which previously each carried a byte-for-byte
identical copy of this method). ``navigator_n1_5.py`` defines its own,
differently-shaped ``_predict`` and is out of scope here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# examples/_common.py pulls in the optional "examples" extra (loguru, openai, tenacity)
# which isn't installed by the `.[dev]`-only CI test job; skip cleanly rather than
# erroring on collection when it's unavailable.
pytest.importorskip("loguru")
from examples._common import BrowserAgentMixin  # noqa: E402


def _make_agent(*, content: list, message_index: int = 0) -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent._page = MagicMock(url="https://example.com/current")
    agent._messages = [{"role": "user", "content": content}]
    agent._message_index = message_index
    agent._take_screenshot = AsyncMock(return_value="data:image/png;base64,AAAA")

    response = MagicMock()
    response.choices = [MagicMock(message="assistant-message")]
    agent._call_llm_with_retries = AsyncMock(return_value=response)
    return agent


async def test_predict_inserts_current_url_when_content_is_empty() -> None:
    agent = _make_agent(content=[])

    message = await agent._predict()

    assert message == "assistant-message"
    content = agent._messages[-1]["content"]
    assert content[0] == {"type": "text", "text": "Current URL: https://example.com/current"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"},
    }
    agent._call_llm_with_retries.assert_awaited_once()


async def test_predict_skips_current_url_when_content_is_nonempty() -> None:
    agent = _make_agent(content=[{"type": "text", "text": "existing"}])

    await agent._predict()

    content = agent._messages[-1]["content"]
    # No "Current URL: ..." entry is inserted -- only the screenshot is appended.
    assert content == [
        {"type": "text", "text": "existing"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}},
    ]


async def test_predict_logs_only_messages_from_message_index_onward() -> None:
    agent = _make_agent(content=[], message_index=0)
    agent._messages = [
        {"role": "user", "content": [{"type": "text", "text": "already logged"}]},
        {"role": "user", "content": []},
    ]
    agent._message_index = 1
    logged: list[dict] = []
    agent._format_message_for_log = MagicMock(side_effect=lambda m: logged.append(m) or m)

    await agent._predict()

    # Only the message at/after _message_index is formatted for logging.
    assert logged == [agent._messages[1]]
