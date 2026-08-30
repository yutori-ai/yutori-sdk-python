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
    assert '#   "yutori>=0.9.4",' in script


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

    async def fake_run_agent(_agent: object, task: str, _guard: object) -> None:
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
