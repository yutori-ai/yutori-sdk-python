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
    MacOSBackgroundDeliveryError,
    MacOSComputer,
    MacOSFocusChangedError,
    MacOSRecoverableActionError,
    MacOSTargetCrashedError,
    MacOSTargetWindowChangedError,
    MacOSUncertainActionError,
)
from yutori.navigator.macos.frontmost import FrontmostApp
from yutori.navigator.macos.polling import FramePollResult
from yutori.navigator.macos.transport import CuaDriverToolError, CuaDriverUncertainActionError
from yutori.navigator.macos.types import (
    MacOSAppState,
    MacOSPresentationStatus,
    MacOSStatusMetrics,
    MacOSWindowTarget,
    N2Observation,
)


def _png(width: int = 2560, height: int = 1600, color: tuple[int, int, int] = (15, 25, 35)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
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

    def blocking_surface(self, _point: tuple[float, float]) -> "str | None":
        return None


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


async def test_restore_native_cursor_falls_back_to_cursorless_like_select_does():
    class CursorlessTransport(FakeTransport):
        async def call_tool(self, name, arguments, **kwargs):
            if name == "set_agent_cursor_enabled":
                raise RuntimeError("cursor unavailable")
            return await super().call_tool(name, arguments, **kwargs)

    computer = MacOSComputer(CursorlessTransport(), owns_transport=False, presentation=False)
    computer._native_cursor = "yutori.default"
    assert await computer._restore_native_cursor() == "cursorless"


async def test_stop_region_refuses_click_drag_and_anchored_scroll_before_driver_input():
    transport = FakeTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    computer._native_size = (1000, 1000)
    sink = PresentationSink()
    sink.blocking_surface = lambda point: "stop" if point[0] >= 900 else None
    computer.presentation = sink

    with pytest.raises(MacOSActionRefusedError, match="Stop control"):
        await computer.click(950, 100)
    with pytest.raises(MacOSActionRefusedError, match="Stop control"):
        await computer.drag([{"x": 10, "y": 10}, {"x": 950, "y": 100}])
    with pytest.raises(MacOSActionRefusedError, match="Stop control"):
        await computer.scroll(950, 100, 0, 100)
    assert not [call for call in transport.calls if call[0] in {"click", "drag", "scroll"}]


async def test_activity_grip_refuses_model_input_with_its_own_reason():
    """The activity window's grip swallows clicks the way the Stop item does; the message says which."""
    transport = FakeTransport()
    computer = MacOSComputer(transport, owns_transport=False, presentation=False)
    computer._native_size = (1000, 1000)
    sink = PresentationSink()
    sink.blocking_surface = lambda point: "activity" if point[0] < 100 else None
    computer.presentation = sink

    with pytest.raises(MacOSActionRefusedError, match="activity window's grip"):
        await computer.click(50, 50)
    await computer.click(500, 500)
    assert [call[0] for call in transport.calls if call[0] == "click"] == ["click"]


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


async def test_release_held_mouse_button_only_releases_when_held():
    # release_held_mouse_button is inherited from sandbox_tools.PointerKeyLifecycleMixin
    # rather than redefined on MacOSComputer; pin its behavior here.
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        await computer.release_held_mouse_button()
        assert not [call for call in transport.calls if call[0] in {"click", "drag"}]

        await computer.move(10, 20)
        await computer.left_mouse_down()
        await computer.release_held_mouse_button()
        assert not computer._left_mouse_down

    click = next(call for call in transport.calls if call[0] == "click")
    assert click[1]["x"] == 10 and click[1]["y"] == 20


@pytest.mark.parametrize("method_name", ["left_mouse_down", "left_mouse_up"])
async def test_mouse_down_and_up_reject_partial_coordinates(method_name: str):
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        method = getattr(computer, method_name)
        with pytest.raises(ValueError, match=f"{method_name.split('_', 1)[1]} coordinates must include both x and y"):
            await method(x=10)
        with pytest.raises(ValueError, match=f"{method_name.split('_', 1)[1]} coordinates must include both x and y"):
            await method(y=10)


async def test_left_mouse_down_without_a_prior_pointer_position_is_recoverable():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        with pytest.raises(MacOSRecoverableActionError, match="requires coordinates after the pointer has moved"):
            await computer.left_mouse_down()


async def test_left_mouse_up_without_a_preceding_mouse_down_is_recoverable():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False) as computer:
        await computer.move(10, 20)
        with pytest.raises(MacOSRecoverableActionError, match="requires a preceding mouse_down"):
            await computer.left_mouse_up()


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


async def test_foreground_shell_presents_a_redacted_output_tail(tmp_path):
    """Output now reaches presentation -- bounded and redacted, never raw.

    This deliberately reverses the older "no output to presentation" contract, so
    an operator can watch a slow command print instead of a spinner. What has NOT
    changed is the redaction: the secret in the command line must stay out of every
    event, and it is the output side that matters most here, since a command's argv
    is chosen by the model while its output is whatever the machine printed.
    """
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
    # Two `running` events: the lifecycle one at launch, then the first output
    # chunk. The first chunk is deliberately not throttled -- a command that prints
    # once and exits should still show what it printed.
    assert [event.state for event in shell_events] == ["starting", "running", "running", "completed"]
    assert all("topsecret" not in repr(event) for event in shell_events)
    assert all("[REDACTED]" in event.command for event in shell_events)
    assert [event.output for event in shell_events[:2]] == [None, None]
    assert shell_events[2].output == os.path.expanduser("~")
    assert shell_events[-1].output == os.path.expanduser("~")


async def test_presented_output_is_bounded_to_the_cards_last_lines():
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    sink = PresentationSink()
    computer.presentation = sink
    await computer.run_shell_command("printf 'a\\nb\\nc\\nd\\ne\\nf\\n'", timeout_seconds=5)
    computer.presentation = None

    # The END, not the beginning: the card shows a few lines, and a feed truncated
    # at the head would freeze on the first thing printed.
    assert sink.events[-1]["event"].output == "…c\nd\ne\nf"


async def test_slow_command_streams_its_output_before_it_exits():
    """The whole point: the card updates while the command is still running.

    A command that prints, waits, prints, waits, then exits must produce output
    events BEFORE its terminal one -- and each must carry the tail so far, not a
    delta, so a dropped frame costs nothing.
    """
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    sink = PresentationSink()
    computer.presentation = sink
    await computer.run_bash_command("printf 'first\\n'; sleep 0.6; printf 'second\\n'; sleep 0.6", timeout=10)
    computer.presentation = None

    events = [event["event"] for event in sink.events]
    running_output = [event.output for event in events if event.state == "running" and event.output]
    assert running_output, "no output reached the card while the command was running"
    assert running_output[0] == "first"
    # Cumulative, not a delta.
    assert running_output[-1].endswith("second")
    # And every one of them landed before the command reported its exit.
    assert events[-1].state == "completed"
    assert all(event.output is not None for event in events[-1:])


async def test_timeout_does_not_resurrect_the_card_as_running():
    """A pending output frame must never land after the terminal event.

    `except` runs before `finally`, so closing the output stream only in `finally`
    let a queued `running` frame flush AFTER the card was marked timed out --
    putting it back into its running state for the whole finished-card dwell.

    The sink is deliberately SLOW: the race needs a frame still in flight when the
    timeout fires, which a sink that returns immediately never leaves behind.
    """

    class SlowSink(PresentationSink):
        async def present(self, event: dict[str, Any]) -> None:
            if event.get("type") == "shell" and event["event"].output:
                await asyncio.sleep(2.0)
            await super().present(event)

    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    sink = SlowSink()
    computer.presentation = sink
    with pytest.raises(TimeoutError):
        await computer.run_bash_command("printf 'working\\n'; sleep 20", timeout=0.5)
    computer.presentation = None

    states = [event["event"].state for event in sink.events]
    assert states[-1] == "timed_out", states


async def test_presented_output_never_leaks_the_bash_cwd_sentinel():
    computer = MacOSComputer(presentation=False, allow_local_shell=True)
    sink = PresentationSink()
    computer.presentation = sink
    await computer.run_bash_command("printf 'done\\n'", timeout=5)
    computer.presentation = None

    assert await computer.run_bash_command("pwd", timeout=5)
    for event in sink.events:
        assert "__YUTORI_N2_BASH_CWD_" not in (event["event"].output or "")


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

    # Mirrors the asyncio subprocess surface the shell pump actually drives -- a
    # stdin writer and an incrementally-read stdout -- rather than `communicate`,
    # which the pump replaced so a running command's output can reach the card.
    class Stdin:
        def write(self, data):
            captured.append((argv, data))

        async def drain(self):
            return None

        def close(self):
            return None

    class Stdout:
        def __init__(self):
            self._remaining = b"launched"

        async def read(self, _limit):
            chunk, self._remaining = self._remaining, b""
            return chunk

    class Process:
        pid = 123
        returncode = 0

        def __init__(self):
            self.stdin = Stdin()
            self.stdout = Stdout()

        async def wait(self):
            return self.returncode

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
        line for line in listing.splitlines() if line.strip() and line.strip().split(maxsplit=1)[0] == str(group)
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


