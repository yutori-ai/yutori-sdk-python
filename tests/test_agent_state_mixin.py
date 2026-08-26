"""Characterization tests for ``examples._common.BrowserAgentMixin._init_agent_state``/``_start_run``.

Pins the exact behavior of these two helpers, which hold the ``__init__`` browser/replay
bookkeeping and the ``run()`` reset-and-start-replay prologue now shared by
``navigator_n1_5.py`` and the custom-tool scripts that subclass it (the retired
``navigator_n1_*`` scripts each hand-rolled their own copy).
"""

from __future__ import annotations

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402
from yutori.navigator.page_ready import PageReadyChecker  # noqa: E402


def _make_agent(*, replay_dir: str | None = None, replay_id: str | None = None) -> BrowserAgentMixin:
    agent = BrowserAgentMixin()
    agent.replay_dir = replay_dir
    agent.replay_id = replay_id
    return agent


def test_init_agent_state_sets_expected_defaults() -> None:
    agent = _make_agent()

    agent._init_agent_state()

    assert agent._client is None
    assert agent._browser is None
    assert agent._page is None
    assert isinstance(agent._page_ready_checker, PageReadyChecker)
    assert agent._replay is None
    assert agent._messages == []
    assert agent._step_payloads == []
    assert agent._step_count == 0


def test_init_agent_state_page_ready_checker_matches_prior_hardcoded_kwargs() -> None:
    agent = _make_agent()

    agent._init_agent_state()

    checker = agent._page_ready_checker
    assert checker.timeout == 30
    assert checker.initial_wait == 2.0
    assert checker.wait_after_ready == 1.0
    assert checker.replace_native_select_dropdown is True
    assert checker.disable_new_tabs is True
    assert checker.disable_printing is True


def test_init_agent_state_returns_fresh_mutable_containers() -> None:
    """Two agents must not share the same list objects."""
    agent_one = _make_agent()
    agent_two = _make_agent()

    agent_one._init_agent_state()
    agent_two._init_agent_state()

    agent_one._messages.append({"role": "user", "content": []})
    agent_one._step_payloads.append({"step_num": 1})

    assert agent_two._messages == []
    assert agent_two._step_payloads == []


def test_start_run_resets_message_and_step_state_without_replay_dir() -> None:
    agent = _make_agent(replay_dir=None)
    agent._init_agent_state()
    # Simulate leftover state from a prior run.
    agent._messages = [{"role": "user", "content": ["stale"]}]
    agent._step_count = 5
    agent._step_payloads = [{"step_num": 1}]

    agent._start_run("do the thing", "https://example.com", replay_prefix="navigator_1_5")

    assert agent._messages == [{"role": "user", "content": [{"type": "text", "text": "do the thing"}]}]
    assert agent._message_index == 0
    assert agent._step_count == 0
    assert agent._step_payloads == []
    assert agent._replay is None


def test_start_run_creates_replay_recorder_when_replay_dir_set(tmp_path) -> None:
    agent = _make_agent(replay_dir=str(tmp_path), replay_id="fixed-run-id")
    agent._init_agent_state()

    agent._start_run("do the thing", "https://example.com", replay_prefix="navigator_1_5")

    assert agent._replay is not None
    assert agent._replay.run_id == "fixed-run-id"
    assert agent._replay.item_dir == tmp_path / "fixed-run-id"
    assert agent._replay.item_dir.is_dir()


def test_start_run_derives_replay_id_from_prefix_and_task_when_unset(tmp_path) -> None:
    agent = _make_agent(replay_dir=str(tmp_path), replay_id=None)
    agent._init_agent_state()

    agent._start_run("Do The Thing!", "https://example.com", replay_prefix="n1_5_custom")

    assert agent._replay is not None
    assert agent._replay.run_id.startswith("n1_5_custom_")
    assert "do-the-thing" in agent._replay.run_id


def test_start_run_uses_given_task_verbatim_for_message_and_label(tmp_path) -> None:
    """navigator_n1_5.py calls this with an already-reformatted task string; both the
    seeded message and the replay label must reflect exactly what was passed in."""
    agent = _make_agent(replay_dir=str(tmp_path), replay_id=None)
    agent._init_agent_state()

    reformatted_task = "Reformatted: do the thing (tz=UTC, loc=SF)"
    agent._start_run(reformatted_task, "https://example.com", replay_prefix="navigator_1_5")

    assert agent._messages[0]["content"][0]["text"] == reformatted_task
    assert agent._replay is not None
