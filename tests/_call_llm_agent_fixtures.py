"""Shared ``BrowserAgentMixin`` agent-builder for ``_call_llm``/``_call_llm_with_tools`` tests.

``test_call_llm_mixin.py`` and ``test_call_llm_with_tools_mixin.py`` each defined a
``_make_agent()`` that was byte-for-byte identical except for one line: the
``_call_llm_with_tools`` tests pre-populate ``agent._messages`` (read internally via
``self._messages``), while the ``_call_llm`` tests pass a local ``messages`` list explicitly
and don't need ``agent._messages`` set. Centralizing the shared setup here mirrors the
``tests/_client_fixtures.py`` precedent for the sync/async client test suites.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from examples._common import BrowserAgentMixin
from yutori.navigator import NAVIGATOR_N1_MODEL


def make_call_llm_agent() -> BrowserAgentMixin:
    """Build a ``BrowserAgentMixin`` with a mocked chat-completions client.

    ``model``/``temperature`` are set to fixed values, ``_step_count``/``_step_payloads``
    are initialized, and ``_client.chat.completions.create`` is mocked to return a response
    whose ``model_dump()`` is ``{"role": "assistant", "content": "done"}``.
    """
    agent = BrowserAgentMixin()
    agent.model = NAVIGATOR_N1_MODEL
    agent.temperature = 0.3
    agent._step_count = 3
    agent._step_payloads = []

    response = MagicMock()
    response.model_dump.return_value = {"role": "assistant", "content": "done"}
    agent._client = MagicMock()
    agent._client.chat.completions.create = AsyncMock(return_value=response)
    return agent