# --- window scope -------------------------------------------------------------------------

# The adapter checks target liveness with os.kill(pid, 0); our own pid is the one process
# every test run can rely on being alive.
PID = os.getpid()
DEAD_PID = 2**22 - 7


def _window_record(window_id: int = 7, pid: int = PID, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "window_id": window_id,
        "pid": pid,
        "app_name": "Calculator",
        "title": "Calculator",
        "bounds": {"x": 40, "y": 60, "width": 400, "height": 300},
        "z_index": 5,
        "is_on_screen": True,
        "on_current_space": True,
    }
    record.update(overrides)
    return record


class WindowFakeTransport(FakeTransport):
    """FakeTransport that also serves window captures, window lists, and scripted action envelopes."""

    def __init__(self, window_frames: list[bytes] | None = None, windows: list[dict[str, Any]] | None = None):
        super().__init__()
        self.window_frames = list(window_frames or [_png(400, 300)])
        self.windows = list(windows if windows is not None else [_window_record()])
        self.action_results: dict[str, list[dict[str, Any]]] = {}
        self.tool_errors: dict[str, list[Exception]] = {}
        # Extra structuredContent merged into get_window_state responses, keyed by window_id.
        self.window_state_extra: dict[int, dict[str, Any]] = {}
        # Scripted human-readable result lines (the driver's text content part), keyed by tool.
        self.result_text: dict[str, list[str]] = {}

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
        queued_errors = self.tool_errors.get(name)
        if queued_errors:
            raise queued_errors.pop(0)
        if name == "get_window_state":
            frame = self.window_frames.pop(0) if len(self.window_frames) > 1 else self.window_frames[0]
            with Image.open(io.BytesIO(frame)) as image:
                width, height = image.size
            extra = self.window_state_extra.get(arguments.get("window_id"), {})
            return {
                "content": [{"type": "image", "data": base64.b64encode(frame).decode("ascii")}],
                "structuredContent": {"screenshot_width": width, "screenshot_height": height, "elements": [], **extra},
            }
        if name == "list_windows":
            pid = arguments.get("pid")
            return {"structuredContent": {"windows": [w for w in self.windows if pid in (None, w["pid"])]}}
        if name in {"get_app_state", "invoke_app_menu", "invoke_menu"}:
            raise AssertionError(f"App/menu support must use existing background APIs, not {name}")
        scripted = self.action_results.get(name)
        if scripted:
            result: dict[str, Any] = {"structuredContent": scripted.pop(0)}
            queued_text = self.result_text.get(name)
            if queued_text:
                result["content"] = [{"type": "text", "text": queued_text.pop(0)}]
            return result
        if name in {"click", "type_text", "press_key", "hotkey", "scroll", "drag", "move_cursor"}:
            return {
                "structuredContent": {
                    "effect": "unverifiable",
                    "route": "synthetic_events",
                    "delivery": {"mode": arguments.get("delivery_mode")},
                }
            }
        return {"structuredContent": {"ok": True}}


def _window_computer(transport: FakeTransport, **kwargs: Any) -> MacOSComputer:
    return MacOSComputer(transport, owns_transport=False, presentation=False, scope="window", **kwargs)


def _bound_window_computer(transport: FakeTransport, **kwargs: Any) -> MacOSComputer:
    target = MacOSWindowTarget(PID, 7, title="Calculator", app_name="Calculator")
    return _window_computer(transport, target_window=target, **kwargs)


def _tool_error(code: str) -> CuaDriverToolError:
    return CuaDriverToolError(f"Cua Driver failed: {code}", structured={"code": code, "effect": "refused"})


def _names(transport: FakeTransport) -> list[str]:
    return [call[0] for call in transport.calls]


def _arguments(transport: FakeTransport, name: str) -> list[dict[str, Any]]:
    return [arguments for called, arguments, _ in transport.calls if called == name]


async def _no_wait(_seconds: float) -> None:
    return None


async def test_desktop_scope_wire_arguments_are_unchanged():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False, verify_focus=False) as computer:
        await computer.click(10, 20)
        await computer.scroll(10, 10, 0, 300)
        await computer.type("hi")
        await computer.keypress("Return")
        await computer.keypress(["cmd", "c"])
        await computer.drag([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
        await computer.move(5, 6)
    session = computer.session
    by_name = {name: arguments for name, arguments, _ in transport.calls}
    assert by_name["start_session"] == {"session": session, "capture_scope": "desktop"}
    base = {"session": session, "scope": "desktop", "delivery_mode": "foreground"}
    assert by_name["click"] == {**base, "x": 10, "y": 20, "button": "left", "count": 1}
    assert by_name["scroll"] == {**base, "x": 10, "y": 10, "direction": "down", "amount": 2, "by": "line"}
    assert by_name["type_text"] == {**base, "text": "hi", "delay_ms": 0}
    assert by_name["press_key"] == {**base, "key": "Return"}
    assert by_name["hotkey"] == {**base, "keys": ["cmd", "c"]}
    assert by_name["drag"] == {**base, "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}
    assert by_name["move_cursor"] == {**base, "x": 5, "y": 6}
    assert not computer.window_mode and computer.target_window is None
    assert computer.action_outcomes == ()
    assert computer.delivery_counts["background_attempts"] == 0


def test_window_scope_constructor_validation():
    with pytest.raises(ValueError, match="target_window requires"):
        MacOSComputer(
            FakeTransport(), owns_transport=False, presentation=False, target_window=MacOSWindowTarget(PID, 7)
        )
    with pytest.raises(ValueError, match="scope must be"):
        MacOSComputer(FakeTransport(), owns_transport=False, presentation=False, scope="screen")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="background_focus_overlay requires"):
        MacOSComputer(FakeTransport(), owns_transport=False, background_focus_overlay=True)
    computer = _bound_window_computer(FakeTransport())
    assert computer.window_mode and computer.target_pid == PID


async def test_window_scope_captures_only_the_target_window():
    transport = WindowFakeTransport([_png(400, 300)])
    async with _window_computer(transport) as computer:
        assert computer.target_window is None
        with pytest.raises(computer_module.MacOSComputerError, match="set_window_target"):
            await computer.screenshot()
        await computer.set_window_target(MacOSWindowTarget(PID, 7, title="Calculator", app_name="Calculator"))
        assert computer.target_pid == PID
        observation = await computer.screenshot()
        assert (observation.native_width, observation.native_height) == (400, 300)
        assert computer.window_target_info == {
            "pid": PID,
            "window_id": 7,
            "title": "Calculator",
            "app_name": "Calculator",
            "capture_width": 400,
            "capture_height": 300,
        }
    assert "get_desktop_state" not in _names(transport)
    assert _arguments(transport, "start_session") == [{"session": computer.session}]
    assert _arguments(transport, "get_window_state") == [
        {
            "session": computer.session,
            "pid": PID,
            "window_id": 7,
            "include_screenshot": True,
            "max_elements": 1,
            "max_depth": 1,
        }
    ]


class _FakeStatusController:
    instances: list[_FakeStatusController] = []
    fail_start = False

    def __init__(self, **kwargs: Any):
        self.kwargs = kwargs
        self.thumbnails: list[tuple[bytes, str | None]] = []
        self.previews: list[bytes] = []
        self.on_preview_demand = None
        self.events: list[dict[str, Any]] = []
        self.targets: list[MacOSWindowTarget | None] = []
        self.metrics: list[MacOSStatusMetrics] = []
        self.stopped = False
        self.status = MacOSPresentationStatus(True, True, "active", "hidden")
        self.telemetry: tuple[dict[str, Any], ...] = ()
        self.__class__.instances.append(self)

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("no status item")

    async def reveal(self) -> None:
        pass

    async def set_window_target(self, target: MacOSWindowTarget | None) -> bool:
        self.targets.append(target)
        return True

    async def show_thumbnail(self, image_bytes: bytes, *, caption: str | None = None) -> bool:
        self.thumbnails.append((image_bytes, caption))
        return True

    async def show_preview_frame(self, image_bytes: bytes) -> bool:
        self.previews.append(image_bytes)
        return True

    async def present(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def update_status_metrics(self, metrics: MacOSStatusMetrics) -> bool:
        self.metrics.append(metrics)
        return True

    async def before_capture(self, _capture_id: int) -> bool:
        return False

    async def after_capture(self, *_args: Any) -> bool:
        return False

    async def encode_observation(self, _png: bytes) -> None:
        return None

    def blocking_surface(self, _point: tuple[float, float]) -> "str | None":
        return None

    async def stop(self) -> None:
        self.stopped = True


async def test_window_scope_shows_a_menu_bar_status_item_with_the_latest_frame(monkeypatch):
    _FakeStatusController.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeStatusController)
    transport = WindowFakeTransport([_png(400, 300)])
    async with MacOSComputer(transport, owns_transport=False, presentation=True, scope="window") as computer:
        (controller,) = _FakeStatusController.instances
        assert computer.presentation is controller
        assert controller.kwargs["mode"] == "status" and controller.kwargs["title"]
        assert controller.kwargs["show_stop_button"] is True
        assert controller.kwargs["background_focus_overlay"] is False
        assert controller.kwargs["show_status_item"] is True
        await computer.set_window_target(MacOSWindowTarget(PID, 7, title="Calculator", app_name="Calculator"))
        assert controller.targets == []
        assert controller.events == [{"type": "status", "text": f"Driving Calculator (pid {PID}, window 7)"}]
        observation = await computer.screenshot()
        assert observation.native_width == 400
        ((thumbnail, caption),) = controller.thumbnails
        with Image.open(io.BytesIO(thumbnail)) as image:
            assert image.format == "JPEG" and image.size == (400, 300)
        assert caption == f"Frame 1 of Calculator (pid {PID}, window 7)"
        metrics = MacOSStatusMetrics(input_tokens=10, request_in_flight=True)
        assert await computer.update_status_metrics(metrics) is True
        assert controller.metrics == [metrics]
        assert computer.presentation_status.state == "active" and computer.presentation_status.cursor == "hidden"
    assert controller.stopped
    assert _arguments(transport, "set_agent_cursor_enabled") == [{"session": computer.session, "enabled": False}]
    assert "set_agent_cursor_theme" not in _names(transport)
    assert "get_desktop_state" not in _names(transport)


async def test_window_scope_can_embed_only_the_focus_overlay(monkeypatch):
    _FakeStatusController.instances.clear()
    _FakeStreamer.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeStatusController)
    monkeypatch.setattr(computer_module, "WindowPreviewStreamer", _FakeStreamer)
    transport = WindowFakeTransport([_png(400, 300)])
    target = MacOSWindowTarget(PID, 7, title="Calculator", app_name="Calculator")
    replacement = MacOSWindowTarget(PID + 1, 8, title="Notes", app_name="Notes")
    async with MacOSComputer(
        transport,
        owns_transport=False,
        presentation=True,
        scope="window",
        background_focus_overlay=True,
        show_status_item=False,
        show_stop_button=False,
    ) as computer:
        (controller,) = _FakeStatusController.instances
        assert controller.kwargs["background_focus_overlay"] is True
        assert controller.kwargs["show_status_item"] is False
        assert controller.kwargs["show_stop_button"] is False
        assert controller.targets == [None]
        await computer.set_window_target(target)
        await computer.screenshot()
        await computer.set_window_target(replacement)
        await computer.set_window_target(None)
        assert controller.targets == [None, target, replacement, None]
        assert controller.thumbnails == []
        assert computer.preview_frames_sent == 0
    assert not _FakeStreamer.instances


async def test_status_metrics_are_a_noop_without_presentation():
    computer = MacOSComputer(FakeTransport(), owns_transport=False, presentation=False)
    assert await computer.update_status_metrics(MacOSStatusMetrics()) is False


async def test_window_scope_status_item_failure_is_fail_soft(monkeypatch):
    _FakeStatusController.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeStatusController)
    monkeypatch.setattr(_FakeStatusController, "fail_start", True)
    transport = WindowFakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=True, scope="window") as computer:
        assert computer.presentation is None
        status = computer.presentation_status
        assert status.requested and not status.available
        assert status.degradation_reason == "status_item_start_failed:RuntimeError"
        assert status.cursor == "hidden"
    assert _FakeStatusController.instances[0].stopped


