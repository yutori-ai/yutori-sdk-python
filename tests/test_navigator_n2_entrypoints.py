"""Credential-free tests for the public n2 example entrypoints."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples import navigator_n2_daytona
from examples.navigator_n2 import remote_sandbox


def test_daytona_script_declares_its_uv_environment_inline() -> None:
    script = Path(navigator_n2_daytona.__file__).read_text(encoding="utf-8")

    assert '# requires-python = ">=3.10"' in script
    assert '#   "daytona==0.207.0",' in script
    assert '#   "yutori>=0.9.5",' in script


def test_remote_sandbox_cli_accepts_documented_docker_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["remote_sandbox.py", "Open Calculator"])

    args = remote_sandbox.parse_args()

    assert args.task == "Open Calculator"


async def test_remote_sandbox_resolves_yutori_key_before_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImage:
        @staticmethod
        def linux(**_kwargs: object) -> object:
            return object()

    class FakeSandbox:
        @staticmethod
        def ephemeral(*_args: object, **_kwargs: object) -> object:
            pytest.fail("sandbox allocation started before Yutori authentication")

    monkeypatch.setitem(sys.modules, "cua", SimpleNamespace(Image=FakeImage, Sandbox=FakeSandbox))

    def missing_api_key() -> str:
        raise RuntimeError("missing Yutori key")

    monkeypatch.setattr(remote_sandbox, "require_api_key", missing_api_key)
    args = argparse.Namespace(
        max_steps=1,
        task="test",
        tool_set="latest",
        auto_approve=False,
    )

    with pytest.raises(RuntimeError, match="missing Yutori key"):
        await remote_sandbox.main(args)


async def test_remote_sandbox_rejects_tool_set_before_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImage:
        @staticmethod
        def linux(**_kwargs: object) -> object:
            return object()

    class FakeSandbox:
        @staticmethod
        def ephemeral(*_args: object, **_kwargs: object) -> object:
            pytest.fail("sandbox allocation started before tool-set validation")

    monkeypatch.setitem(sys.modules, "cua", SimpleNamespace(Image=FakeImage, Sandbox=FakeSandbox))
    monkeypatch.setattr(remote_sandbox, "require_api_key", lambda: "test-yutori-key")
    args = argparse.Namespace(
        max_steps=1,
        task="test",
        tool_set="not-a-tool-set",
        auto_approve=False,
    )

    with pytest.raises(ValueError, match="unknown tool set"):
        await remote_sandbox.main(args)


async def test_remote_sandbox_wires_local_container_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    fake_sandbox = object()

    class FakeImage:
        @staticmethod
        def linux(**kwargs: object) -> object:
            seen["image"] = kwargs
            return "linux-image"

    class FakeEphemeral:
        async def __aenter__(self) -> object:
            return fake_sandbox

        async def __aexit__(self, *_args: object) -> None:
            seen["sandbox-closed"] = True

    class FakeSandbox:
        @staticmethod
        def ephemeral(image: object, **kwargs: object) -> FakeEphemeral:
            seen["ephemeral"] = (image, kwargs)
            return FakeEphemeral()

    class FakeComputer:
        def __init__(self, sandbox: object) -> None:
            seen["computer-sandbox"] = sandbox

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            seen["agent"] = kwargs

        async def __aenter__(self) -> "FakeAgent":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

    async def fake_run_agent(_agent: object, task: str, *, completions: object) -> None:
        seen["task"] = task

    monkeypatch.setitem(sys.modules, "cua", SimpleNamespace(Image=FakeImage, Sandbox=FakeSandbox))
    monkeypatch.setattr(remote_sandbox, "require_api_key", lambda: "test-yutori-key")
    monkeypatch.setattr(remote_sandbox, "CuaSandboxComputer", FakeComputer)
    monkeypatch.setattr(remote_sandbox, "N2ComputerAgent", FakeAgent)
    monkeypatch.setattr(remote_sandbox, "run_agent", fake_run_agent)
    args = argparse.Namespace(
        max_steps=2,
        task="test",
        tool_set="latest",
        auto_approve=True,
    )

    await remote_sandbox.main(args)

    assert seen["image"] == {"kind": "container"}
    assert seen["ephemeral"] == ("linux-image", {"local": True})
    assert seen["computer-sandbox"] is fake_sandbox
    assert seen["task"] == "test"
    assert seen["sandbox-closed"] is True


def test_remote_sandbox_watch_url_comes_from_the_runtime_info() -> None:
    info = SimpleNamespace(host="localhost", vnc_port=54423)
    assert remote_sandbox._watch_url(SimpleNamespace(_runtime_info=info)) == "http://localhost:54423/vnc.html"
    assert remote_sandbox._watch_url(SimpleNamespace(_runtime_info=None)) is None
    assert remote_sandbox._watch_url(SimpleNamespace()) is None


def test_daytona_cli_record_flag_is_optional_and_order_safe() -> None:
    assert navigator_n2_daytona.parse_args(["task"]).record is False
    assert navigator_n2_daytona.parse_args(["--record", "task"]).record is True
    args = navigator_n2_daytona.parse_args(["task", "--record"])
    assert args.record is True and args.task == "task"


def test_daytona_cli_help_is_safe_without_optional_runtime(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        navigator_n2_daytona.parse_args(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "task" in help_text
    assert "--max-steps" in help_text


@pytest.mark.parametrize("argv", [[], ["--max-steps", "0", "test"]])
def test_daytona_cli_rejects_incomplete_or_invalid_input(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        navigator_n2_daytona.parse_args(argv)

    assert exit_info.value.code == 2


async def test_daytona_validates_yutori_before_ephemeral_sandbox_and_waits_for_delete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    seen: dict[str, object] = {}

    class FakeYutoriClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=object())

        async def __aenter__(self) -> "FakeYutoriClient":
            events.append("yutori-enter")
            return self

        async def __aexit__(self, *_args: object) -> None:
            events.append("yutori-exit")

        async def get_usage(self) -> None:
            events.append("yutori-validated")

    class FakeSandbox:
        async def delete(self, *, wait: bool = False) -> None:
            seen["delete-wait"] = wait
            events.append("sandbox-delete")

    class FakeDaytona:
        async def __aenter__(self) -> "FakeDaytona":
            events.append("daytona-enter")
            return self

        async def __aexit__(self, *_args: object) -> None:
            events.append("daytona-exit")

        async def create(self, params: object) -> FakeSandbox:
            seen["sandbox-params"] = params
            events.append("sandbox-create")
            return FakeSandbox()

    class FakeComputer:
        def __init__(self, _sandbox: FakeSandbox) -> None:
            pass

        async def start(self) -> None:
            events.append("computer-start")

    class FakeAgent:
        stopped_by = "final_answer"

        def __init__(self, **kwargs: object) -> None:
            seen["agent-kwargs"] = kwargs

        async def run(self, _task: str):
            yield {"output": [{"type": "function_call", "name": "bash", "arguments": '{"command":"pwd"}'}]}
            yield {"output": [{"type": "function_call_output", "output": "/workspace"}]}
            yield {
                "output": [
                    {
                        "type": "function_call_output",
                        "output": {"result": "[1:left_click] OK", "image_url": "data:image/png;base64,secret"},
                    }
                ]
            }

    monkeypatch.setattr(navigator_n2_daytona, "AsyncYutoriClient", FakeYutoriClient)
    monkeypatch.setattr(navigator_n2_daytona, "DaytonaComputer", FakeComputer)
    monkeypatch.setattr(navigator_n2_daytona, "N2ComputerAgent", FakeAgent)
    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(
            AsyncDaytona=FakeDaytona,
            CreateSandboxFromSnapshotParams=lambda **kwargs: kwargs,
        ),
    )

    await navigator_n2_daytona.main("test", max_steps=2)

    assert events.index("yutori-validated") < events.index("daytona-enter")
    assert events.index("yutori-validated") < events.index("sandbox-create")
    assert seen["sandbox-params"] == {"snapshot": navigator_n2_daytona.SNAPSHOT, "ephemeral": True}
    assert seen["agent-kwargs"]["max_steps"] == 2
    assert seen["delete-wait"] is True
    assert events[-3:] == ["sandbox-delete", "daytona-exit", "yutori-exit"]
    printed = capsys.readouterr().out
    assert 'ACTION bash: {"command":"pwd"}' in printed
    assert "/workspace" in printed
    assert 'RESULT "[1:left_click] OK"' in printed
    assert "base64,secret" not in printed


async def test_daytona_example_compacts_long_runs_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() with the real agent: usage past the default trigger is checkpointed
    (SDK-default compactor, no compactor wiring in the example) and announced."""
    import base64 as _b64
    import io as _io
    import json as _json
    from unittest.mock import AsyncMock

    from PIL import Image as _Image

    buffer = _io.BytesIO()
    _Image.new("RGB", (100, 100), (10, 20, 30)).save(buffer, format="PNG")
    screenshot = f"data:image/png;base64,{_b64.b64encode(buffer.getvalue()).decode()}"

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "act-1",
                                "function": {
                                    "name": "computer_batch",
                                    "arguments": _json.dumps(
                                        {"actions": [{"action": "left_click", "coordinates": [500, 500]}]}
                                    ),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 60_000},  # past the default 53,760 trigger
            "request_id": "actor-1",
        },
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "<conversation_compaction_summary>\n## Goal\nsummarize\n</conversation_compaction_summary>"
                        ),
                        "tool_calls": [],
                    }
                }
            ],
            "usage": {"prompt_tokens": 2},
            "request_id": "compact-1",
        },
        {
            "choices": [{"message": {"content": "done", "tool_calls": []}}],
            "usage": {"prompt_tokens": 2},
            "request_id": "actor-2",
        },
    ]
    requests: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs: object) -> dict:
            requests.append(kwargs)
            return responses[len(requests) - 1]

    class FakeYutoriClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def __aenter__(self) -> "FakeYutoriClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def get_usage(self) -> dict:
            return {}

    fake_sandbox = SimpleNamespace(
        computer_use=SimpleNamespace(
            start=AsyncMock(),
            display=SimpleNamespace(
                get_info=AsyncMock(return_value=SimpleNamespace(displays=[SimpleNamespace(height=100)]))
            ),
            screenshot=SimpleNamespace(take_full_screen=AsyncMock(return_value=SimpleNamespace(screenshot=screenshot))),
            mouse=SimpleNamespace(click=AsyncMock()),
        ),
        delete=AsyncMock(),
    )

    class FakeDaytona:
        async def __aenter__(self) -> "FakeDaytona":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def create(self, _params: object) -> object:
            return fake_sandbox

    monkeypatch.setattr(navigator_n2_daytona, "AsyncYutoriClient", FakeYutoriClient)
    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(AsyncDaytona=FakeDaytona, CreateSandboxFromSnapshotParams=lambda **kwargs: kwargs),
    )

    await navigator_n2_daytona.main("long task")

    assert len(requests) == 3, "expected actor -> compaction -> actor"
    request_text = _json.dumps(requests[2].get("messages", []))
    assert "working_checkpoint" in request_text, "compacted checkpoint never reached the next actor request"
    printed = capsys.readouterr().out
    assert "Compacted context:" in printed
    assert "done" in printed


