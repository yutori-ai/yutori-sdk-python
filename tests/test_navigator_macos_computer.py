"""Tests for the Python-owned macOS computer handler."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import yutori.navigator.macos.computer as computer_module
from yutori.navigator.macos.computer import (
    MacOSActionRefusedError,
    MacOSComputer,
    MacOSFocusChangedError,
    MacOSRecoverableActionError,
    MacOSTargetCrashedError,
    MacOSUncertainActionError,
)
from yutori.navigator.macos.frontmost import FrontmostApp
from yutori.navigator.macos.polling import FramePollResult
from yutori.navigator.macos.transport import CuaDriverToolError, CuaDriverUncertainActionError
from yutori.navigator.macos.types import MacOSPresentationStatus, N2Observation


def _png(width: int = 2560, height: int = 1600) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (15, 25, 35)).save(output, format="PNG")
    return output.getvalue()


class FakeTransport:
    def __init__(self, frames: list[bytes] | None = None):
        self.frames = list(frames or [_png()])
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        read_only: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        del timeout_seconds
        self.calls.append((name, arguments, read_only))
        if name == "get_desktop_state":
            frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
            with Image.open(io.BytesIO(frame)) as image:
                width, height = image.size
            return {
                "content": [{"type": "image", "data": base64.b64encode(frame).decode("ascii")}],
                "structuredContent": {"screenshot_width": width, "screenshot_height": height},
            }
        if name == "set_agent_cursor_theme" and arguments["theme_id"] == "yutori.default":
            return {"structuredContent": {"ok": True}}
        return {"structuredContent": {"ok": True}}


class PresentationSink:
    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self.status = MacOSPresentationStatus(True, True, "active", "yutori")

    async def present(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def blocks_point(self, _point: tuple[float, float]) -> bool:
        return False


async def test_context_manager_owns_one_session_and_encodes_native_geometry():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=True, presentation=False) as computer:
        observation = await computer.screenshot()
        assert (observation.native_width, observation.native_height) == (2560, 1600)
        assert (observation.encoded_width, observation.encoded_height) == (1920, 1200)
        assert observation.media_type in {"image/webp", "image/jpeg"}
        assert computer.current_observation == observation
        assert computer.presentation_status.codec in {"webp", "jpeg"}
        assert computer.timings["screenshots"] == 1
    assert transport.started and transport.closed
    assert [call[0] for call in transport.calls].count("start_session") == 1
    assert [call[0] for call in transport.calls].count("end_session") == 1


async def test_mutation_invalidates_the_frame_captured_during_session_startup():
    transport = FakeTransport([_png(100, 50), _png(200, 80)])
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        await computer.launch_app(name="Calculator")
        observation = await computer.screenshot()
    assert (observation.native_width, observation.native_height) == (200, 80)
    assert [call[0] for call in transport.calls].count("get_desktop_state") == 2


async def test_expired_deadline_blocks_session_startup():
    transport = FakeTransport()
    computer = MacOSComputer(
        transport,
        owns_transport=False,
        presentation=False,
        execution_deadline=0,
    )
    with pytest.raises(asyncio.CancelledError, match="deadline"):
        await computer.__aenter__()
    assert not transport.started


async def test_wait_is_not_mistaken_for_a_distant_deadline(monkeypatch):
    computer = MacOSComputer(
        FakeTransport(),
        owns_transport=False,
        presentation=False,
        execution_deadline=time.monotonic() + 10,
    )
    original_wait = asyncio.wait
    observed_timeouts: list[float | None] = []

    async def observe_wait(
        tasks: Any,
        *,
        timeout: float | None = None,
        return_when: str = asyncio.ALL_COMPLETED,
    ) -> Any:
        observed_timeouts.append(timeout)
        return await original_wait(tasks, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", observe_wait)
    await computer._sleep(0.001)

    assert observed_timeouts == [None]
    assert computer.cancellation.cause is None


async def test_wait_stops_at_the_execution_deadline():
    computer = MacOSComputer(
        FakeTransport(),
        owns_transport=False,
        presentation=False,
        execution_deadline=time.monotonic() + 0.01,
    )
    with pytest.raises(asyncio.CancelledError, match="deadline"):
        await computer._sleep(10)
    assert computer.cancellation.cause == "deadline"


async def test_capture_retries_two_declined_frames_before_succeeding(monkeypatch):
    class DecliningTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def call_tool(self, name, arguments, **kwargs):
            if name == "get_desktop_state":
                self.attempts += 1
                if self.attempts < 3:
                    return {"content": [], "structuredContent": {}}
            return await super().call_tool(name, arguments, **kwargs)

    transport = DecliningTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(computer, "_sleep", no_wait)
    observation = await computer.screenshot()
    assert observation.native_width == 2560
    assert transport.attempts == 3


async def test_capture_rejects_reported_geometry_that_disagrees_with_pixels(monkeypatch):
    class MismatchedTransport(FakeTransport):
        async def call_tool(self, name, arguments, **kwargs):
            result = await super().call_tool(name, arguments, **kwargs)
            if name == "get_desktop_state":
                result["structuredContent"]["screenshot_width"] += 1
            return result

    computer = MacOSComputer(MismatchedTransport(), owns_transport=False, presentation=False)
    monkeypatch.setattr(computer, "_sleep", lambda _seconds: asyncio.sleep(0))
    with pytest.raises(RuntimeError, match="reported screenshot width"):
        await computer.screenshot()


def test_pillow_encoder_reports_exact_jpeg_fallback(monkeypatch):
    original_save = Image.Image.save

    def fail_webp(image, output, format=None, **kwargs):
        if format == "WEBP":
            raise OSError("WebP unavailable")
        return original_save(image, output, format=format, **kwargs)

    monkeypatch.setattr(Image.Image, "save", fail_webp)
    encoded, codec = MacOSComputer._encode_with_pillow(_png(100, 50))
    assert codec == "jpeg"
    with Image.open(io.BytesIO(encoded)) as image:
        assert image.format == "JPEG"


async def test_click_counts_and_modifiers_are_forwarded_atomically():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        await computer.click(10, 20, count=3, modifier=["cmd", "shift"])
    click = next(call for call in transport.calls if call[0] == "click")
    assert click[1]["count"] == 3
    assert click[1]["modifier"] == ["cmd", "shift"]
    assert click[1]["delivery_mode"] == "foreground"


async def test_missing_overlay_preparation_is_fail_soft_and_never_compiles_at_startup(monkeypatch):
    async def fail_start(_controller):
        raise RuntimeError("not prepared")

    monkeypatch.setattr("yutori.navigator.macos.computer.MacOSPresentationController.start", fail_start)
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=True) as computer:
        observation = await computer.screenshot()
        assert observation.native_width == 2560
        assert computer.presentation is None
        assert computer.presentation_status.degradation_reason == "overlay_start_failed:RuntimeError"
    cursor_calls = [call for call in transport.calls if call[0] == "set_agent_cursor_enabled"]
    assert cursor_calls[-1][1]["enabled"] is True


async def test_native_cursor_fallback_order_uses_current_then_cursorless():
    class ThemeTransport(FakeTransport):
        async def call_tool(self, name, arguments, **kwargs):
            if name == "set_agent_cursor_theme":
                raise CuaDriverToolError("theme missing")
            return await super().call_tool(name, arguments, **kwargs)

    computer = MacOSComputer(ThemeTransport(), owns_transport=False, presentation=False)
    assert await computer._select_native_cursor() == "current"

    class CursorlessTransport(FakeTransport):
        async def call_tool(self, name, arguments, **kwargs):
            if name == "set_agent_cursor_enabled":
                raise RuntimeError("cursor unavailable")
            return await super().call_tool(name, arguments, **kwargs)

    computer = MacOSComputer(CursorlessTransport(), owns_transport=False, presentation=False)
    assert await computer._select_native_cursor() == "cursorless"


async def test_stop_region_refuses_click_drag_and_anchored_scroll_before_driver_input():
    transport = FakeTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    computer._native_size = (1000, 1000)
    sink = PresentationSink()
    sink.blocks_point = lambda point: point[0] >= 900
    computer.presentation = sink

    with pytest.raises(MacOSActionRefusedError):
        await computer.click(950, 100)
    with pytest.raises(MacOSActionRefusedError):
        await computer.drag([{"x": 10, "y": 10}, {"x": 950, "y": 100}])
    with pytest.raises(MacOSActionRefusedError):
        await computer.scroll(950, 100, 0, 100)
    assert not [call for call in transport.calls if call[0] in {"click", "drag", "scroll"}]


async def test_scroll_translates_horizontal_pixel_deltas_to_line_scrolls():
    transport = FakeTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    computer._native_size = (1000, 800)

    await computer.scroll(10, 10, 300, 0)
    name, arguments, _ = transport.calls[-1]
    assert name == "scroll"
    assert arguments["direction"] == "right" and arguments["amount"] == 3 and arguments["by"] == "line"

    await computer.scroll(10, 10, -100, 0)
    name, arguments, _ = transport.calls[-1]
    assert name == "scroll"
    assert arguments["direction"] == "left" and arguments["amount"] == 1


async def test_modified_scroll_fails_recoverably_without_driver_input():
    transport = FakeTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    with pytest.raises(MacOSRecoverableActionError, match="held modifier"):
        await computer.scroll(10, 10, 0, 100, modifier=["ctrl"])
    assert not transport.calls


async def test_file_tools_preserve_the_served_contract(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("first\nmatch\nthird\n", encoding="utf-8")
    computer = MacOSComputer(
        FakeTransport(),
        owns_transport=False,
        presentation=False,
        allow_local_shell=True,
    )
    computer._bash_cwd = str(tmp_path)

    assert await computer.read_file("notes.txt", offset=1, limit=1) == "     1\tfirst"
    assert await computer.read_file("notes.txt", offset=2, limit=1) == "     2\tmatch"
    assert await computer.edit_file("created.txt", "", "created") == "File created successfully at: created.txt"
    assert (tmp_path / "created.txt").read_text() == "created"
    with pytest.raises(MacOSRecoverableActionError, match="already exists"):
        await computer.edit_file("created.txt", "", "replacement")

    grep_output = await computer.grep_files("match", output_mode="content")
    assert f"{notes}:2:match" in grep_output
    assert str(notes) in await computer.glob_files("*.txt")


async def test_held_modifier_is_emulated_by_the_pinned_driver_actions():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        await computer.key_down("ctrl")
        await computer.click(10, 20)
        await computer.key_up("ctrl")
        with pytest.raises(MacOSRecoverableActionError, match="modifier keys"):
            await computer.key_down("a")

    click = next(call for call in transport.calls if call[0] == "click")
    assert click[1]["modifier"] == ["ctrl"]


async def test_manual_drag_and_timed_held_modifier_use_public_driver_primitives():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        await computer.move(10, 20)
        await computer.left_mouse_down()
        await computer.move(30, 40)
        await computer.left_mouse_up()
        await computer.hold_key("shift", ms=0)

    drag = next(call for call in transport.calls if call[0] == "drag")
    assert drag[1]["from_x"] == 10
    assert drag[1]["to_y"] == 40
    assert not [call for call in transport.calls if call[0] in {"mouse_down", "mouse_up", "hold_key"}]


async def test_uncertain_mutation_captures_a_fresh_observation():
    class UncertainTransport(FakeTransport):
        async def call_tool(self, name, arguments, **kwargs):
            if name == "click":
                raise CuaDriverUncertainActionError("lost acknowledgement")
            return await super().call_tool(name, arguments, **kwargs)

    computer = MacOSComputer(UncertainTransport(), owns_transport=False, presentation=False)
    with pytest.raises(MacOSUncertainActionError) as raised:
        await computer.click(10, 20)
    assert raised.value.observation is not None
    assert raised.value.observation.native_width == 2560


async def test_stop_cancels_an_in_flight_driver_action():
    class HangingTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.action_started = asyncio.Event()
            self.action_cancelled = False

        async def call_tool(self, name, arguments, **kwargs):
            if name == "click":
                self.action_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.action_cancelled = True
                    raise
            return await super().call_tool(name, arguments, **kwargs)

    transport = HangingTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    action = asyncio.create_task(computer.click(10, 20))
    await transport.action_started.wait()
    computer.cancellation.request("operator_stop")
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await action
    assert computer.cancellation.cause == "operator_stop"
    assert transport.action_cancelled


async def test_outer_task_cancellation_cancels_an_in_flight_driver_action():
    class HangingTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.action_started = asyncio.Event()
            self.action_cancelled = False

        async def call_tool(self, name, arguments, **kwargs):
            if name == "click":
                self.action_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.action_cancelled = True
                    raise
            return await super().call_tool(name, arguments, **kwargs)

    transport = HangingTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    action = asyncio.create_task(computer.click(10, 20))
    await transport.action_started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert transport.action_cancelled


async def test_stop_cancels_capture_and_wait():
    class HangingCaptureTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.capture_started = asyncio.Event()

        async def call_tool(self, name, arguments, **kwargs):
            if name == "get_desktop_state":
                self.capture_started.set()
                await asyncio.Future()
            return await super().call_tool(name, arguments, **kwargs)

    capture_computer = MacOSComputer(HangingCaptureTransport(), owns_transport=False, presentation=False)
    capture = asyncio.create_task(capture_computer.screenshot())
    await capture_computer.transport.capture_started.wait()
    capture_computer.cancellation.request("operator_stop")
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await capture
    assert capture_computer.cancellation.cause == "operator_stop"

    wait_computer = MacOSComputer(presentation=False)
    waiting = asyncio.create_task(wait_computer.wait(300_000))
    await asyncio.sleep(0)
    wait_computer.cancellation.request("operator_stop")
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert wait_computer.cancellation.cause == "operator_stop"


async def test_target_liveness_allows_at_most_two_recoveries(monkeypatch):
    recoveries = 0

    async def recover():
        nonlocal recoveries
        recoveries += 1
        return 100 + recoveries

    computer = MacOSComputer(
        FakeTransport(),
        owns_transport=False,
        presentation=False,
        target_pid=100,
        recover_target=recover,
    )
    monkeypatch.setattr(computer, "_pid_alive", lambda _pid: False)
    with pytest.raises(MacOSTargetCrashedError):
        await computer._ensure_target_alive()
    assert recoveries == 2
    assert computer.cancellation.cause == "target_crash"


async def test_stop_cancels_a_hung_target_recovery(monkeypatch):
    recovery_started = asyncio.Event()
    recovery_cancelled = False

    async def recover():
        nonlocal recovery_cancelled
        recovery_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            recovery_cancelled = True
            raise

    computer = MacOSComputer(
        FakeTransport(),
        owns_transport=False,
        presentation=False,
        target_pid=100,
        recover_target=recover,
    )
    monkeypatch.setattr(computer, "_pid_alive", lambda _pid: False)
    recovery = asyncio.create_task(computer._ensure_target_alive())
    await recovery_started.wait()
    computer.cancellation.request("operator_stop")
    with pytest.raises(asyncio.CancelledError):
        await recovery
    assert computer.cancellation.cause == "operator_stop"
    assert recovery_cancelled


async def test_foreground_shell_preserves_contract_and_emits_no_output_to_presentation(tmp_path):
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    sink = PresentationSink()
    computer.presentation = sink
    result = await computer.run_shell_command(
        'API_KEY=topsecret printf %s "$HOME"', cwd=str(tmp_path), timeout_seconds=5
    )
    computer.presentation = None

    assert result == os.path.expanduser("~")
    shell_events = [event["event"] for event in sink.events]
    assert computer.shell_events == tuple(shell_events)
    assert [event.state for event in shell_events] == ["starting", "running", "completed"]
    assert all(os.path.expanduser("~") not in repr(event) for event in shell_events)
    assert all("topsecret" not in repr(event) for event in shell_events)
    assert all("[REDACTED]" in event.command for event in shell_events)


async def test_shell_preview_redacts_explicit_known_secrets():
    secret = "short-active-key"
    computer = MacOSComputer(presentation=False, allow_local_shell=True, known_secrets=secret)
    assert computer._known_secrets == (secret,)
    assert await computer.run_shell_command(f"printf %s {secret}") == secret
    assert all(secret not in event.command for event in computer.shell_events)


async def test_bash_does_not_load_api_keys_from_login_profiles(tmp_path, monkeypatch):
    secret = "yt-profile-secret-abcdefghijklmnop"
    (tmp_path / ".bash_profile").write_text(f"export YUTORI_API_KEY={secret}\n")
    bash_env = tmp_path / "bash-env"
    bash_env.write_text(f"export YUTORI_API_KEY={secret}\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASH_ENV", str(bash_env))
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)
    computer = MacOSComputer(presentation=False, allow_local_shell=True)

    assert await computer.run_bash_command('printf %s "${YUTORI_API_KEY-unset}"') == "unset"
    await computer.run_bash_command('printf %s "${YUTORI_API_KEY-unset}"', run_in_background=True)
    background = next(iter(computer._background.values()))
    for _ in range(100):
        if background.terminal_state is not None:
            break
        await asyncio.sleep(0.02)
    assert background.output_path.read_text() == "unset"
    await computer.aclose()
    background.output_path.unlink(missing_ok=True)


async def test_bash_persists_working_directory_without_affecting_shell_command(tmp_path):
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    await computer.run_bash_command(f"cd {tmp_path}")
    assert (await computer.run_bash_command("pwd")).strip() == str(tmp_path)
    await computer.run_shell_command("pwd", cwd=str(tmp_path.parent))
    assert (await computer.run_bash_command("pwd")).strip() == str(tmp_path)


async def test_explicit_terminal_command_is_allowed_unchanged(monkeypatch):
    captured: list[tuple[tuple[str, ...], bytes]] = []

    class Process:
        pid = 123
        returncode = 0

        async def communicate(self, input_data):
            captured.append((argv, input_data))
            return b"launched", None

    async def create(*command, **_kwargs):
        nonlocal argv
        argv = command
        return Process()

    argv: tuple[str, ...] = ()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    assert await computer.run_shell_command("open -a Terminal") == "launched"
    assert captured[0][1] == b"open -a Terminal"
    assert "open -a Terminal" not in " ".join(captured[0][0])


async def test_foreground_shell_timeout_and_cancellation_kill_the_process_group():
    timeout_computer = MacOSComputer(presentation=False, allow_local_shell=True)
    with pytest.raises(TimeoutError, match="timeout"):
        await timeout_computer.run_bash_command("sleep 20", timeout=0.05)
    assert not timeout_computer._foreground_processes

    cancelled_computer = MacOSComputer(presentation=False, allow_local_shell=True)
    command = asyncio.create_task(cancelled_computer.run_bash_command("sleep 20"))
    while not cancelled_computer._foreground_processes:
        await asyncio.sleep(0)
    cancelled_computer.cancellation.request("operator_stop")
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await command
    assert cancelled_computer.cancellation.cause == "operator_stop"
    assert not cancelled_computer._foreground_processes

    outer_cancelled_computer = MacOSComputer(presentation=False, allow_local_shell=True)
    outer_cancelled = asyncio.create_task(outer_cancelled_computer.run_bash_command("sleep 20"))
    while not outer_cancelled_computer._foreground_processes:
        await asyncio.sleep(0)
    process = next(iter(outer_cancelled_computer._foreground_processes))
    outer_cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer_cancelled
    assert process.returncode is not None
    assert not outer_cancelled_computer._foreground_processes


async def test_shell_command_text_is_absent_from_process_arguments():
    secret = "process-argument-secret-93f82"
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    command = asyncio.create_task(computer.run_shell_command(f"value={secret}; sleep 20"))
    while not computer._foreground_processes:
        await asyncio.sleep(0)
    process = next(iter(computer._foreground_processes))
    group = os.getpgid(process.pid)
    listing = subprocess.run(
        ["/bin/ps", "-axo", "pgid=,command="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    group_commands = "\n".join(
        line
        for line in listing.splitlines()
        if line.strip() and line.strip().split(maxsplit=1)[0] == str(group)
    )
    assert secret not in group_commands
    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command


async def test_background_supervisor_retains_group_ownership_until_teardown():
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    result = await computer.run_bash_command("printf finished", run_in_background=True)
    background = next(iter(computer._background.values()))
    for _ in range(100):
        if background.terminal_state is not None:
            break
        await asyncio.sleep(0.02)

    assert background.terminal_state == "completed"
    assert background.process.returncode is None
    assert os.getpgid(background.process.pid) == background.identity.group
    assert background.task_id in result
    assert [event.state for event in computer.shell_events] == ["starting", "running", "completed"]
    output_path = background.output_path
    status_path = background.status_path
    await computer.aclose()
    assert background.process.returncode is not None
    assert not status_path.exists()
    assert output_path.read_text(encoding="utf-8") == "finished"
    output_path.unlink()


async def test_background_command_text_is_absent_from_long_lived_process_arguments():
    secret = "background-process-secret-2ea11"
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    await computer.run_bash_command(f"value={secret}; sleep 20", run_in_background=True)
    background = next(iter(computer._background.values()))
    listing = subprocess.run(
        ["/bin/ps", "-axo", "pgid=,command="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    group_commands = "\n".join(
        line
        for line in listing.splitlines()
        if line.strip() and line.strip().split(maxsplit=1)[0] == str(background.identity.group)
    )
    assert secret not in group_commands
    output_path = background.output_path
    await computer.aclose()
    output_path.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_background_group_dies_if_the_owning_python_process_is_killed():
    script = """
