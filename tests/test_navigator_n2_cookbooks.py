"""Credential-free tests for the public n2 cookbook examples."""

from __future__ import annotations

import base64
import importlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

from examples.navigator_n2.remote_sandbox import CuaSandboxComputer
from examples.navigator_n2.shared import TOOL_SET_ALIASES, RunGuard, selected_tool_set
from yutori.navigator import NAVIGATOR_N2_MODEL, TOOL_SET_COMPUTER_USE_LATEST, N2ComputerAgent

ROOT = Path(__file__).resolve().parents[1]


def _screenshot_base64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), (10, 20, 30)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class FakeMouse:
    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        self.calls = calls

    async def click(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("click", x, y, button))

    async def double_click(self, x: int, y: int) -> None:
        self.calls.append(("double_click", x, y))

    async def move(self, x: int, y: int) -> None:
        self.calls.append(("move", x, y))

    async def scroll(self, x: int, y: int, scroll_x: int = 0, scroll_y: int = 3) -> None:
        self.calls.append(("scroll", x, y, scroll_x, scroll_y))

    async def mouse_down(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("mouse_down", x, y, button))

    async def mouse_up(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("mouse_up", x, y, button))

    async def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left") -> None:
        self.calls.append(("drag", start_x, start_y, end_x, end_y, button))


class FakeKeyboard:
    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        self.calls = calls

    async def type(self, text: str) -> None:
        self.calls.append(("type", text))

    async def keypress(self, keys: list[str] | str) -> None:
        self.calls.append(("keypress", tuple(keys) if isinstance(keys, list) else keys))

    async def key_down(self, key: str) -> None:
        self.calls.append(("key_down", key))

    async def key_up(self, key: str) -> None:
        self.calls.append(("key_up", key))


class FakeShell:
    async def run(self, _command: str, timeout: int = 30) -> SimpleNamespace:
        return SimpleNamespace(stdout="/workspace\n", stderr="", returncode=0)


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.mouse = FakeMouse(self.calls)
        self.keyboard = FakeKeyboard(self.calls)
        self.shell = FakeShell()

    async def screenshot_base64(self, **_kwargs: Any) -> str:
        self.calls.append(("screenshot",))
        return _screenshot_base64()

    async def get_dimensions(self) -> tuple[int, int]:
        return 200, 100


class FakeCompletions:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        payload = self.responses.pop(0)

        class Response:
            def model_dump(self) -> dict[str, Any]:
                return payload

        return Response()


def _response(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": message}], "usage": {}}


def test_every_ported_example_is_importable_without_its_optional_runtime() -> None:
    for name in (
        "examples.navigator_n2",
        "examples.navigator_n2.local_driver",
        "examples.navigator_n2.local_macos",
        "examples.navigator_n2.remote_sandbox",
        "examples.navigator_n2.shared",
        "examples.navigator_n2_daytona",
    ):
        assert importlib.import_module(name)


def test_cookbook_uses_a_public_pinned_cua_dependency() -> None:
    project = (ROOT / "examples" / "navigator_n2" / "pyproject.toml").read_text()
    readme = (ROOT / "examples" / "navigator_n2" / "README.md").read_text()

    assert '"cua==0.1.6"' in project
    assert "cua-agent" not in project
    assert "git =" not in project
    assert "private" not in readme.lower()
    assert "n2-preview" not in readme


def test_cookbook_aliases_include_current_and_historical_n2_tool_sets() -> None:
    assert selected_tool_set("latest") == "computer_use_tools-20260825"
    assert selected_tool_set("bash-batch") == "computer_use_tools-20260812"
    assert set(TOOL_SET_ALIASES.values()).issuperset(
        {
            "computer_use_tools-20260708",
            "computer_use_tools-20260716",
            "computer_use_tools-20260825",
        }
    )


async def test_public_cua_adapter_executes_all_current_batch_actions() -> None:
    sandbox = FakeSandbox()
    computer = CuaSandboxComputer(sandbox)
    actions = [
        {"name": "left_click", "arguments": {"coordinates": [100, 100], "modifier": "ctrl"}},
        {"name": "double_click", "arguments": {"coordinates": [200, 100]}},
        {"name": "triple_click", "arguments": {"coordinates": [300, 100]}},
        {"name": "middle_click", "arguments": {"coordinates": [400, 100]}},
        {"name": "right_click", "arguments": {"coordinates": [500, 100]}},
        {
            "name": "scroll",
            "arguments": {"coordinates": [500, 500], "direction": "down", "amount": 2, "modifier": "shift"},
        },
        {"name": "type", "arguments": {"text": "hello"}},
        {"name": "key_press", "arguments": {"key": "ctrl+a"}},
        {"name": "drag", "arguments": {"start_coordinates": [100, 200], "coordinates": [200, 300]}},
        {"name": "mouse_move", "arguments": {"coordinates": [600, 400]}},
        {"name": "mouse_down", "arguments": {}},
        {"name": "mouse_up", "arguments": {}},
        {"name": "hold_key", "arguments": {"key": "shift", "duration": 0}},
        {"name": "wait", "arguments": {"duration": 0}},
        {"name": "screenshot", "arguments": {}},
    ]
    completions = FakeCompletions(
        [
            _response(
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "current",
                            "function": {"name": "computer_batch", "arguments": json.dumps({"actions": actions})},
                        }
                    ],
                }
            ),
            _response({"content": "done", "tool_calls": []}),
        ]
    )
    agent = N2ComputerAgent(
        computer=computer,
        completions=completions,
        callbacks=[RunGuard(3)],
        screenshot_delay=0,
        supports_click_modifiers=True,
    )

    steps = [step async for step in agent.run("exercise the current action set")]

    assert completions.requests[0]["model"] == NAVIGATOR_N2_MODEL
    assert completions.requests[0]["tool_set"] == TOOL_SET_COMPUTER_USE_LATEST
    result = next(
        item
        for step in steps
        for item in step["output"]
        if item.get("type") == "function_call_output" and item.get("call_id") == "current"
    )
    assert result["output"]["result"]["completed"] == 15
    assert ("mouse_down", 120, 40, "left") in sandbox.calls
    assert ("mouse_up", 120, 40, "left") in sandbox.calls
    assert ("key_down", "ctrl") in sandbox.calls
    assert ("key_up", "ctrl") in sandbox.calls
    assert ("key_down", "shift") in sandbox.calls
    assert ("key_up", "shift") in sandbox.calls


async def test_public_cua_adapter_preserves_bash_cwd_when_the_command_fails() -> None:
    class FailingBashShell:
        async def run(self, command: str, timeout: int = 30) -> SimpleNamespace:
            del timeout
            if command == "pwd":
                return SimpleNamespace(stdout="/workspace\n", stderr="", returncode=0)
            sentinel = re.search(r"__YUTORI_N2_BASH_CWD_[a-f0-9]+__", command)
            assert sentinel is not None
            return SimpleNamespace(
                stdout=f"command output\n{sentinel.group()}/next-workspace\n",
                stderr="command error",
                returncode=7,
            )

    sandbox = FakeSandbox()
    sandbox.shell = FailingBashShell()
    computer = CuaSandboxComputer(sandbox)

    output = await computer.run_bash_command("false")

    assert computer._bash_cwd == "/next-workspace"
    assert output == "command output\ncommand error\n[exit code 7]"
