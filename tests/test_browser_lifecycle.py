"""Characterization tests for ``examples._common.BrowserAgentMixin._run_with_browser_lifecycle``.

Pins the exact behavior of this helper before/after extracting it out of
``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``, each of
which previously carried a byte-for-byte identical ``run()`` body (open the client/browser,
navigate, run the predict/execute loop, clean up), differing only in the ``replay_prefix``
passed to ``_start_run``. ``navigator_n1_5.py`` diverges after page-ready and keeps its own
``run()``, so it is out of scope here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402


def _make_agent() -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent.base_url = "https://example.yutori.com"
    agent._page = MagicMock()
    agent._page.goto = AsyncMock()
    agent._page.wait_for_load_state = AsyncMock()
    agent._start_run = MagicMock()
    agent._init_browser = AsyncMock()
    agent._wait_for_page_ready = AsyncMock()
    agent._run_agent_loop = AsyncMock(return_value="final answer")
    agent._persist_replay = AsyncMock()
    agent._close_browser = AsyncMock()
    return agent


def _mock_async_context_manager(yielded: object) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=yielded)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_run_with_browser_lifecycle_happy_path() -> None:
    agent = _make_agent()
    client_instance = MagicMock()
    playwright_instance = MagicMock()
    client_cm = _mock_async_context_manager(client_instance)
    playwright_cm = _mock_async_context_manager(playwright_instance)

    with (
        patch("examples._common.AsyncYutoriClient", return_value=client_cm) as client_ctor,
        patch("examples._common.async_playwright", return_value=playwright_cm),
    ):
        result = await agent._run_with_browser_lifecycle("do it", "https://example.com", replay_prefix="n1")

    client_ctor.assert_called_once_with(base_url="https://example.yutori.com")
    agent._start_run.assert_called_once_with("do it", "https://example.com", replay_prefix="n1")
    assert agent._client is client_instance
    agent._init_browser.assert_awaited_once_with(playwright_instance)
    agent._page.goto.assert_awaited_once_with("https://example.com")
    agent._page.wait_for_load_state.assert_awaited_once_with("domcontentloaded")
    agent._wait_for_page_ready.assert_awaited_once()
    agent._run_agent_loop.assert_awaited_once()
    agent._persist_replay.assert_awaited_once()
    agent._close_browser.assert_awaited_once()
    assert result == "final answer"


async def test_run_with_browser_lifecycle_swallows_keyboard_interrupt() -> None:
    agent = _make_agent()
    agent._run_agent_loop = AsyncMock(side_effect=KeyboardInterrupt())
    client_cm = _mock_async_context_manager(MagicMock())
    playwright_cm = _mock_async_context_manager(MagicMock())

    with (
        patch("examples._common.AsyncYutoriClient", return_value=client_cm),
        patch("examples._common.async_playwright", return_value=playwright_cm),
    ):
        result = await agent._run_with_browser_lifecycle("do it", "https://example.com", replay_prefix="n1")

    # No final response was ever set before the interrupt.
    assert result == ""
    agent._persist_replay.assert_awaited_once()
    agent._close_browser.assert_awaited_once()


async def test_run_with_browser_lifecycle_cleans_up_on_loop_exception() -> None:
    """Non-KeyboardInterrupt exceptions propagate, but cleanup still runs (via ``finally``)."""
    agent = _make_agent()
    agent._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))
    client_cm = _mock_async_context_manager(MagicMock())
    playwright_cm = _mock_async_context_manager(MagicMock())

    with (
        patch("examples._common.AsyncYutoriClient", return_value=client_cm),
        patch("examples._common.async_playwright", return_value=playwright_cm),
    ):
        try:
            await agent._run_with_browser_lifecycle("do it", "https://example.com", replay_prefix="n1")
            raised = False
        except RuntimeError:
            raised = True

    assert raised
    agent._persist_replay.assert_awaited_once()
    agent._close_browser.assert_awaited_once()