async def test_window_scope_without_presentation_reports_window_mode(monkeypatch):
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeStatusController)
    _FakeStatusController.instances.clear()
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        assert computer.presentation is None
        assert computer.presentation_status.degradation_reason is None  # not requested
    assert not _FakeStatusController.instances


async def test_window_scope_actions_carry_the_window_target_and_background_delivery():
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(10, 20)
        await computer.scroll(10, 10, 0, 60)
        await computer.type("9*9=")
        await computer.keypress("Return")
        await computer.keypress(["cmd", "c"])
        await computer.drag([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
    tools = ["click", "scroll", "type_text", "press_key", "hotkey", "drag"]
    actions = [(name, arguments) for name, arguments, _ in transport.calls if name in tools]
    assert [name for name, _ in actions] == tools
    for _, arguments in actions:
        assert arguments["target"] == {"kind": "window", "pid": PID, "window_id": 7}
        # The driver refuses `target` alongside the legacy scope/pid/window_id fields.
        assert not {"scope", "pid", "window_id"} & arguments.keys()
        assert arguments["delivery_mode"] == "background"
    assert actions[1][1]["amount"] == 2  # 60px against a 300px-tall window: two lines
    assert computer.delivery_counts == {
        "background_attempts": 6,
        "foreground_escalations": 0,
        "fallback_skips": 0,
        "accessibility_rungs": 0,
        "background_refusals": 0,
        "window_rebinds": 0,
    }
    assert len(computer.action_outcomes) == 6
    assert computer.last_action_outcome is not None
    assert (computer.last_action_outcome.tool, computer.last_action_outcome.effect) == ("drag", "unverifiable")
    assert computer.last_action_outcome.reported_delivery == "background"


async def test_window_scope_move_records_the_pointer_without_driver_input():
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport) as computer:
        await computer.move(30, 40)
        await computer.left_mouse_down()
        await computer.move(50, 60)
        await computer.left_mouse_up()
    assert "move_cursor" not in _names(transport)
    (drag,) = _arguments(transport, "drag")
    assert (drag["from_x"], drag["from_y"], drag["to_x"], drag["to_y"]) == (30, 40, 50, 60)
    assert drag["delivery_mode"] == "background"


async def test_window_scope_skips_the_frontmost_probe_and_focus_guard():
    transport = WindowFakeTransport()
    probe = _frontmost_probe([FrontmostApp(10, "Calculator"), FrontmostApp(20, "Slack")])
    async with _bound_window_computer(transport, frontmost_probe=probe) as computer:
        await computer.screenshot()
        await computer.type("x")
        await computer.keypress("Return")
    assert probe.calls["count"] == 0
    assert _names(transport).count("type_text") == 1 and _names(transport).count("press_key") == 1
    assert computer.focus_guard_trips == 0


async def test_background_action_that_did_not_land_is_refused_with_a_fresh_frame():
    transport = WindowFakeTransport([_png(400, 300), _png(420, 310)])
    transport.action_results["click"] = [
        {
            "effect": "suspected_noop",
            "route": "synthetic_events",
            "escalation": {"recommended": "foreground", "reason": "renderer never took focus"},
        }
    ]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSBackgroundDeliveryError) as raised:
            await computer.click(10, 20)
        error = raised.value
        assert error.recoverable
        assert "did not land" in str(error)
        assert "effect=suspected_noop" in str(error) and "recommended=foreground" in str(error)
        assert isinstance(error.observation, N2Observation)
        assert (error.observation.native_width, error.observation.native_height) == (420, 310)
        assert error.outcome is not None
        assert (error.outcome.effect, error.outcome.recommended, error.outcome.escalated) == (
            "suspected_noop",
            "foreground",
            False,
        )
        assert computer.delivery_counts["background_refusals"] == 1
        assert computer.delivery_counts["foreground_escalations"] == 0
    assert _names(transport).count("click") == 1


async def test_foreground_fallback_retries_once_when_the_driver_recommends_it():
    transport = WindowFakeTransport()
    transport.action_results["type_text"] = [
        {"effect": "suspected_noop", "escalation": {"recommended": "foreground"}},
        {"effect": "confirmed", "route": "trusted_input", "delivery": {"mode": "foreground"}},
    ]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.type("hello")
    sends = _arguments(transport, "type_text")
    assert [send["delivery_mode"] for send in sends] == ["background", "foreground"]
    assert sends[1]["text"] == "hello" and sends[1]["target"] == sends[0]["target"]
    assert computer.delivery_counts["foreground_escalations"] == 1
    assert computer.delivery_counts["background_refusals"] == 0
    outcome = computer.last_action_outcome
    assert outcome is not None and outcome.escalated
    assert (outcome.requested_delivery, outcome.reported_delivery, outcome.effect) == (
        "foreground",
        "foreground",
        "confirmed",
    )


@pytest.mark.parametrize(
    "structured",
    [
        {"effect": "unverifiable"},
        {"effect": "partial"},
        {"effect": "confirmed", "route": "accessibility"},
        {"effect": "unverifiable", "escalation": {"recommended": "px"}},
        {"effect": "unverifiable", "escalation": {"recommended": "foreground"}},
        {"effect": "partial", "escalation": {"target": "foreground", "reason": "delivery_failed"}},
        {"effect": "confirmed", "escalation": {"target": "foreground", "reason": "delivery_failed"}},
        {},
    ],
)
async def test_landed_effects_never_escalate(structured):
    transport = WindowFakeTransport()
    transport.action_results["click"] = [structured]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.click(1, 1)
    assert _names(transport).count("click") == 1
    assert computer.delivery_counts["foreground_escalations"] == 0
    assert computer.last_action_outcome is not None and computer.last_action_outcome.landed