async def test_shared_stop_and_summarize_returns_visible_text_only() -> None:
    from examples.navigator_n2 import shared

    class FakeAgent:
        def completion_request(self, extra_messages=None):
            assert extra_messages and "Stop here." in extra_messages[0]["content"][0]["text"]
            return {"model": "n2", "messages": [{"role": "user", "content": "history"}, *extra_messages]}

    class FakeCompletions:
        def __init__(self, message: dict) -> None:
            self._message = message

        async def create(self, **kwargs: object) -> dict:
            assert kwargs["model"] == "n2"
            return {"choices": [{"message": self._message}]}

    summary = await shared.stop_and_summarize(FakeAgent(), FakeCompletions({"content": "did X, found Y"}), "task")
    assert summary == "did X, found Y"
    # A tool-call reply with no visible text yields None (keeps the pre-cap answer).
    empty = await shared.stop_and_summarize(FakeAgent(), FakeCompletions({"content": "", "tool_calls": [{}]}), "task")
    assert empty is None


async def test_daytona_step_cap_takes_one_summarize_only_turn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wrapup_requests: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs: object) -> object:
            wrapup_requests.append(kwargs)
            return {"choices": [{"message": {"content": "summary: reached step 2 of 5"}}]}

    class FakeYutoriClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def __aenter__(self) -> "FakeYutoriClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def get_usage(self) -> dict:
            return {}

    class FakeSandbox:
        async def delete(self, wait: bool = False) -> None:
            pass

    class FakeDaytona:
        async def __aenter__(self) -> "FakeDaytona":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def create(self, _params: object) -> FakeSandbox:
            return FakeSandbox()

    class FakeComputer:
        def __init__(self, _sandbox: object) -> None:
            pass

        async def start(self) -> None:
            pass

    class FakeAgent:
        stopped_by = "max_steps"

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run(self, _task: str):
            if False:  # pragma: no cover - empty async generator
                yield {}

        def completion_request(self, extra_messages=None):
            assert "Stop here." in extra_messages[0]["content"][0]["text"]
            return {"messages": [*extra_messages]}

    monkeypatch.setattr(navigator_n2_daytona, "AsyncYutoriClient", FakeYutoriClient)
    monkeypatch.setattr(navigator_n2_daytona, "DaytonaComputer", FakeComputer)
    monkeypatch.setattr(navigator_n2_daytona, "N2ComputerAgent", FakeAgent)
    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(AsyncDaytona=FakeDaytona, CreateSandboxFromSnapshotParams=lambda **kwargs: kwargs),
    )

    with pytest.raises(RuntimeError, match="summary above"):
        await navigator_n2_daytona.main("long task", max_steps=2)

    assert len(wrapup_requests) == 1
    printed = capsys.readouterr().out
    assert "summary: reached step 2 of 5" in printed


