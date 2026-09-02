"""Credential-free tests for the public n2 example entrypoints."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples import navigator_n2_daytona
from examples.navigator_n2 import local_docker, local_x11, local_x11_docker, shared


def test_daytona_script_declares_its_uv_environment_inline() -> None:
    script = Path(navigator_n2_daytona.__file__).read_text(encoding="utf-8")

    assert '# requires-python = ">=3.10"' in script
    assert '#   "daytona==0.207.0",' in script
    assert '#   "yutori>=0.9.6",' in script


def test_local_docker_cli_accepts_documented_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["local_docker.py", "Open Calculator"])

    args = local_docker.parse_args()

    assert args.task == "Open Calculator"
    assert args.auto_approve is False


async def test_local_docker_resolves_yutori_key_before_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImage:
        @staticmethod
        def linux(**_kwargs: object) -> object:
            return object()

    class FakeSandbox:
        @staticmethod
        def ephemeral(*_args: object, **_kwargs: object) -> object:
            pytest.fail("sandbox allocation started before Yutori authentication")

    monkeypatch.setitem(sys.modules, "cua_sandbox", SimpleNamespace(Image=FakeImage, Sandbox=FakeSandbox))

    def missing_api_key() -> str:
        raise RuntimeError("missing Yutori key")

    monkeypatch.setattr(local_docker, "require_api_key", missing_api_key)
    args = argparse.Namespace(
        max_steps=1,
        task="test",
        tool_set="latest",
        auto_approve=False,
    )

    with pytest.raises(RuntimeError, match="missing Yutori key"):
        await local_docker.main(args)


async def test_local_docker_rejects_tool_set_before_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImage:
        @staticmethod
        def linux(**_kwargs: object) -> object:
            return object()

    class FakeSandbox:
        @staticmethod
        def ephemeral(*_args: object, **_kwargs: object) -> object:
            pytest.fail("sandbox allocation started before tool-set validation")

    monkeypatch.setitem(sys.modules, "cua_sandbox", SimpleNamespace(Image=FakeImage, Sandbox=FakeSandbox))
    monkeypatch.setattr(local_docker, "require_api_key", lambda: "test-yutori-key")
    args = argparse.Namespace(
        max_steps=1,
        task="test",
        tool_set="not-a-tool-set",
        auto_approve=False,
    )

    with pytest.raises(ValueError, match="unknown tool set"):
        await local_docker.main(args)


async def test_local_docker_wires_local_container_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setitem(sys.modules, "cua_sandbox", SimpleNamespace(Image=FakeImage, Sandbox=FakeSandbox))
    monkeypatch.setattr(local_docker, "require_api_key", lambda: "test-yutori-key")
    monkeypatch.setattr(local_docker, "CuaSandboxComputer", FakeComputer)
    monkeypatch.setattr(shared, "N2ComputerAgent", FakeAgent)
    monkeypatch.setattr(shared, "run_agent", fake_run_agent)
    args = argparse.Namespace(
        max_steps=2,
        task="test",
        tool_set="latest",
        auto_approve=True,
    )

    await local_docker.main(args)

    assert seen["image"] == {"kind": "container"}
    assert seen["ephemeral"] == ("linux-image", {"local": True})
    assert seen["computer-sandbox"] is fake_sandbox
    agent_kwargs = seen["agent"]
    assert isinstance(agent_kwargs, dict)
    assert agent_kwargs["supports_click_modifiers"] is True
    assert seen["task"] == "test"
    assert seen["sandbox-closed"] is True


def test_local_docker_watch_url_comes_from_the_runtime_info() -> None:
    info = SimpleNamespace(host="localhost", vnc_port=54423)
    assert local_docker._watch_url(SimpleNamespace(_runtime_info=info)) == "http://localhost:54423/vnc.html"
    assert local_docker._watch_url(SimpleNamespace(_runtime_info=None)) is None
    assert local_docker._watch_url(SimpleNamespace()) is None


def test_local_x11_docker_cli_accepts_documented_command() -> None:
    args = local_x11_docker.parse_args(["Open Calculator"])

    assert args.task == "Open Calculator"
    assert args.auto_approve is True
    assert local_x11_docker.parse_args(["--confirm-actions", "Open Calculator"]).auto_approve is False


def test_local_x11_native_keeps_shell_auto_approval_internal() -> None:
    native_args = local_x11.parse_args(["Open Calculator", "--auto-approve"])
    assert native_args.auto_approve_shell is False
    assert native_args.workspace is None

    docker_args = local_x11.parse_args(
        ["Open Calculator", "--auto-approve", "--auto-approve-shell", "--workspace", "/work"]
    )
    assert docker_args.auto_approve_shell is True
    assert docker_args.workspace == "/work"


async def test_local_x11_delegates_to_shared_agent_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    computer = object()

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            seen["api-key"] = api_key
            self.chat = SimpleNamespace(completions=object())

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

    async def fake_run_computer_agent(**kwargs: object) -> None:
        seen["agent-args"] = kwargs

    def fake_local_x11_computer(*, cwd: str | None) -> object:
        seen["computer-cwd"] = cwd
        return computer

    monkeypatch.setattr(local_x11, "_require_x11", lambda: None)
    monkeypatch.setattr(local_x11, "require_api_key", lambda: "test-yutori-key")
    monkeypatch.setattr(local_x11, "AsyncYutoriClient", FakeClient)
    monkeypatch.setattr(local_x11, "LocalX11Computer", fake_local_x11_computer)
    monkeypatch.setattr(local_x11, "run_computer_agent", fake_run_computer_agent)

    args = local_x11.parse_args(["Open Calculator", "--auto-approve", "--workspace", "/work"])
    await local_x11.main(args)

    agent_args = seen["agent-args"]
    assert isinstance(agent_args, dict)
    assert seen["api-key"] == "test-yutori-key"
    assert seen["computer-cwd"] == "/work"
    assert agent_args["computer"] is computer
    assert agent_args["args"] is args
    assert agent_args["always_confirm_shell"] is True
    assert agent_args["supports_click_modifiers"] is True


def test_local_x11_docker_builds_image_and_delegates_to_local_x11(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("YUTORI_API_KEY", "test-yutori-key")
    monkeypatch.setattr(
        local_x11_docker,
        "require_api_key",
        lambda: pytest.fail("get_auth_status already resolved an authenticated source"),
    )
    monkeypatch.setattr(
        local_x11_docker,
        "get_auth_status",
        lambda: SimpleNamespace(authenticated=True, source="env_var", config_path=None),
    )
    monkeypatch.setattr(local_x11_docker.shutil, "which", lambda _command: "/usr/local/bin/docker")
    monkeypatch.setattr(local_x11_docker, "_available_local_port", lambda: 54321)
    monkeypatch.setattr(local_x11_docker.subprocess, "run", fake_run)
    args = argparse.Namespace(
        max_steps=7,
        task="Open Calculator",
        tool_set="latest",
        auto_approve=True,
    )

    local_x11_docker.main(args)

    assert commands[0] == ["docker", "info"]
    assert commands[1][0:2] == ["docker", "build"]
    docker_run = commands[2]
    assert docker_run[0:2] == ["docker", "run"]
    assert ["--publish", "127.0.0.1:54321:6080"] == docker_run[
        docker_run.index("--publish") : docker_run.index("--publish") + 2
    ]
    assert ["--env", "YUTORI_API_KEY"] == docker_run[
        docker_run.index("YUTORI_API_KEY") - 1 : docker_run.index("YUTORI_API_KEY") + 1
    ]
    assert "/sdk/examples/navigator_n2/local_x11.py" in docker_run
    assert "Open Calculator" in docker_run
    assert ["--workspace", "/work"] == docker_run[docker_run.index("--workspace") : docker_run.index("--workspace") + 2]
    assert docker_run[-2:] == ["--auto-approve", "--auto-approve-shell"]
    assert "Watch the desktop live: http://localhost:54321/vnc.html" in capsys.readouterr().out

    documentation = Path(local_x11_docker.__file__).with_name("README.md").read_text(encoding="utf-8")
    assert 'uv run python local_x11_docker.py "Launch xcalc and compute 17 * 23."' in documentation
    assert "A terminal is open" not in documentation
    assert "view-only noVNC session" in documentation


def test_local_x11_docker_uses_sdk_config_path_for_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_x11_docker,
        "require_api_key",
        lambda: pytest.fail("get_auth_status already resolved an authenticated source"),
    )
    monkeypatch.setattr(
        local_x11_docker,
        "get_auth_status",
        lambda: SimpleNamespace(
            authenticated=True,
            source="config_file",
            config_path="/custom/yutori/config.json",
        ),
    )

    assert local_x11_docker._authentication_args() == [
        "--mount",
        "type=bind,source=/custom/yutori/config.json,target=/root/.yutori/config.json,readonly",
    ]


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
    from yutori.navigator import TOOL_SET_COMPUTER_USE_LATEST

    assert seen["agent-kwargs"]["tool_set"] == TOOL_SET_COMPUTER_USE_LATEST
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


async def test_daytona_triple_click_pairs_a_native_double_with_one_single() -> None:
    clicks: list[tuple[int, int, str, bool]] = []

    class FakeMouse:
        async def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
            clicks.append((x, y, button, double))

    computer = navigator_n2_daytona.DaytonaComputer(
        SimpleNamespace(process=SimpleNamespace(), computer_use=SimpleNamespace(mouse=FakeMouse()))
    )

    await computer.triple_click(5, 9)

    # The native double survives any network latency; the single completes the
    # triple when round-trips fit the OS multi-click window.
    assert clicks == [(5, 9, "left", True), (5, 9, "left", False)]


async def test_daytona_file_tools_ride_the_shared_mixin() -> None:
    calls: list[tuple[str, str | None, int]] = []

    class FakeProcess:
        async def exec(self, command: str, cwd: str | None = None, timeout: int = 30) -> SimpleNamespace:
            calls.append((command, cwd, timeout))
            if command == "pwd":
                return SimpleNamespace(result="/root\n", exit_code=0)
            return SimpleNamespace(result="     1\thello\n", exit_code=0)

    fake_sandbox = SimpleNamespace(process=FakeProcess(), computer_use=SimpleNamespace())
    computer = navigator_n2_daytona.DaytonaComputer(fake_sandbox)

    assert await computer.read_file("notes.txt") == "     1\thello"
    assert calls[0][0] == "pwd"  # cwd resolved once for relative paths
    assert calls[1][0].startswith("python3 -c ") and calls[1][2] == 30


async def test_daytona_file_tool_failures_become_plain_error_results() -> None:
    class FakeProcess:
        async def exec(self, command: str, cwd: str | None = None, timeout: int = 30) -> SimpleNamespace:
            if command == "pwd":
                return SimpleNamespace(result="/root\n", exit_code=0)
            return SimpleNamespace(result="Traceback ...\nValueError: boom\n", exit_code=2)

    fake_sandbox = SimpleNamespace(process=FakeProcess(), computer_use=SimpleNamespace())
    computer = navigator_n2_daytona.DaytonaComputer(fake_sandbox)

    assert await computer.write_file("a.txt", "x") == "ERROR: ValueError: boom"


async def test_daytona_bash_timeout_is_the_expected_result_and_clamped() -> None:
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


async def test_daytona_bash_timeout_detected_from_the_message_alone() -> None:
    async def exec_(*_args, **_kwargs):
        # A generically-named error carrying Daytona's live expiry message,
        # which says "timeout" rather than "timed out".
        raise RuntimeError("Failed to execute command: command execution timeout")

    computer = _daytona_computer_with_exec(exec_)
    assert await computer.run_bash_command("sleep 900", timeout=5) == "Command timed out after 5s"


def _daytona_computer_with_scroll(scrolls: list) -> "navigator_n2_daytona.DaytonaComputer":
    async def scroll(x, y, direction, amount):
        scrolls.append((x, y, direction, amount))

    sandbox = SimpleNamespace(process=None, computer_use=SimpleNamespace(mouse=SimpleNamespace(scroll=scroll)))
    computer = navigator_n2_daytona.DaytonaComputer(sandbox)
    computer._height = 768
    return computer


async def test_daytona_scroll_prefers_the_models_own_units() -> None:
    scrolls: list = []
    computer = _daytona_computer_with_scroll(scrolls)

    await computer.scroll(10, 20, 0, 154, model_action={"action": "scroll", "direction": "up", "amount": 7})

    assert scrolls == [(10, 20, "up", 7)]  # not the 2 notches the 154px fallback would reconstruct


async def test_daytona_scroll_reconstructs_notches_without_model_action() -> None:
    scrolls: list = []
    computer = _daytona_computer_with_scroll(scrolls)

    await computer.scroll(10, 20, 0, 230)  # 230px on a 768-tall screen = 3 notches
    await computer.scroll(10, 20, 0, -1)  # tiny distances still scroll at least one notch

    assert scrolls == [(10, 20, "down", 3), (10, 20, "up", 1)]


async def test_daytona_scroll_rejects_horizontal_in_both_forms() -> None:
    computer = _daytona_computer_with_scroll([])

    with pytest.raises(NotImplementedError):
        await computer.scroll(10, 20, 0, 0, model_action={"action": "scroll", "direction": "left", "amount": 2})
    with pytest.raises(NotImplementedError):
        await computer.scroll(10, 20, 45, 0)


async def test_daytona_bash_background_result_matches_the_expected_format() -> None:
    async def exec_(*_args, **_kwargs):
        return SimpleNamespace(result="4242\n", exit_code=0)

    computer = _daytona_computer_with_exec(exec_)
    result = await computer.run_bash_command("sleep 999", run_in_background=True)

    lines = result.split("\n")
    assert lines[0].startswith("Started background task `bash_")
    assert lines[1].startswith("stdout+stderr is streaming to: /tmp/yutori-n2-")
    assert lines[2] == "Use the read tool on that file to retrieve output."
    assert lines[3] == "Process id: 4242"
    assert lines[4] == "To cancel: run bash with `kill 4242`"


def test_daytona_default_turn_budget_is_500() -> None:
    assert navigator_n2_daytona.MAX_STEPS == 500
    assert navigator_n2_daytona.parse_args(["task"]).max_steps == 500


async def test_daytona_bash_background_pid_lines_are_conditional() -> None:
    async def exec_(*_args, **_kwargs):
        return SimpleNamespace(result="", exit_code=0)

    computer = _daytona_computer_with_exec(exec_)
    result = await computer.run_bash_command("sleep 999", run_in_background=True)

    lines = result.split("\n")
    assert len(lines) == 3  # no `Process id: ` / `kill ` lines with nothing to kill
    assert lines[2] == "Use the read tool on that file to retrieve output."


async def test_daytona_bash_background_start_failure_is_a_normal_result() -> None:
    async def exec_(*_args, **_kwargs):
        raise RuntimeError("sandbox gone")

    computer = _daytona_computer_with_exec(exec_)
    result = await computer.run_bash_command("sleep 999", run_in_background=True)

    assert result.startswith("ERROR: failed to start background command: ")


async def test_daytona_bash_background_launch_failure_is_an_error_result() -> None:
    async def exec_(*_args, **_kwargs):
        return SimpleNamespace(result="sh: 1: cannot fork\n", exit_code=2)

    computer = _daytona_computer_with_exec(exec_)
    result = await computer.run_bash_command("sleep 999", run_in_background=True)

    assert result.startswith("ERROR: failed to start background command: exit code 2")
    assert "cannot fork" in result
    assert "Started background task" not in result
