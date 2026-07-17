"""Characterization tests for ``examples._common.BrowserAgentMixin.__init__``.

Pins the exact behavior of this constructor before/after extracting it out of
``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py``, which previously each defined a
byte-for-byte identical ``Agent.__init__`` (same nine-kwarg signature, calling
``_init_common_agent_config`` with all nine values) -- only their trailing custom-tool setup
line differed. Both now call ``super().__init__(**kwargs)`` and add just that one line.
``navigator_n1.py`` interleaves two more payload-trim kwargs into this same signature and keeps
its own ``__init__``; ``navigator_n1_5.py`` interleaves several more model-specific kwargs and
also keeps its own.
"""

from __future__ import annotations

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402
from yutori.config import DEFAULT_BASE_URL  # noqa: E402
from yutori.navigator import NAVIGATOR_N1_MODEL  # noqa: E402


def test_init_assigns_default_kwargs() -> None:
    agent = BrowserAgentMixin()

    assert agent.base_url == DEFAULT_BASE_URL
    assert agent.model == NAVIGATOR_N1_MODEL
    assert agent.temperature == 0.3
    assert agent.max_steps == 100
    assert agent.viewport_width == 1280
    assert agent.viewport_height == 800
    assert agent.headless is False
    assert agent.replay_dir is None
    assert agent.replay_id is None


def test_init_assigns_overridden_kwargs() -> None:
    agent = BrowserAgentMixin(
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


def test_init_also_initializes_agent_state() -> None:
    """Matches ``_init_common_agent_config``'s behavior, since ``__init__`` delegates to it."""
    agent = BrowserAgentMixin()

    assert agent._client is None
    assert agent._browser is None
    assert agent._page is None
    assert agent._replay is None
    assert agent._messages == []
    assert agent._step_payloads == []
    assert agent._step_count == 0


def test_subclass_can_extend_init_with_super_and_kwargs() -> None:
    """Mirrors the ``navigator_n1_custom_tools.py``/``navigator_n1_memo.py`` pattern: a
    subclass that adds no constructor kwargs of its own now forwards everything to
    ``super().__init__(**kwargs)`` and only adds its own extra setup line."""

    class Agent(BrowserAgentMixin):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.custom_tool = "configured"

    agent = Agent(model="custom-model", headless=True)

    assert agent.model == "custom-model"
    assert agent.headless is True
    assert agent.custom_tool == "configured"
    assert agent._messages == []