import asyncio
from yutori.navigator.macos import MacOSComputer

async def main():
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    await computer.run_bash_command("sleep 60", run_in_background=True)
    background = next(iter(computer._background.values()))
    print(background.process.pid, flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert owner.stdout is not None
    supervisor_pid = int(owner.stdout.readline().strip())
    try:
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(supervisor_pid, 0)
            except ProcessLookupError:
                break
            process_state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(supervisor_pid)],
                capture_output=True,
                check=False,
                text=True,
            ).stdout.strip()
            if not process_state or process_state.startswith("Z"):
                break
            time.sleep(0.05)
        else:
            pytest.fail("detached background supervisor survived its owning Python process")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        try:
            os.killpg(supervisor_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _frame(capture_id: int = 1) -> N2Observation:
    return N2Observation(
        capture_id=capture_id,
        native_width=100,
        native_height=100,
        encoded_width=100,
        encoded_height=100,
        media_type="image/webp",
        encoded_bytes=b"frame-bytes",
    )


def _poll_result(*, outcome: str, last_frame: object, waited_ms: int = 0, capture_ms: int = 0) -> FramePollResult:
    return FramePollResult(
        outcome=outcome,
        waited_ms=waited_ms,
        polls=1,
        capture_ms=capture_ms,
        last_frame=last_frame,
        changed_fraction=None,
    )


async def test_wait_for_change_banks_polling_time_and_returns_the_changed_frame(monkeypatch):
    computer = MacOSComputer(FakeTransport(), owns_transport=False, presentation=False)
    changed_frame = _frame(2)

    async def fake_poll(**kwargs: Any) -> FramePollResult:
        return _poll_result(outcome="changed", last_frame=changed_frame, waited_ms=900, capture_ms=300)

    monkeypatch.setattr(computer_module, "poll_until_frame_changes", fake_poll)

    result = await computer.wait_for_change(500, _frame(1))

    assert result is changed_frame
    assert computer.timings["polling_ms"] == 600


async def test_wait_for_change_falls_back_to_a_fresh_screenshot_when_the_poll_yields_no_frame(monkeypatch):
    computer = MacOSComputer(FakeTransport(), owns_transport=False, presentation=False)
    fresh_frame = _frame(3)

    async def fake_poll(**kwargs: Any) -> FramePollResult:
        return _poll_result(outcome="undiffable", last_frame=None)

    async def fake_screenshot() -> N2Observation:
        return fresh_frame

    monkeypatch.setattr(computer_module, "poll_until_frame_changes", fake_poll)
    monkeypatch.setattr(computer, "screenshot", fake_screenshot)

    assert await computer.wait_for_change(500, _frame(1)) is fresh_frame


async def test_poll_after_action_returns_the_reference_first_frame_when_the_poll_yields_no_frame(monkeypatch):
    computer = MacOSComputer(FakeTransport(), owns_transport=False, presentation=False)
    first_frame = _frame(4)

    async def fake_poll(**kwargs: Any) -> FramePollResult:
        return _poll_result(outcome="exhausted", last_frame=None)

    async def unexpected_screenshot() -> N2Observation:
        raise AssertionError("poll_after_action must not take a fresh screenshot of its own")

    monkeypatch.setattr(computer_module, "poll_until_frame_changes", fake_poll)
    monkeypatch.setattr(computer, "screenshot", unexpected_screenshot)

    result = await computer.poll_after_action("left_click", _frame(1), first_frame)

    assert result is first_frame


async def test_wait_for_change_raises_on_an_aborted_poll(monkeypatch):
    computer = MacOSComputer(FakeTransport(), owns_transport=False, presentation=False)

    async def fake_poll(**kwargs: Any) -> FramePollResult:
        return _poll_result(outcome="aborted", last_frame=None)

    monkeypatch.setattr(computer_module, "poll_until_frame_changes", fake_poll)
    computer.cancellation.request("operator_stop")
    await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError, match="operator_stop"):
        await computer.wait_for_change(500, _frame(1))


