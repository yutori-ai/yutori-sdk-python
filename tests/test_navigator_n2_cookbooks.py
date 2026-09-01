"""Credential-free tests for the public n2 cookbook examples."""

from __future__ import annotations

import base64
import importlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from examples.navigator_n2.cua_adapter import CuaSandboxComputer
from examples.navigator_n2.shared import TOOL_SET_ALIASES, RunGuard, selected_tool_set
from examples.navigator_n2_daytona import CWD_SENTINEL, DaytonaComputer
from yutori.navigator import FILE_TOOL_SCRIPT as _FILE_TOOL_SCRIPT
from yutori.navigator import (
    NAVIGATOR_N2_MODEL,
    TOOL_SET_COMPUTER_USE_BASH_BATCH,
    TOOL_SET_COMPUTER_USE_LATEST,
    N2ComputerAgent,
)
from yutori.navigator import format_shell_output as _format_shell_output
from yutori.navigator.n2_actions import TOOL_SETS_WITH_CLICK_MODIFIERS

from .conftest import FakeCompletions

ROOT = Path(__file__).resolve().parents[1]


def _screenshot_base64(image_format: str = "PNG") -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), (10, 20, 30)).save(buffer, format=image_format)
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

    async def screenshot_base64(self, **kwargs: Any) -> str:
        self.calls.append(("screenshot",))
        return _screenshot_base64(str(kwargs.get("format", "png")).upper())

    async def get_dimensions(self) -> tuple[int, int]:
        return 200, 100


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