@pytest.mark.parametrize("allow_foreground_fallback", [False, True])
@pytest.mark.parametrize("advice_key", ["recommended", "target"])
async def test_background_shortcut_advice_does_not_abort_or_repeat_a_sequence(allow_foreground_fallback, advice_key):
    transport = WindowFakeTransport()
    transport.action_results["hotkey"] = [
        {"effect": "unverifiable", "escalation": {advice_key: "foreground", "reason": "delivery_failed"}}
        for _ in range(3)
    ]
    async with _bound_window_computer(transport, allow_foreground_fallback=allow_foreground_fallback) as computer:
        await computer.screenshot()
        for _ in range(3):
            await computer.keypress(["cmd", "plus"])
    sends = _arguments(transport, "hotkey")
    assert len(sends) == 3
    assert all(send["delivery_mode"] == "background" for send in sends)
    assert computer.delivery_counts["background_refusals"] == 0
    assert computer.delivery_counts["foreground_escalations"] == 0
    # An unchanged screenshot cannot prove failure either: accepted input is left to visual verification.
    assert len(_arguments(transport, "get_window_state")) == 1


@pytest.mark.parametrize("allow_foreground_fallback", [False, True])
async def test_unverified_typing_is_not_replayed_when_the_driver_suggests_foreground(allow_foreground_fallback):
    transport = WindowFakeTransport()
    transport.action_results["type_text"] = [
        {"effect": "unverifiable", "escalation": {"target": "foreground", "reason": "delivery_failed"}},
    ]
    async with _bound_window_computer(transport, allow_foreground_fallback=allow_foreground_fallback) as computer:
        await computer.type("15*15")
    assert len(_arguments(transport, "type_text")) == 1
    assert computer.delivery_counts["foreground_escalations"] == 0


async def test_foreground_fallback_is_skipped_when_the_window_already_changed():
    # Frame 1 is what the model reasoned over; frame 2 (a different image) is captured after the
    # background attempt that the driver reported as not landing.
    transport = WindowFakeTransport([_png(400, 300), _png(400, 300, color=(240, 240, 240))])
    transport.action_results["type_text"] = [{"effect": "suspected_noop", "escalation": {"target": "foreground"}}]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.screenshot()
        await computer.type("15*15")
    assert [send["delivery_mode"] for send in _arguments(transport, "type_text")] == ["background"]
    assert computer.delivery_counts["fallback_skips"] == 1
    assert computer.delivery_counts["foreground_escalations"] == 0
    assert computer.delivery_counts["background_refusals"] == 0


async def test_foreground_fallback_proceeds_when_the_window_did_not_change():
    transport = WindowFakeTransport([_png(400, 300)])
    transport.action_results["type_text"] = [
        {"effect": "suspected_noop", "escalation": {"target": "foreground", "reason": "delivery_failed"}},
        {"effect": "unverifiable"},
    ]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.screenshot()
        await computer.type("15*15")
    assert [send["delivery_mode"] for send in _arguments(transport, "type_text")] == ["background", "foreground"]
    assert computer.delivery_counts["fallback_skips"] == 0
    assert computer.delivery_counts["foreground_escalations"] == 1
    # The guard frame plus the frame the model reasoned over.
    assert _names(transport).count("get_window_state") == 2


async def test_foreground_fallback_that_still_fails_raises_after_one_retry():
    transport = WindowFakeTransport()
    transport.action_results["click"] = [{"effect": "suspected_noop"}, {"effect": "suspected_noop"}]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        with pytest.raises(MacOSBackgroundDeliveryError) as raised:
            await computer.click(1, 1)
    assert _names(transport).count("click") == 2
    assert raised.value.outcome is not None
    assert raised.value.outcome.escalated and raised.value.outcome.requested_delivery == "foreground"
    assert computer.delivery_counts == {
        "background_attempts": 1,
        "foreground_escalations": 1,
        "fallback_skips": 0,
        "accessibility_rungs": 0,
        "background_refusals": 1,
        "window_rebinds": 0,
    }


async def test_modified_click_in_strict_window_scope_is_refused_before_driver_input():
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport) as computer:
        with pytest.raises(MacOSRecoverableActionError, match="foreground delivery"):
            await computer.click(1, 1, modifier=["cmd"])
        await computer.key_down("shift")
        with pytest.raises(MacOSRecoverableActionError, match="foreground delivery"):
            await computer.click(1, 1)
    assert "click" not in _names(transport)


async def test_modified_click_goes_foreground_when_fallback_is_allowed():
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.click(1, 1, modifier=["cmd"])
    (click,) = _arguments(transport, "click")
    assert click["delivery_mode"] == "foreground" and click["modifier"] == ["cmd"]
    assert computer.delivery_counts["foreground_escalations"] == 1
    assert computer.delivery_counts["background_attempts"] == 0
    assert computer.last_action_outcome is not None and computer.last_action_outcome.escalated


async def test_stale_frame_refusal_on_input_hands_back_a_fresh_frame():
    transport = WindowFakeTransport()
    transport.tool_errors["click"] = [_tool_error("px_frame_mismatch")]
    async with _bound_window_computer(transport) as computer:
        with pytest.raises(MacOSUncertainActionError, match="px_frame_mismatch") as raised:
            await computer.click(1, 1)
        assert raised.value.observation is not None
    assert computer.delivery_counts["window_rebinds"] == 0


async def test_capture_follows_the_app_to_another_window_when_the_target_window_is_gone(monkeypatch):
    transport = WindowFakeTransport(windows=[_window_record(window_id=9)])
    transport.tool_errors["get_window_state"] = [_tool_error("window_id_not_found")]
    async with _bound_window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        observation = await computer.screenshot()
        assert observation.native_width == 400
        assert computer.target_window == MacOSWindowTarget(PID, 9, title="Calculator", app_name="Calculator")
    assert [capture["window_id"] for capture in _arguments(transport, "get_window_state")] == [7, 9]
    assert _arguments(transport, "list_windows") == [{"pid": PID}]
    assert computer.delivery_counts["window_rebinds"] == 1


async def test_input_after_window_loss_rebinds_and_hands_the_model_the_new_window():
    transport = WindowFakeTransport([_png(400, 300), _png(500, 350)], windows=[_window_record(window_id=9)])
    transport.tool_errors["click"] = [
        CuaDriverToolError(
            "Cua Driver click failed: gone",
            structured={"status": "refused", "refusal": {"code": "window_id_not_found", "message": "gone"}},
        )
    ]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSTargetWindowChangedError) as raised:
            await computer.click(10, 10)
        error = raised.value
        assert error.recoverable
        assert f"(pid {PID}, window 7)" in str(error) and f"(pid {PID}, window 9)" in str(error)
        assert isinstance(error.observation, N2Observation)
        assert (error.observation.native_width, error.observation.native_height) == (500, 350)
        assert computer.target_window is not None and computer.target_window.window_id == 9
        await computer.click(10, 10)
    assert [click["target"]["window_id"] for click in _arguments(transport, "click")] == [7, 9]
    assert computer.delivery_counts["window_rebinds"] == 1


async def test_window_loss_with_a_dead_process_recovers_the_target_and_reresolves_its_window(monkeypatch):
    transport = WindowFakeTransport(windows=[_window_record(window_id=11)])
    transport.tool_errors["get_window_state"] = [_tool_error("window_owner_pid_mismatch")]
    recoveries = 0

    async def recover() -> int:
        nonlocal recoveries
        recoveries += 1
        return PID

    async with _window_computer(transport, recover_target=recover) as computer:
        await computer.set_window_target(MacOSWindowTarget(DEAD_PID, 7, app_name="Calculator"))
        monkeypatch.setattr(computer, "_pid_alive", lambda pid: pid == PID)
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        await computer.screenshot()
        assert recoveries == 1
        assert computer.target_pid == PID
        assert computer.target_window == MacOSWindowTarget(PID, 11, title="Calculator", app_name="Calculator")
        assert computer.target_recovery_attempts == 1
    assert [capture["pid"] for capture in _arguments(transport, "get_window_state")] == [DEAD_PID, PID]


async def test_window_loss_without_recovery_is_a_target_crash(monkeypatch):
    transport = WindowFakeTransport(windows=[])
    transport.tool_errors["get_window_state"] = [_tool_error("window_id_not_found")]
    async with _window_computer(transport) as computer:
        await computer.set_window_target(MacOSWindowTarget(DEAD_PID, 7))
        monkeypatch.setattr(computer, "_pid_alive", lambda _pid: False)
        with pytest.raises(MacOSTargetCrashedError):
            await computer.screenshot()
        assert computer.cancellation.cause == "target_crash"


async def test_live_process_with_no_windows_left_returns_app_state(monkeypatch):
    transport = WindowFakeTransport(windows=[])
    transport.tool_errors["get_window_state"] = [_tool_error("window_id_not_found")]
    async with _bound_window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        observation = await computer.screenshot()
        assert isinstance(observation, MacOSAppState)
        assert observation.windows == ()
        assert observation.menus == ()
        assert observation.menu_unavailable_reason == "no_window"
        assert computer.current_observation is None
        assert computer.target_window is None
    assert _names(transport).count("list_windows") == 3
    assert computer.cancellation.cause is None