async def test_daytona_rejected_yutori_key_never_enters_daytona(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeYutoriClient:
        async def __aenter__(self) -> "FakeYutoriClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def get_usage(self) -> None:
            raise RuntimeError("invalid Yutori key")

    class FakeDaytona:
        def __init__(self) -> None:
            pytest.fail("Daytona initialized before Yutori authentication succeeded")

    monkeypatch.setattr(navigator_n2_daytona, "AsyncYutoriClient", FakeYutoriClient)
    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(
            AsyncDaytona=FakeDaytona,
            CreateSandboxFromSnapshotParams=lambda **kwargs: kwargs,
        ),
    )

    with pytest.raises(RuntimeError, match="invalid Yutori key"):
        await navigator_n2_daytona.main("test")


class _SandboxTimeout(Exception):
    """Stands in for Daytona's process-execution timeout error (name carries 'timeout')."""


def _daytona_computer_with_exec(exec_) -> "navigator_n2_daytona.DaytonaComputer":
    sandbox = SimpleNamespace(process=SimpleNamespace(exec=exec_), computer_use=None)
    return navigator_n2_daytona.DaytonaComputer(sandbox)


async def test_daytona_bash_timeout_is_the_trained_result_and_clamped() -> None:
    async def exec_(*_args, **kwargs):
        assert kwargs["timeout"] == 600  # the n2 contract clamps the model's request to [0, 600]
        raise _SandboxTimeout("deadline exceeded")

    computer = _daytona_computer_with_exec(exec_)
    result = await computer.run_bash_command("sleep 900", timeout=1200.0)
    assert result == "Command timed out after 600s"


async def test_daytona_bash_zero_timeout_expires_without_running() -> None:
    async def exec_(*_args, **_kwargs):
        pytest.fail("a 0s timeout must expire before the sandbox is reached")

    computer = _daytona_computer_with_exec(exec_)
    assert await computer.run_bash_command("true", timeout=0) == "Command timed out after 0s"


async def test_daytona_bash_non_timeout_errors_still_raise() -> None:
    async def exec_(*_args, **_kwargs):
        raise RuntimeError("sandbox gone")

    computer = _daytona_computer_with_exec(exec_)
    with pytest.raises(RuntimeError, match="sandbox gone"):
        await computer.run_bash_command("true")
