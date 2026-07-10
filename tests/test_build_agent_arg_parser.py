"""Characterization tests for ``examples._common.build_agent_arg_parser``.

Pins the exact argument set/defaults produced before/after extracting this
parser assembly out of ``navigator_n1.py``, ``navigator_n1_memo.py``, and
``navigator_n1_custom_tools.py`` (which previously each hand-assembled the
same task/model/agent/browser/replay argument groups in ``main()``, with
``navigator_n1.py`` alone also wiring in payload-trim arguments).
"""

from __future__ import annotations

from types import SimpleNamespace

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import build_agent_arg_parser  # noqa: E402


def _default_config(**overrides) -> SimpleNamespace:
    values = {
        "task": "default task",
        "start_url": "https://example.com",
        "base_url": "https://api.example.com/v1",
        "model": "some-model",
        "temperature": 0.3,
        "max_steps": 100,
        "viewport_width": 1280,
        "viewport_height": 800,
        "max_request_bytes": 9_500_000,
        "keep_recent_screenshots": 6,
        "replay_dir": None,
        "replay_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_agent_arg_parser_without_payload_trim() -> None:
    default_config = _default_config()

    parser = build_agent_arg_parser(
        "Example description",
        default_config,
        api_label="Yutori Navigator n1",
    )
    args = parser.parse_args([])

    assert vars(args) == {
        "task": default_config.task,
        "start_url": default_config.start_url,
        "base_url": default_config.base_url,
        "model": default_config.model,
        "temperature": default_config.temperature,
        "max_steps": default_config.max_steps,
        "viewport_width": default_config.viewport_width,
        "viewport_height": default_config.viewport_height,
        "headless": False,
        "replay_dir": default_config.replay_dir,
        "replay_id": default_config.replay_id,
    }
    assert parser.description == "Example description"


def test_build_agent_arg_parser_with_payload_trim() -> None:
    default_config = _default_config()

    parser = build_agent_arg_parser(
        "Example description",
        default_config,
        api_label="Yutori Navigator n1",
        include_payload_trim=True,
    )
    args = parser.parse_args([])

    assert args.max_request_bytes == default_config.max_request_bytes
    assert args.keep_recent_screenshots == default_config.keep_recent_screenshots


def test_build_agent_arg_parser_honors_overrides_via_cli() -> None:
    default_config = _default_config()

    parser = build_agent_arg_parser(
        "Example description",
        default_config,
        api_label="Yutori Navigator n1",
    )
    args = parser.parse_args(["--task", "custom task", "--max-steps", "5"])

    assert args.task == "custom task"
    assert args.max_steps == 5