async def test_app_without_windows_refuses_menu_and_binds_a_window_when_one_appears(monkeypatch):
    transport = WindowFakeTransport(windows=[])
    async with _window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        await computer.set_app_target(PID)
        state = await computer.screenshot()
        assert isinstance(state, MacOSAppState)
        assert "Coordinates are unavailable" in state.text
        assert "get_window_state" not in _names(transport)
        with pytest.raises(MacOSRecoverableActionError, match="No window"):
            await computer.click(10, 10)
        with pytest.raises(MacOSRecoverableActionError, match="Menu actions require a window"):
            await computer.invoke_app_menu(["File", "New"])
        assert not {"activate_app", "bring_to_front", "click"} & set(_names(transport))
        transport.windows = [_window_record(window_id=21)]
        frame = await computer.screenshot()
        assert isinstance(frame, N2Observation)
        assert computer.target_window.window_id == 21
        # Screenshots carry the cheap window list only; the menu projection (hundreds of rows
        # for a browser) is an explicit get_app_state read, never part of frame polling.
        assert '"menus"' not in frame.text and "get_app_state" in frame.text
        assert all(args["max_elements"] == 1 for args in _arguments(transport, "get_window_state"))
        transport.windows = []
        assert isinstance(await computer.screenshot(), MacOSAppState)
        assert computer.current_observation is None
        assert computer._native_size is None
        assert computer.cancellation.cause is None


def _menu_rows(token="fresh:1", disabled=False):
    return [
        {"role": "AXMenuBarItem", "label": "File", "depth": 1, "element_token": "fresh:0"},
        {"role": "AXMenuItem", "label": "New", "depth": 3, "element_token": token, "enabled": not disabled},
    ]


async def test_app_menus_use_fresh_window_ax_and_background_token_click_only():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": _menu_rows("old:1")}
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        state = await computer.get_app_state()
        assert state.menu_available and state.menus_truncated
        assert state.menus[1]["path"] == ["File", "New"]
        transport.window_state_extra[7] = {"elements": _menu_rows("new:1")}
        await computer.invoke_app_menu(["File", "New"])
        assert _arguments(transport, "click") == [
            {
                "session": computer.session,
                "pid": PID,
                "window_id": 7,
                "element_token": "new:1",
                "action": "press",
                "delivery_mode": "background",
            }
        ]
        assert computer.action_outcomes[-1].tool == "invoke_app_menu"
    assert all(not args["include_screenshot"] for args in _arguments(transport, "get_window_state"))
    assert set(_names(transport)) <= {
        "start_session",
        "end_session",
        "set_agent_cursor_enabled",
        "list_windows",
        "get_window_state",
        "click",
    }


async def test_top_level_menu_titles_are_never_pressed():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": _menu_rows()}
    async with _bound_window_computer(transport) as computer:
        with pytest.raises(MacOSRecoverableActionError, match="Top-level menu titles are not pressed"):
            await computer.invoke_app_menu(["File"])
    assert "click" not in _names(transport) and "get_window_state" not in _names(transport)


async def test_menu_truncation_comes_from_element_counts_and_tokens_stay_out_of_model_text():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {
        "elements": _menu_rows(),
        "elements_complete": False,
        "returned_element_count": 2,
        "total_element_count": 2,
    }
    async with _bound_window_computer(transport) as computer:
        state = await computer.get_app_state()
    assert state.menu_available and not state.menus_truncated
    assert "fresh:1" in state.menus[1]["element_token"] and "fresh:1" not in state.text


@pytest.mark.parametrize("rows", [[], _menu_rows(disabled=True), _menu_rows() + _menu_rows(disabled=True)])
async def test_unavailable_disabled_or_ambiguous_menus_never_click(rows):
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": rows}
    async with _bound_window_computer(transport) as computer:
        with pytest.raises(MacOSRecoverableActionError, match="unavailable, ambiguous, or disabled"):
            await computer.invoke_app_menu(["File", "New"])
    assert "click" not in _names(transport)


async def test_identical_duplicate_menu_rows_are_one_target():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": _menu_rows("first:1") + _menu_rows("second:1")}
    async with _bound_window_computer(transport) as computer:
        await computer.invoke_app_menu(["File", "New"])
    assert [args["element_token"] for args in _arguments(transport, "click")] == ["first:1"]


async def test_menu_press_refusals_and_window_loss_use_the_shared_delivery_path(monkeypatch):
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": _menu_rows()}
    transport.tool_errors["click"] = [_tool_error("minimized_or_hidden_window")]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        with pytest.raises(MacOSRecoverableActionError, match="refused \\(minimized_or_hidden_window\\)"):
            await computer.invoke_app_menu(["File", "New"])
        assert computer.action_outcomes[-1].tool == "invoke_app_menu"
        assert computer.action_outcomes[-1].refusal_code == "minimized_or_hidden_window"
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        transport.tool_errors["click"] = [_tool_error("window_id_not_found")]
        with pytest.raises(MacOSTargetWindowChangedError, match="now driving") as raised:
            await computer.invoke_app_menu(["File", "New"])
        assert isinstance(raised.value.observation, N2Observation)
    assert all(args["delivery_mode"] == "background" for args in _arguments(transport, "click"))
    assert "bring_to_front" not in _names(transport)


async def test_stale_menu_token_is_not_retried_or_escalated():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": _menu_rows()}
    transport.tool_errors["click"] = [_tool_error("stale_element_token")]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        with pytest.raises(CuaDriverToolError, match="stale_element_token"):
            await computer.invoke_app_menu(["File", "New"])
    assert len(_arguments(transport, "click")) == 1
    assert _arguments(transport, "click")[0]["delivery_mode"] == "background"


async def test_window_switch_during_menu_refresh_does_not_attach_old_pixels(monkeypatch):
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport) as computer:
        await computer.set_app_target(PID, window=computer.target_window)
        capture = computer._capture_observation_png

        async def close_and_replace(*args):
            pixels = await capture(*args)
            transport.windows = [_window_record(window_id=21)]
            return pixels

        monkeypatch.setattr(computer, "_capture_observation_png", close_and_replace)
        frame = await computer.screenshot()
        # Recaptured against the window the app moved to, not the pixels of the closed one.
        assert isinstance(frame, N2Observation) and computer.current_observation is frame
        assert computer.target_window.window_id == 21
        assert [args["window_id"] for args in _arguments(transport, "get_window_state")] == [7, 21]


async def test_frame_poll_never_settles_on_pixels_of_a_window_no_longer_driven():
    transport = WindowFakeTransport()
    async with _window_computer(transport) as computer:
        await computer.set_app_target(PID, window=MacOSWindowTarget(PID, 7))
        old_frame = await computer.screenshot()
        transport.windows = [_window_record(window_id=21)]
        await computer.get_app_state(include_menus=False)
        assert computer.target_window.window_id == 21 and computer.current_observation is None
        result = FramePollResult(
            outcome="undiffable", waited_ms=0, polls=1, capture_ms=0, last_frame=old_frame, changed_fraction=None
        )
        settled = await computer._settle_frame_poll(result, fallback=old_frame)
        assert isinstance(settled, N2Observation) and settled is not old_frame
        assert computer.current_observation is settled
        assert _arguments(transport, "get_window_state")[-1]["window_id"] == 21


async def test_menu_refresh_follows_the_app_to_a_remaining_window(monkeypatch):
    transport = WindowFakeTransport(windows=[_window_record(window_id=7), _window_record(window_id=21)])
    transport.tool_errors["get_window_state"] = [_tool_error("window_id_not_found")]
    transport.window_state_extra[21] = {"elements": _menu_rows()}
    async with _bound_window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        state = await computer.get_app_state()
        assert computer.target_window.window_id == 21
        assert state.menu_available and [menu["path"] for menu in state.menus] == [["File"], ["File", "New"]]
        assert len(state.windows) == 2
    assert [args["window_id"] for args in _arguments(transport, "get_window_state")] == [7, 21]


async def test_window_loss_during_frame_poll_does_not_restore_old_pixels(monkeypatch):
    from yutori.navigator.macos.polling import poll_until_frame_changes

    transport = WindowFakeTransport()
    async with _window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        await computer.set_app_target(PID, window=MacOSWindowTarget(PID, 7))
        frame = await computer.screenshot()
        transport.windows = []
        result = await poll_until_frame_changes(
            capture=computer.screenshot, reference=frame, mode="strict", budget_ms=100, min_wait_ms=0
        )
        assert isinstance(await computer._settle_frame_poll(result, fallback=frame), MacOSAppState)
        assert computer.current_observation is None


async def test_target_recovery_in_window_scope_rebinds_to_the_relaunched_process_window(monkeypatch):
    transport = WindowFakeTransport(windows=[_window_record(window_id=13)])

    async def recover() -> int:
        return PID

    async with _window_computer(transport, recover_target=recover) as computer:
        await computer.set_window_target(MacOSWindowTarget(DEAD_PID, 7))
        monkeypatch.setattr(computer, "_pid_alive", lambda pid: pid == PID)
        await computer._ensure_target_alive()
        assert computer.target_pid == PID
        assert computer.target_window == MacOSWindowTarget(PID, 13, title="Calculator", app_name="Calculator")
        assert computer.delivery_counts["window_rebinds"] == 1