def _frontmost_probe(sequence: list[FrontmostApp | None]):
    calls = {"count": 0}

    async def probe() -> FrontmostApp | None:
        calls["count"] += 1
        return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


async def test_keyboard_delivery_proceeds_while_the_frontmost_app_is_unchanged():
    transport = FakeTransport()
    probe = _frontmost_probe([FrontmostApp(10, "Calculator")])
    async with MacOSComputer(transport, owns_transport=False, presentation=False, frontmost_probe=probe) as computer:
        await computer.screenshot()
        await computer.type("9*9=")
        await computer.keypress("Return")
        await computer.keypress(["cmd", "c"])
    sent = [call[0] for call in transport.calls]
    assert sent.count("type_text") == 1 and sent.count("press_key") == 1 and sent.count("hotkey") == 1
    assert computer.focus_guard_trips == 0
    # One probe per screenshot plus one per keyboard action.
    assert probe.calls["count"] == 4


async def test_keystrokes_are_withheld_when_focus_moved_since_the_last_frame():
    transport = FakeTransport([_png(100, 50), _png(200, 80)])
    probe = _frontmost_probe([FrontmostApp(10, "Calculator"), FrontmostApp(20, "Slack")])
    async with MacOSComputer(transport, owns_transport=False, presentation=False, frontmost_probe=probe) as computer:
        await computer.screenshot()
        captures_before = [call[0] for call in transport.calls].count("get_desktop_state")
        with pytest.raises(MacOSFocusChangedError) as raised:
            await computer.type("hello")
        error = raised.value
        assert error.recoverable
        assert "Calculator (pid 10)" in str(error) and "Slack (pid 20)" in str(error)
        assert isinstance(error.observation, N2Observation)
        assert (error.observation.native_width, error.observation.native_height) == (200, 80)
        assert computer.focus_guard_trips == 1
        # The refusal captured exactly one fresh frame for the model.
        assert [call[0] for call in transport.calls].count("get_desktop_state") == captures_before + 1
        # That frame re-anchored the guard on Slack, so a retry against it goes through.
        await computer.type("hello")
    assert [call[0] for call in transport.calls].count("type_text") == 1


