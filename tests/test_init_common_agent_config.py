"""Characterization tests for ``examples._common.BrowserAgentMixin._init_common_agent_config``.

Pins the exact constructor-kwarg-assignment behavior before/after extracting it out of
``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``.
``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py`` previously had byte-for-byte
identical ``__init__`` bodies built from exactly these nine kwargs (differing only in their
trailing custom-tool setup line); ``navigator_n1.py`` set the same nine kwargs with its own
payload-trim fields interleaved. ``navigator_n1_5.py`` interleaves several more model-specific
kwargs among these same nine and keeps its own inline assignment, so it is out of scope here.
"""

from __future__ import annotations

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402
from yutori.navigator.page_ready import PageReadyChecker  # noqa: E402


def _make_agent() -> BrowserAgentMixin:
    return BrowserAgentMixin()


def test_init_common_agent_config_assigns_all_kwargs() -> None:
    agent = _make_agent()

    agent._init_common_agent_config(
        base_url="https://api.example.com/v1",
        model="some-model",
        temperature=0.7,
        max_steps=42,
        viewport_width=1024,
        viewport_height=768,
        headless=True,
        replay_dir="/tmp/replays",
        replay_id="fixed-run-id",
    )

    assert agent.base_url == "https://api.example.com/v1"
    assert agent.model == "some-model"
    assert agent.temperature == 0.7
    assert agent.max_steps == 42
    assert agent.viewport_width == 1024
    assert agent.viewport_height == 768
    assert agent.headless is True
    assert agent.replay_dir == "/tmp/replays"
    assert agent.replay_id == "fixed-run-id"


def test_init_common_agent_config_also_initializes_agent_state() -> None:
    agent = _make_agent()

    agent._init_common_agent_config(
        base_url="https://api.example.com/v1",
        model="some-model",
        temperature=0.3,
        max_steps=100,
        viewport_width=1280,
        viewport_height=800,
        headless=False,
        replay_dir=None,
        replay_id=None,
    )

    assert agent._client is None
    assert agent._browser is None
    assert agent._page is None
    assert isinstance(agent._page_ready_checker, PageReadyChecker)
    assert agent._replay is None
    assert agent._messages == []
    assert agent._step_payloads == []
    assert agent._step_count == 0


def test_init_common_agent_config_returns_fresh_mutable_containers() -> None:
    """Two agents must not share the same list objects (matches ``_init_agent_state``)."""
    agent_one = _make_agent()
    agent_two = _make_agent()

    kwargs = dict(
        base_url="https://api.example.com/v1",
        model="some-model",
        temperature=0.3,
        max_steps=100,
        viewport_width=1280,
        viewport_height=800,
        headless=False,
        replay_dir=None,
        replay_id=None,
    )
    agent_one._init_common_agent_config(**kwargs)
    agent_two._init_common_agent_config(**kwargs)

    agent_one._messages.append({"role": "user", "content": []})

    assert agent_two._messages == []