async def test_target_recovery_keeps_a_window_the_recoverer_already_bound(monkeypatch):
    transport = WindowFakeTransport()
    computer = _window_computer(transport)

    async def recover_and_bind() -> int:
        await computer.set_window_target(MacOSWindowTarget(PID, 21))
        return PID

    computer.recover_target = recover_and_bind
    async with computer:
        await computer.set_window_target(MacOSWindowTarget(DEAD_PID, 7))
        monkeypatch.setattr(computer, "_pid_alive", lambda pid: pid == PID)
        await computer._ensure_target_alive()
        assert computer.target_window == MacOSWindowTarget(PID, 21)
        assert computer.delivery_counts["window_rebinds"] == 0
    assert "list_windows" not in _names(transport)


async def test_window_capture_gives_up_after_three_unusable_frames(monkeypatch):
    transport = WindowFakeTransport()
    transport.tool_errors["get_window_state"] = [_tool_error("px_capture_unavailable") for _ in range(3)]
    async with _bound_window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        with pytest.raises(computer_module.MacOSComputerError, match="after 3 attempts"):
            await computer.screenshot()
    assert _names(transport).count("get_window_state") == 3
    assert "list_windows" not in _names(transport)


async def test_set_window_target_requires_window_scope_and_a_released_mouse():
    desktop = MacOSComputer(FakeTransport(), owns_transport=False, presentation=False)
    with pytest.raises(computer_module.MacOSComputerError, match="scope='window'"):
        await desktop.set_window_target(MacOSWindowTarget(PID, 7))
    transport = WindowFakeTransport()
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.left_mouse_down(1, 1)
        with pytest.raises(MacOSRecoverableActionError, match="held mouse"):
            await computer.set_window_target(MacOSWindowTarget(PID, 9))
        await computer.left_mouse_up(1, 1)
        await computer.set_window_target(MacOSWindowTarget(PID, 9))
        assert computer.current_observation is None
        assert computer.target_window == MacOSWindowTarget(PID, 9)
        assert computer.window_target_info is not None and computer.window_target_info["capture_width"] is None


async def test_unhide_app_delegates_to_appkit_without_activating(monkeypatch):
    unhidden: list[int] = []

    async def fake_unhide(pid: int) -> bool:
        unhidden.append(pid)
        return True

    monkeypatch.setattr(computer_module, "unhide_application", fake_unhide)
    transport = WindowFakeTransport()
    async with _window_computer(transport) as computer:
        assert await computer.unhide_app(PID) is True
    assert unhidden == [PID]
    assert "bring_to_front" not in _names(transport)


_UNRESOLVED = {
    "degraded": True,
    "degraded_reason": "ax_window_unresolved: window_id 7 exists and is owned by the pid, but no AXWindow reports it",
    "element_count": 0,
}


async def test_capture_of_a_window_without_an_accessibility_window_moves_to_the_apps_live_window():
    dialog = _window_record(7, title="", bounds={"x": 0, "y": 0, "width": 500, "height": 500}, is_on_screen=False)
    document = _window_record(9, title="Untitled 16", z_index=83)
    transport = WindowFakeTransport([_png(400, 300)], windows=[dialog, document])
    transport.window_state_extra[7] = _UNRESOLVED
    async with _bound_window_computer(transport) as computer:
        observation = await computer.screenshot()
        assert observation.native_width == 400
        assert computer.target_window == MacOSWindowTarget(PID, 9, title="Untitled 16", app_name="Calculator")
    assert [capture["window_id"] for capture in _arguments(transport, "get_window_state")] == [7, 9]
    assert computer.delivery_counts["window_rebinds"] == 1


async def test_capture_of_an_unresolved_window_is_kept_when_the_app_has_no_other_window():
    transport = WindowFakeTransport([_png(400, 300)], windows=[_window_record(7)])
    transport.window_state_extra[7] = _UNRESOLVED
    async with _bound_window_computer(transport) as computer:
        observation = await computer.screenshot()
        assert observation.native_width == 400
        assert computer.target_window is not None and computer.target_window.window_id == 7
    assert _names(transport).count("get_window_state") == 1
    assert computer.delivery_counts["window_rebinds"] == 0


async def test_input_refused_for_an_unresolved_window_rebinds_to_the_apps_live_window():
    transport = WindowFakeTransport([_png(400, 300), _png(500, 350)], windows=[_window_record(7), _window_record(9)])
    transport.tool_errors["click"] = [_tool_error("off_space_or_ax_unresolved")]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSTargetWindowChangedError, match="can no longer be driven") as raised:
            await computer.click(10, 10)
        assert raised.value.recoverable
        assert raised.value.observation is not None and raised.value.observation.native_width == 500
        assert computer.target_window is not None and computer.target_window.window_id == 9
        await computer.click(10, 10)
    assert [click["target"]["window_id"] for click in _arguments(transport, "click")] == [7, 9]
    assert computer.delivery_counts["window_rebinds"] == 1


async def test_input_refused_for_an_unresolved_window_without_alternatives_is_a_recoverable_refusal():
    transport = WindowFakeTransport(windows=[_window_record(7)])
    transport.tool_errors["type_text"] = [_tool_error("off_space_or_ax_unresolved")]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        with pytest.raises(MacOSBackgroundDeliveryError, match="another Space") as raised:
            await computer.type("x")
        assert raised.value.recoverable and raised.value.observation is not None
        assert computer.target_window is not None and computer.target_window.window_id == 7
    assert _names(transport).count("type_text") == 1
    assert computer.delivery_counts["background_refusals"] == 1
    assert computer.delivery_counts["window_rebinds"] == 0


@pytest.mark.parametrize("code", ["same_pid_keyboard_ambiguity", "minimized_or_hidden_window"])
async def test_upfront_keyboard_refusals_escalate_to_foreground_when_allowed(code):
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error(code)]
    transport.action_results["type_text"] = [{"effect": "unverifiable", "delivery": {"mode": "foreground"}}]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.screenshot()
        await computer.type("hello")
    sends = _arguments(transport, "type_text")
    assert [send["delivery_mode"] for send in sends] == ["background", "foreground"]
    assert computer.delivery_counts["foreground_escalations"] == 1
    assert computer.delivery_counts["background_refusals"] == 0
    refused, retried = computer.action_outcomes[-2:]
    assert (refused.effect, refused.refusal_code, refused.landed) == ("refused", code, False)
    assert retried.escalated and retried.landed


@pytest.mark.parametrize("code", ["same_pid_keyboard_ambiguity", "minimized_or_hidden_window"])
async def test_upfront_keyboard_refusals_are_recoverable_in_strict_window_scope(code):
    transport = WindowFakeTransport()
    transport.tool_errors["hotkey"] = [_tool_error(code)]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSBackgroundDeliveryError, match=code) as raised:
            await computer.keypress(["cmd", "shift", "s"])
        assert raised.value.recoverable and raised.value.observation is not None
        assert raised.value.outcome is not None and raised.value.outcome.refusal_code == code
    assert _names(transport).count("hotkey") == 1
    assert computer.delivery_counts["background_refusals"] == 1


def _element(index: int, role: str, frame: dict[str, float], **extra: Any) -> dict[str, Any]:
    return {
        "element_index": index,
        "element_token": f"s0000000a:{index}",
        "role": role,
        "frame": frame,
        **extra,
    }


# The fake window capture is 400x300, so a root AXWindow at the origin with those point
# dimensions makes capture pixels and element-frame points the same numbers.
_ROOT_WINDOW = _element(0, "AXWindow", {"x": 0.0, "y": 0.0, "w": 400.0, "h": 300.0})
_SEARCH_FIELD = _element(1, "AXTextField", {"x": 10.0, "y": 10.0, "w": 100.0, "h": 20.0})


async def test_keyboard_ambiguity_retries_type_text_as_a_write_to_the_clicked_field():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.type("hello")
    first, retried = _arguments(transport, "type_text")
    assert "element_token" not in first
    assert retried["element_token"] == _SEARCH_FIELD["element_token"]
    assert retried["delivery_mode"] == "background" and retried["text"] == "hello"
    assert computer.delivery_counts["accessibility_rungs"] == 1
    assert computer.delivery_counts["background_refusals"] == 0
    assert computer.last_action_outcome is not None and computer.last_action_outcome.landed


async def test_the_accessibility_rung_reads_the_tree_without_paying_for_a_capture():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.type("hello")
    rung_snapshot = _arguments(transport, "get_window_state")[-1]
    assert rung_snapshot["include_screenshot"] is False
    assert rung_snapshot["max_elements"] > 1


async def test_hover_does_not_retarget_typing_away_from_the_clicked_field():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    other = _element(2, "AXTextField", {"x": 200.0, "y": 200.0, "w": 100.0, "h": 20.0})
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD, other]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.move(220, 210)
        await computer.type("hello")
    assert _arguments(transport, "type_text")[-1]["element_token"] == _SEARCH_FIELD["element_token"]