async def test_focus_guard_fails_open_when_the_probe_cannot_answer():
    transport = FakeTransport()
    probe = _frontmost_probe([None])
    async with MacOSComputer(transport, owns_transport=False, presentation=False, frontmost_probe=probe) as computer:
        await computer.screenshot()
        await computer.keypress("Return")
    assert [call[0] for call in transport.calls].count("press_key") == 1


async def test_focus_guard_fails_open_when_the_probe_raises():
    transport = FakeTransport()

    async def probe() -> FrontmostApp | None:
        raise OSError("lsappinfo missing")

    async with MacOSComputer(transport, owns_transport=False, presentation=False, frontmost_probe=probe) as computer:
        await computer.screenshot()
        await computer.type("x")
    assert [call[0] for call in transport.calls].count("type_text") == 1


async def test_focus_guard_can_be_disabled():
    transport = FakeTransport()
    probe = _frontmost_probe([FrontmostApp(10, "Calculator"), FrontmostApp(20, "Slack")])
    async with MacOSComputer(
        transport, owns_transport=False, presentation=False, verify_focus=False, frontmost_probe=probe
    ) as computer:
        await computer.screenshot()
        await computer.type("x")
    assert probe.calls["count"] == 0
    assert [call[0] for call in transport.calls].count("type_text") == 1


async def test_pointer_actions_do_not_consult_the_focus_guard():
    transport = FakeTransport()
    probe = _frontmost_probe([FrontmostApp(10, "Calculator"), FrontmostApp(20, "Slack")])
    async with MacOSComputer(transport, owns_transport=False, presentation=False, frontmost_probe=probe) as computer:
        await computer.screenshot()
        await computer.click(5, 5)
    assert probe.calls["count"] == 1
    assert [call[0] for call in transport.calls].count("click") == 1