async def test_daytona_adapter_runs_with_public_agent_loop_end_to_end() -> None:
    screenshot = f"data:image/png;base64,{_screenshot_base64()}"
    sandbox = SimpleNamespace(
        computer_use=SimpleNamespace(
            start=AsyncMock(),
            display=SimpleNamespace(
                get_info=AsyncMock(return_value=SimpleNamespace(displays=[SimpleNamespace(height=100)]))
            ),
            screenshot=SimpleNamespace(take_full_screen=AsyncMock(return_value=SimpleNamespace(screenshot=screenshot))),
            mouse=SimpleNamespace(click=AsyncMock()),
        ),
        process=SimpleNamespace(
            exec=AsyncMock(
                return_value=SimpleNamespace(
                    result=f"listed files\n{CWD_SENTINEL}/workspace",
                    exit_code=0,
                )
            )
        ),
    )
    computer = DaytonaComputer(sandbox)
    await computer.start()
    completions = FakeCompletions(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "screenshot-1",
                                    "function": {
                                        "name": "computer_batch",
                                        "arguments": json.dumps(
                                            {"actions": [{"action": "left_click", "coordinates": [500, 500]}]}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2},
                "request_id": "actor-1",
            },
            {
                "choices": [{"message": {"content": "done", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1},
                "request_id": "actor-2",
            },
        ]
    )
    agent = N2ComputerAgent(
        computer=computer,
        completions=completions,
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
    )

    steps = [step async for step in agent.run("list files")]

    assert steps[-1]["message"]["content"] == "done"
    sandbox.process.exec.assert_not_awaited()
    assert sandbox.computer_use.screenshot.take_full_screen.await_count == 2
    assert completions.requests[1]["extra_body"]["prev_request_id"] == "actor-1"
    tool_result = completions.requests[1]["messages"][-1]
    assert tool_result["role"] == "tool"
    assert [part["type"] for part in tool_result["content"]] == ["text", "image_url"]


def test_cookbook_uses_pinned_public_runtime_dependencies() -> None:
    root_project = (ROOT / "pyproject.toml").read_text()
    project = (ROOT / "examples" / "navigator_n2" / "pyproject.toml").read_text()
    readme = (ROOT / "examples" / "navigator_n2" / "README.md").read_text()
    version_match = re.search(r'^version = "([^"]+)"$', root_project, re.MULTILINE)

    assert version_match is not None
    sdk_version = version_match.group(1)
    assert '"cua-sandbox==0.1.17"' in project
    assert f'"yutori=={sdk_version}"' in project
    assert f'"yutori[macos]=={sdk_version}"' in project
    assert "cua-agent" not in project
    assert "git =" not in project
    assert "private" not in readme.lower()
    assert "n2-preview" not in readme


def test_cookbook_adapter_does_not_shadow_cua_sandbox_package() -> None:
    assert not (ROOT / "examples" / "navigator_n2" / "cua_sandbox.py").exists()


def test_cookbook_aliases_include_current_and_historical_n2_tool_sets() -> None:
    # Tracks the constant rather than a literal: "latest" is meant to follow the newest
    # published set, so pinning the date here fails every time one ships.
    assert selected_tool_set("latest") == TOOL_SET_COMPUTER_USE_LATEST
    assert selected_tool_set("batch-files") == "computer_use_tools-20260825"
    assert selected_tool_set("bash-batch") == "computer_use_tools-20260812"
    assert set(TOOL_SET_ALIASES.values()).issuperset(
        {
            "computer_use_tools-20260708",
            "computer_use_tools-20260716",
            "computer_use_tools-20260825",
            "computer_use_tools-20260830",
        }
    )
    assert selected_tool_set("latest") in TOOL_SETS_WITH_CLICK_MODIFIERS
    assert selected_tool_set("gui") not in TOOL_SETS_WITH_CLICK_MODIFIERS


def test_public_cua_file_tool_script_executes_without_shell_interpolation(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("first\nmatch\n", encoding="utf-8")
    ignored = tmp_path / ".git" / "ignored.txt"
    ignored.parent.mkdir()
    ignored.write_text("match\n", encoding="utf-8")

    def run(**arguments: Any) -> str:
        encoded = base64.b64encode(json.dumps(arguments).encode()).decode()
        result = subprocess.run(
            [sys.executable, "-c", _FILE_TOOL_SCRIPT, encoded],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    common = {"cwd": str(tmp_path)}
    grep_output = run(
        **common,
        operation="grep",
        pattern="match",
        path=str(tmp_path),
        glob=None,
        file_type=None,
        output_mode="content",
        ignore_case=False,
        show_line_numbers=None,
        before_context=None,
        after_context=None,
        context=None,
        head_limit=250,
        multiline=False,
    )
    assert f"{notes}:2:match" in grep_output
    assert str(ignored) not in grep_output

    read_output = run(**common, operation="read", file_path="notes.txt", offset=1, limit=2)
    assert "     2\tmatch" in read_output

    run(**common, operation="write", file_path="draft.txt", content="before")
    edit_output = run(
        **common,
        operation="edit",
        file_path="draft.txt",
        old_string="before",
        new_string="after",
        replace_all=False,
    )
    # The n2 edit contract: the success line plus a cat -n snippet of the edited region.
    assert edit_output.startswith("The file draft.txt has been updated successfully:\n")
    assert "     1\tafter" in edit_output
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "after"

    glob_output = run(**common, operation="glob", pattern="*.txt", path=str(tmp_path))
    assert str(notes) in glob_output


@pytest.mark.parametrize("source", ["x" * 30_001, "x" * 30_000 + "y"])
def test_public_cua_shell_truncation_retains_failure_exit_code(source: str) -> None:
    output = _format_shell_output(source, 7)
    assert output.startswith("Exit code 7\nx")
    assert output.endswith("[... output truncated, 1 more chars ...]")


async def test_public_cua_adapter_labels_jpeg_screenshots_correctly() -> None:
    screenshot = await CuaSandboxComputer(FakeSandbox()).screenshot()
    assert screenshot.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(screenshot.split(",", 1)[1]))) as image:
        assert image.format == "JPEG"


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
        {"name": "hold_key", "arguments": {"key": "shift"}},
        {"name": "mouse_move", "arguments": {"coordinates": [600, 400]}},
        {"name": "mouse_down", "arguments": {}},
        {"name": "mouse_up", "arguments": {}},
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
    # A GUI turn: the [i:name] member lines ride with the turn's frame.
    member_lines = result["output"]["result"].splitlines()
    assert len(member_lines) == 15 and member_lines[0] == "[0:left_click] "
    assert result["output"]["type"] == "input_image"
    assert ("mouse_down", 120, 40, "left") in sandbox.calls
    assert ("mouse_up", 120, 40, "left") in sandbox.calls
    assert ("key_down", "ctrl") in sandbox.calls
    assert ("key_up", "ctrl") in sandbox.calls
    assert ("key_down", "shift") in sandbox.calls
    assert ("key_up", "shift") in sandbox.calls
    held_shift_down = [index for index, call in enumerate(sandbox.calls) if call == ("key_down", "shift")][-1]
    held_shift_up = [index for index, call in enumerate(sandbox.calls) if call == ("key_up", "shift")][-1]
    mouse_move = sandbox.calls.index(("move", 120, 40))
    assert held_shift_down < mouse_move < held_shift_up


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
    assert output == "Exit code 7\ncommand output\ncommand error"


def test_cua_file_tools_match_the_n2_tool_contract(tmp_path: Path) -> None:
    """Pin the exact tool-result strings n2 expects, so the reference adapter
    cannot drift from the documented tool contract."""
    import struct

    def run(**arguments: Any) -> str:
        encoded = base64.b64encode(json.dumps({"cwd": str(tmp_path), **arguments}).encode()).decode()
        result = subprocess.run(
            [sys.executable, "-c", _FILE_TOOL_SCRIPT, encoded],
            check=True,  # expected tool errors are printed results, exit 0
            capture_output=True,
            text=True,
        )
        return result.stdout.rstrip("\n")

    read_args = {"operation": "read", "offset": 1, "limit": 2_000}
    # Pre-checks return their messages as plain results, never raised errors.
    assert run(**read_args, file_path="nope.txt") == "ERROR: file does not exist: nope.txt"
    assert run(**read_args, file_path=".") == "ERROR: path is a directory, not a file: ."

    # The read-before-edit gate: sha256 fingerprints, both messages.
    target = tmp_path / "a.txt"
    target.write_text("hello world\nline two\n", encoding="utf-8")
    edit_args = {"operation": "edit", "file_path": "a.txt", "replace_all": False}
    assert (
        run(**edit_args, old_string="hello", new_string="hi")
        == "ERROR: you must read a.txt before editing it (read it, then edit)."
    )
    assert run(**read_args, file_path="a.txt").startswith("     1\thello world")
    edited = run(**edit_args, old_string="hello", new_string="hi")
    assert edited.startswith("The file a.txt has been updated successfully:\n")
    assert "     1\thi world" in edited  # cat -n snippet of the edited region
    target.write_text(target.read_text(encoding="utf-8") + "more\n", encoding="utf-8")
    assert (
        run(**edit_args, old_string="world", new_string="globe")
        == "ERROR: a.txt changed since you last read it - read it again before editing."
    )

    # Ambiguity and empty-file renders.
    dup = tmp_path / "b.txt"
    dup.write_text("x y x\n", encoding="utf-8")
    run(**read_args, file_path="b.txt")
    assert (
        run(operation="edit", file_path="b.txt", old_string="x", new_string="z", replace_all=False)
        == "ERROR: old_string is not unique (2 occurrences). Add context or pass replace_all=true."
    )
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    assert run(**read_args, file_path="empty.txt") == "[file exists but is empty]"

    # Image reads hand raw bytes to the host, which renders visible image content.
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">IIBBBBB", 64, 48, 8, 6, 0, 0, 0)
    (tmp_path / "img.png").write_bytes(png)
    assert run(**read_args, file_path="img.png").splitlines()[0] == "__YUTORI_IMAGE__"
    # Image and PDF reads must record fingerprints too, or a later edit of the
    # same path loops forever on the read-before-edit gate (Bugbot, PR #281).
    assert not run(
        operation="edit", file_path="img.png", old_string="nope", new_string="x", replace_all=False
    ).startswith("ERROR: you must read")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4 stub")
    assert run(**read_args, file_path="doc.pdf").startswith("[pdf file: doc.pdf - ")
    assert not run(
        operation="edit", file_path="doc.pdf", old_string="nope", new_string="x", replace_all=False
    ).startswith("ERROR: you must read")

    # Grep clamps columns at 500 and skips all six VCS directories.
    (tmp_path / ".hg").mkdir()
    (tmp_path / ".hg" / "hidden.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "wide.txt").write_text("A" * 600 + "needle\n", encoding="utf-8")
    grep_output = run(
        operation="grep",
        pattern="needle",
        path=None,
        glob=None,
        file_type=None,
        output_mode="content",
        ignore_case=False,
        show_line_numbers=None,
        before_context=None,
        after_context=None,
        context=None,
        head_limit=250,
        multiline=False,
    )
    (wide_hit,) = [line for line in grep_output.splitlines() if "wide.txt" in line]
    assert len(wide_hit.split(":", 2)[2]) <= 500
    assert ".hg" not in grep_output


def test_cua_read_file_returns_visible_image_content() -> None:
    """`read` on an image must reach the model as image content: a note with the
    source dimensions plus a WEBP data URL bounded to the 1568-px max edge."""
    import asyncio
    import io as _io

    from PIL import Image as _Image

    from examples.navigator_n2.cua_adapter import CuaSandboxComputer

    buffer = _io.BytesIO()
    _Image.new("RGB", (2000, 500), (200, 30, 30)).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    class _Shell:
        async def run(self, command: str, timeout: int = 30) -> SimpleNamespace:
            if command.strip() == "pwd":
                return SimpleNamespace(stdout="/root\n", stderr="", returncode=0)
            return SimpleNamespace(stdout=f"__YUTORI_IMAGE__\n{encoded}\n", stderr="", returncode=0)

    computer = CuaSandboxComputer(SimpleNamespace(shell=_Shell()))
    result = asyncio.run(computer.read_file("shot.png"))
    assert isinstance(result, dict)
    assert result["text"] == "Loaded image shot.png (2000x500), shown downscaled to 1568x392"
    assert result["image_url"].startswith("data:image/webp;base64,")
    with _Image.open(_io.BytesIO(base64.b64decode(result["image_url"].split(",", 1)[1]))) as shown:
        assert shown.size == (1568, 392)