@pytest.mark.parametrize("resize", [False, True])
async def test_click_fallback_cannot_restore_a_typing_target_invalidated_by_its_capture(resize):
    changed_frame = _png(800, 600) if resize else _png(400, 300, color=(240, 240, 240))
    transport = WindowFakeTransport([_png(400, 300), changed_frame])
    transport.action_results["click"] = [{"effect": "suspected_noop"}, {"effect": "unverifiable"}]
    clicked = _element(1, "AXTextField", {"x": 70.0, "y": 25.0, "w": 20.0, "h": 10.0})
    other = _element(2, "AXTextField", {"x": 30.0, "y": 10.0, "w": 20.0, "h": 10.0})
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, clicked, other]}
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.screenshot()
        await computer.click(80, 30)
        await computer.type("hello")
    sends = _arguments(transport, "type_text")
    assert [send["delivery_mode"] for send in sends] == ["background", "foreground"]
    assert all("element_token" not in send for send in sends)
    assert computer.delivery_counts["accessibility_rungs"] == 0


@pytest.mark.parametrize("intervening", ["hover", "outside", "right", "tab", "scroll", "drag", "resize", "rebind"])
async def test_typing_does_not_guess_a_field_without_a_current_explicit_click(intervening):
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    transport.window_state_extra[9] = transport.window_state_extra[7]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        if intervening != "hover":
            await computer.click(20, 15)
        if intervening == "hover":
            await computer.move(20, 15)
        elif intervening == "outside":
            await computer.click(300, 250)
        elif intervening == "right":
            await computer.click(20, 15, button="right")
        elif intervening == "tab":
            await computer.keypress("tab")
        elif intervening == "scroll":
            await computer.scroll(20, 15, 0, 100)
        elif intervening == "drag":
            await computer.drag([{"x": 20, "y": 15}, {"x": 50, "y": 15}])
        elif intervening == "resize":
            transport.window_frames = [_png(800, 600)]
            await computer.screenshot()
        else:
            await computer.set_window_target(MacOSWindowTarget(PID, 9))
            await computer.screenshot()
        with pytest.raises(MacOSBackgroundDeliveryError, match="same_pid_keyboard_ambiguity"):
            await computer.type("hello")
    assert len(_arguments(transport, "type_text")) == 1
    assert computer.delivery_counts["accessibility_rungs"] == 0


async def test_failed_keypress_invalidates_the_typing_target_before_dispatch():
    transport = WindowFakeTransport()
    transport.tool_errors["press_key"] = [CuaDriverUncertainActionError("acknowledgement lost")]
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        with pytest.raises(MacOSUncertainActionError):
            await computer.keypress("tab")
        with pytest.raises(MacOSBackgroundDeliveryError):
            await computer.type("hello")
    assert len(_arguments(transport, "type_text")) == 1


async def test_uncertain_click_cannot_leave_the_previous_typing_target_selected():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        transport.tool_errors["click"] = [CuaDriverUncertainActionError("acknowledgement lost")]
        with pytest.raises(MacOSUncertainActionError):
            await computer.click(300, 250)
        transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
        with pytest.raises(MacOSBackgroundDeliveryError):
            await computer.type("hello")
    assert len(_arguments(transport, "type_text")) == 1
    assert computer.delivery_counts["accessibility_rungs"] == 0


async def test_uncertain_typing_consumes_the_recent_click_before_another_attempt():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    transport.tool_errors["type_text"] = [
        CuaDriverUncertainActionError("acknowledgement lost"),
        _tool_error("same_pid_keyboard_ambiguity"),
    ]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        with pytest.raises(MacOSUncertainActionError):
            await computer.type("first")
        with pytest.raises(MacOSBackgroundDeliveryError):
            await computer.type("second")
    assert len(_arguments(transport, "type_text")) == 2
    assert computer.delivery_counts["accessibility_rungs"] == 0


async def test_typing_consumes_the_recent_click_instead_of_reusing_it_for_later_input():
    transport = WindowFakeTransport()
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.type("first")
        transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
        with pytest.raises(MacOSBackgroundDeliveryError):
            await computer.type("second")
    assert len(_arguments(transport, "type_text")) == 2
    assert all("element_token" not in send for send in _arguments(transport, "type_text"))


async def test_the_accessibility_rung_is_skipped_when_the_field_under_the_pointer_is_ambiguous():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    elsewhere = _element(2, "AXTextField", {"x": 200.0, "y": 200.0, "w": 100.0, "h": 20.0})
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD, elsewhere]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(380, 290)  # outside both fields
        with pytest.raises(MacOSBackgroundDeliveryError, match="same_pid_keyboard_ambiguity"):
            await computer.type("hello")
    assert _names(transport).count("type_text") == 1
    assert computer.delivery_counts["accessibility_rungs"] == 0


async def test_keyboard_ambiguity_with_no_text_field_explains_the_sibling_window():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [_tool_error("same_pid_keyboard_ambiguity")]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSBackgroundDeliveryError, match="more than one open window") as raised:
            await computer.type("hello")
        assert raised.value.recoverable and raised.value.observation is not None
        # Closing the sibling window is the one "fix" that bricks the app: a windowless
        # application cannot be driven in window scope, and the frame has no Dock to reopen it.
        assert "Do NOT close" in str(raised.value)
    assert _names(transport).count("type_text") == 1
    assert computer.delivery_counts["background_refusals"] == 1


async def test_a_refusal_keeps_the_rung_the_driver_recommended():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [
        CuaDriverToolError(
            "Cua Driver failed: same_pid_keyboard_ambiguity",
            structured={
                "code": "same_pid_keyboard_ambiguity",
                "effect": "refused",
                "escalation": {"recommended": "accessibility", "reason": "pid 9 owns 1 other window"},
            },
        )
    ]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSBackgroundDeliveryError):
            await computer.type("hello")
    outcome = computer.last_action_outcome
    assert outcome is not None
    assert (outcome.recommended, outcome.escalation_reason) == ("accessibility", "pid 9 owns 1 other window")


async def test_a_payload_less_refusal_still_reads_as_wanting_the_foreground_rung():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [CuaDriverToolError("Cua Driver failed: minimized_or_hidden_window")]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        with pytest.raises(MacOSBackgroundDeliveryError):
            await computer.type("hello")
    outcome = computer.last_action_outcome
    assert outcome is not None and (outcome.effect, outcome.recommended) == ("refused", "foreground")


class _FakeStreamer:
    instances: list[_FakeStreamer] = []

    def __init__(self, **kwargs: Any):
        self.kwargs = kwargs
        self.active_states: list[bool] = []
        self.closed = False
        self.frames_sent = 4
        self.__class__.instances.append(self)

    def set_active(self, active: bool) -> None:
        self.active_states.append(active)

    async def aclose(self) -> None:
        self.closed = True


async def test_window_scope_wires_the_live_view_streamer_to_the_status_item(monkeypatch):
    _FakeStatusController.instances.clear()
    _FakeStreamer.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeStatusController)
    monkeypatch.setattr(computer_module, "WindowPreviewStreamer", _FakeStreamer)
    transport = WindowFakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=True, scope="window") as computer:
        (controller,) = _FakeStatusController.instances
        (streamer,) = _FakeStreamer.instances
        assert controller.on_preview_demand == streamer.set_active
        assert streamer.kwargs["sink"] == controller.show_preview_frame
        assert streamer.kwargs["cancellation"] is computer.cancellation
        assert streamer.kwargs["target"]() is None
        target = MacOSWindowTarget(PID, 7, app_name="Calculator")
        await computer.set_window_target(target)
        assert streamer.kwargs["target"]() == target
        controller.on_preview_demand(True)
        controller.on_preview_demand(False)
        assert streamer.active_states == [True, False]
        assert computer.preview_frames_sent == 4
        assert not streamer.closed
    assert streamer.closed


async def test_window_scope_without_a_status_item_has_no_streamer(monkeypatch):
    _FakeStreamer.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeStatusController)
    monkeypatch.setattr(_FakeStatusController, "fail_start", True)
    monkeypatch.setattr(computer_module, "WindowPreviewStreamer", _FakeStreamer)
    async with MacOSComputer(
        WindowFakeTransport(), owns_transport=False, presentation=True, scope="window"
    ) as computer:
        assert computer.preview_frames_sent == 0
    assert not _FakeStreamer.instances
    async with _bound_window_computer(WindowFakeTransport()) as computer:
        assert computer.preview_frames_sent == 0
    assert not _FakeStreamer.instances


async def test_keypress_speaks_the_driver_key_vocabulary():
    """n2's canonical key names cross the RPC boundary as names cua-driver's macOS keycode table knows.

    The table spells the page keys `pageup`/`pagedown`, and it reads `delete` as kVK_Delete (erase LEFT),
    so the forward delete the model means has to travel as `forward_delete`. `backspace`, modifiers, and
    names the table already knows pass through untouched.
    """
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False, verify_focus=False) as computer:
        await computer.keypress("page_up")
        await computer.keypress("page_down")
        await computer.keypress("delete")
        await computer.keypress("backspace")
        await computer.keypress("Return")
        await computer.keypress(["cmd", "delete"])
        await computer.keypress(["shift", "page_down"])
    assert [call["key"] for call in _arguments(transport, "press_key")] == [
        "pageup",
        "pagedown",
        "forward_delete",
        "backspace",
        "Return",
    ]
    assert [call["keys"] for call in _arguments(transport, "hotkey")] == [
        ["cmd", "forward_delete"],
        ["shift", "pagedown"],
    ]


