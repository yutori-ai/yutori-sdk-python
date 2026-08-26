"""Characterization tests for ``examples._common.build_agent_arg_parser``.

Pins the exact argument set/defaults of the task/model/agent/browser/replay parser that the
custom-tool example scripts get through ``run_example_main``, extracted out of the ``main()``
bodies that each used to hand-assemble the same argument groups.
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
        "replay_dir": None,
        "replay_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_agent_arg_parser_produces_the_base_argument_set() -> None:
    default_config = _default_config()

    parser = build_agent_arg_parser(
        "Example description",
        default_config,
        api_label="Yutori Navigator n1.5",
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



def test_build_agent_arg_parser_honors_overrides_via_cli() -> None:
    default_config = _default_config()

    parser = build_agent_arg_parser(
        "Example description",
        default_config,
        api_label="Yutori Navigator n1.5",
    )
    args = parser.parse_args(["--task", "custom task", "--max-steps", "5"])

    assert args.task == "custom task"
    assert args.max_steps == 5
