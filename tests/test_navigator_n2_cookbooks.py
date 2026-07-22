"""Credential-free smoke tests for the Navigator n2 Cua cookbooks."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from examples.navigator_n2.local_driver import CuaDriverDesktop
from examples.navigator_n2.shared import RunGuard, build_confirmation_callback, selected_tool_set
from yutori.navigator import TOOL_SET_COMPUTER_USE, TOOL_SET_COMPUTER_USE_BATCH


class FakeDriverClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        if name == "get_desktop_state":
            return {
                "structuredContent": {"screenshot_width": 2880, "screenshot_height": 1800},
                "content": [{"type": "image", "data": base64.b64encode(b"png").decode()}],
            }
        return {"content": [{"type": "text", "text": "ok"}]}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_local_driver_uses_native_desktop_scope_and_deterministic_cleanup():
    client = FakeDriverClient()
    async with CuaDriverDesktop(client, session="test-session") as desktop:
        assert await desktop.screenshot() == base64.b64encode(b"png").decode()
        assert await desktop.get_dimensions() == (2880, 1800)
        await desktop.click(1440, 900, "middle")
        await desktop.double_click(100, 200)
        await desktop.move(10, 20)
        await desktop.scroll(1440, 900, 0, 180)
        await desktop.type("hello")
        await desktop.keypress(["cmd", "c"])
        await desktop.drag([{"x": 1, "y": 2}, {"x": 3, "y": 4}])

    assert client.started and client.closed
    assert client.calls[0] == (
        "start_session",
        {"session": "test-session", "capture_scope": "desktop"},
    )
    action_calls = client.calls[2:-1]
    assert all(arguments.get("scope") == "desktop" for _, arguments in action_calls)
    assert all("pid" not in arguments and "window_id" not in arguments for _, arguments in action_calls)
    assert client.calls[-1] == ("end_session", {"session": "test-session"})


@pytest.mark.asyncio
async def test_safety_defaults_and_step_limit():
    assert selected_tool_set(False) == TOOL_SET_COMPUTER_USE
    assert selected_tool_set(True) == TOOL_SET_COMPUTER_USE_BATCH
    assert await build_confirmation_callback(True)({"tool_name": "computer_batch", "arguments": '{"actions": []}'})

    guard = RunGuard(2)
    assert await guard.on_run_continue({}, [], []) is True
    assert await guard.on_run_continue({}, [], []) is True
    assert await guard.on_run_continue({}, [], []) is False
    assert guard.limit_reached


def test_cookbook_is_pinned_and_does_not_use_an_editable_cua_checkout():
    root = Path(__file__).parents[1] / "examples" / "navigator_n2"
    config = (root / "pyproject.toml").read_text()

    assert "cua==0.1.6" in config
    assert "cua-cli==0.1.12" in config
    assert "cua-driver==0.10.0" in config
    assert 'rev = "257783cd4b10760f435696d6ca107dcaaebec815"' in config
    assert "cua-agent = { path =" not in config
    compile((root / "local_macos.py").read_text(), "local_macos.py", "exec")
    compile((root / "remote_sandbox.py").read_text(), "remote_sandbox.py", "exec")