async def test_type_sends_each_newline_as_a_return_keystroke(monkeypatch):
    """Cua Driver has no keyboard meaning for a newline character, so the SDK presses Return for it.

    The accessibility write inserts "\\n" literally and the synthetic rung sends it as keycode 0, so
    without this split "query\\n" never submits an address bar or a form on macOS.
    """
    transport = FakeTransport()
    settles: list[float] = []

    async def record_settle(seconds: float) -> None:
        settles.append(seconds)

    async with MacOSComputer(transport, owns_transport=False, presentation=False, verify_focus=False) as computer:
        monkeypatch.setattr(computer, "_sleep", record_settle)
        await computer.type("first line\nsecond\r\n\n")
    keyboard = [
        (name, arguments["text"] if name == "type_text" else arguments["key"])
        for name, arguments, _ in transport.calls
        if name in {"type_text", "press_key"}
    ]
    assert keyboard == [
        ("type_text", "first line"),
        ("press_key", "enter"),
        ("type_text", "second"),
        ("press_key", "enter"),
        ("press_key", "enter"),
    ]
    # Only a Return followed by more text waits for the app to process it.
    assert settles == [computer_module._RETURN_SETTLE_SECONDS]
    press = _arguments(transport, "press_key")[0]
    assert press["scope"] == "desktop" and press["delivery_mode"] == "foreground"


async def test_type_without_a_newline_stays_one_type_text_call():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False, verify_focus=False) as computer:
        await computer.type("plain text")
        await computer.type("")
    assert [arguments["text"] for arguments in _arguments(transport, "type_text")] == ["plain text", ""]
    assert _arguments(transport, "press_key") == []


async def test_a_lone_newline_is_just_return():
    transport = FakeTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=False, verify_focus=False) as computer:
        await computer.type("\n")
    assert _arguments(transport, "type_text") == []
    assert [arguments["key"] for arguments in _arguments(transport, "press_key")] == ["enter"]


async def test_newline_return_keeps_the_clicked_field_as_the_typing_target(monkeypatch):
    """The Return inside typed text must not drop the accessibility rung for the text after it."""

    class SiblingWindowTransport(WindowFakeTransport):
        """Refuses every pid-addressed type_text, like a driver facing a second window; AX writes land."""

        async def call_tool(self, name, arguments, *, read_only=False, timeout_seconds=None):
            if name == "type_text" and "element_token" not in arguments:
                self.calls.append((name, arguments, read_only))
                raise _tool_error("same_pid_keyboard_ambiguity")
            return await super().call_tool(name, arguments, read_only=read_only, timeout_seconds=timeout_seconds)

    transport = SiblingWindowTransport()
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.type("hello\nworld")
    sends = _arguments(transport, "type_text")
    assert [send["text"] for send in sends] == ["hello", "hello", "world", "world"]
    assert [send.get("element_token") for send in sends] == [
        None,
        _SEARCH_FIELD["element_token"],
        None,
        _SEARCH_FIELD["element_token"],
    ]
    press = _arguments(transport, "press_key")
    assert [arguments["key"] for arguments in press] == ["enter"]
    assert press[0]["target"] == {"kind": "window", "pid": PID, "window_id": 7}
    assert press[0]["delivery_mode"] == "background"
    assert computer.delivery_counts["accessibility_rungs"] == 2


async def test_a_refused_return_after_typed_text_still_escalates_to_foreground(monkeypatch):
    """The text before the newline changes the window; that must not read as the Return having landed.

    The fallback skips its foreground retry when the window changed since the frame the action
    started from, so a Return inside typed text needs a reference captured after the text landed.
    """
    transport = WindowFakeTransport([_png(400, 300), _png(400, 300, color=(240, 240, 240))])
    transport.tool_errors["press_key"] = [_tool_error("same_pid_keyboard_ambiguity")]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        monkeypatch.setattr(computer, "_sleep", _no_wait)
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.type("query\n")
    presses = _arguments(transport, "press_key")
    assert [press["delivery_mode"] for press in presses] == ["background", "foreground"]
    assert computer.delivery_counts["fallback_skips"] == 0
    assert computer.delivery_counts["foreground_escalations"] == 1


async def test_a_refused_return_inside_typed_text_surfaces_as_a_refusal():
    """A newline that cannot be pressed is reported, not swallowed into the field as a character."""
    transport = WindowFakeTransport()
    transport.tool_errors["press_key"] = [_tool_error("same_pid_keyboard_ambiguity")]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        with pytest.raises(MacOSBackgroundDeliveryError, match="press_key"):
            await computer.type("query\n")
    assert [send["text"] for send in _arguments(transport, "type_text")] == ["query"]
    assert computer._text_input_point is None


async def test_host_window_ids_reach_the_presentation_controller(monkeypatch):
    """The computer hands a host's window ids to the controller that captures the model's frames."""
    seen: dict = {}

    class _RecordingController:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def start(self):
            raise RuntimeError("stop before the overlay is needed")

        async def stop(self):
            return None

    monkeypatch.setattr(computer_module, "MacOSPresentationController", _RecordingController)
    computer = MacOSComputer(transport=FakeTransport(), owns_transport=False, exclude_capture_window_ids=[101, 202])
    assert computer.exclude_capture_window_ids == (101, 202)
    monkeypatch.setattr(computer, "_restore_native_cursor", _noop_restore)
    await computer._start_presentation(2000, 1200)
    assert seen["exclude_capture_window_ids"] == (101, 202)
    assert seen["exclude_from_capture"] is True
    assert computer.presentation is None


async def _noop_restore():
    return "current"


async def test_action_outcomes_record_every_rung_of_the_delivery_ladder():
    transport = WindowFakeTransport()
    transport.tool_errors["type_text"] = [
        CuaDriverToolError(
            "Background input refused (same_pid_keyboard_ambiguity): pid 9 owns 1 other window",
            structured={
                "code": "same_pid_keyboard_ambiguity",
                "effect": "refused",
                "reason": "pid 9 owns 1 other eligible top-level window(s)",
                "escalation": {"recommended": "accessibility", "reason": "pid 9 owns 1 other window"},
            },
        )
    ]
    transport.action_results["type_text"] = [
        {"effect": "confirmed", "route": "accessibility", "path": "ax", "delivery": {"mode": "background"}}
    ]
    transport.window_state_extra[7] = {"elements": [_ROOT_WINDOW, _SEARCH_FIELD]}
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.click(20, 15)
        await computer.type("hello")
    refused, landed = computer.action_outcomes[-2:]
    assert (refused.rung, refused.effect, refused.refusal_code) == (
        "background",
        "refused",
        "same_pid_keyboard_ambiguity",
    )
    assert refused.reason == "pid 9 owns 1 other eligible top-level window(s)"
    assert refused.detail is not None and refused.detail.startswith("Background input refused")
    assert (refused.text_chars, refused.element_addressed, refused.key) == (5, False, None)
    assert (landed.rung, landed.effect, landed.path, landed.element_addressed) == (
        "accessibility",
        "confirmed",
        "ax",
        True,
    )
    telemetry = landed.as_telemetry()
    assert telemetry["rung"] == "accessibility" and telemetry["landed"] is True
    assert telemetry["text_chars"] == 5 and "text" not in telemetry


async def test_press_key_outcome_records_the_key_and_the_drivers_result_line():
    transport = WindowFakeTransport()
    transport.action_results["press_key"] = [
        {"effect": "unverifiable", "path": "key_events", "delivery": {"mode": "background"}}
    ]
    transport.result_text["press_key"] = ["✅ Pressed return on pid 9."]
    async with _bound_window_computer(transport) as computer:
        await computer.screenshot()
        await computer.keypress("Return")
        await computer.keypress(["cmd", "n"])
    pressed, chord = computer.action_outcomes[-2:]
    assert (pressed.tool, pressed.key, pressed.rung, pressed.path) == (
        "press_key",
        "Return",
        "background",
        "key_events",
    )
    assert pressed.detail == "✅ Pressed return on pid 9."
    assert (chord.tool, chord.key) == ("hotkey", "cmd+n")


async def test_a_withheld_foreground_retry_is_recorded_without_becoming_a_verdict():
    transport = WindowFakeTransport([_png(400, 300), _png(400, 300, color=(240, 240, 240))])
    transport.action_results["press_key"] = [{"effect": "suspected_noop"}]
    async with _bound_window_computer(transport, allow_foreground_fallback=True) as computer:
        await computer.screenshot()
        await computer.keypress("Return")
    attempted, skipped = computer.action_outcomes[-2:]
    assert (attempted.rung, attempted.effect) == ("background", "suspected_noop")
    assert (skipped.rung, skipped.effect, skipped.key) == ("foreground_skipped", "skipped", "Return")
    assert skipped.landed is False and skipped.as_telemetry()["landed"] is False
    assert skipped.reason is not None and "withheld" in skipped.reason
    assert computer.delivery_counts["fallback_skips"] == 1
    # The record is for the trace; the driver's own last verdict is what steers the next action.
    assert computer.last_action_outcome is attempted
    assert _names(transport).count("press_key") == 1
