"""Tests for protocol-v2 presentation mapping and degradation."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from yutori.navigator.macos.presentation import MacOSPresentationController, MacOSPresentationError
from yutori.navigator.macos.types import (
    MacOSPresentationCapabilities,
    MacOSPresentationStatus,
    ShellPresentationEvent,
)


def _active_controller() -> MacOSPresentationController:
    controller = MacOSPresentationController(native_width=2000, native_height=1200)
    capabilities = MacOSPresentationCapabilities(2, 1000, 600, 2.0, True, (850, 10, 140, 50))
    controller._status = MacOSPresentationStatus(True, True, "active", "yutori", capabilities)
    controller._viewport = (1000, 600)
    return controller


def test_ready_handshake_validates_protocol_geometry_scale_hotkey_and_stop_region():
    controller = MacOSPresentationController(native_width=2000, native_height=1200)
    capabilities = controller._validate_ready(
        {
            "protocol_version": 2,
            "width": 1000,
            "height": 600,
            "backing_scale": 2,
            "hotkey": True,
            "stop_region": {"x": 850, "y": 10, "width": 140, "height": 50},
        }
    )
    assert capabilities.hotkey is True
    assert capabilities.stop_region == (850, 10, 140, 50)

    with pytest.raises(MacOSPresentationError, match="geometry"):
        controller._validate_ready(
            {
                "protocol_version": 2,
                "width": 1000,
                "height": 700,
                "backing_scale": 2,
                "hotkey": True,
                "stop_region": {"x": 850, "y": 10, "width": 140, "height": 50},
            }
        )
    with pytest.raises(MacOSPresentationError, match="Stop region"):
        controller._validate_ready(
            {
                "protocol_version": 2,
                "width": 1000,
                "height": 600,
                "backing_scale": 2,
                "hotkey": True,
            }
        )


def test_menu_bar_stop_item_on_another_display_is_accepted_without_a_region():
    controller = MacOSPresentationController(native_width=2000, native_height=1200)
    capabilities = controller._validate_ready(
        {
            "protocol_version": 2,
            "width": 1000,
            "height": 600,
            "backing_scale": 2,
            "hotkey": True,
            "stop_control": "menu_bar",
        }
    )
    assert capabilities.stop_region is None
    controller._status = MacOSPresentationStatus(True, True, "active", "yutori", capabilities)
    assert not controller.blocks_point((990, 5))


async def test_reasoning_action_and_batch_map_to_renderer_operations(monkeypatch):
    controller = _active_controller()
    operations: list[dict] = []
    commands: list[dict] = []

    async def send_operation(operation, **_kwargs):
        operations.append(operation)
        return {"ok": True}

    async def send_command(command, **_kwargs):
        commands.append(command)
        return {"ok": True}

    async def lead():
        operations.append({"op": "lead"})
        return True

    monkeypatch.setattr(controller, "_send_operation", send_operation)
    monkeypatch.setattr(controller, "_send_command", send_command)
    monkeypatch.setattr(controller, "_lead", lead)

    await controller.present({"type": "reasoning", "text": "Inspect the dialog"})
    await controller.present({"type": "action", "name": "left_click", "arguments": {"coordinates": [500, 500]}})
    batch = {
        "id": "c1",
        "index": 0,
        "members": [
            {"name": "left_click", "arguments": {"coordinates": [100, 100]}},
            {"name": "key_press", "arguments": {"key": "cmd+c"}},
        ],
    }
    await controller.present(
        {
            "type": "batch_member",
            "name": "left_click",
            "arguments": {"coordinates": [100, 100]},
            "batch": batch,
        }
    )

    assert any(
        operation.get("op") == "showThought" and "Inspect the dialog" in operation["markdown"]
        for operation in operations
    )
    assert {"op": "moveCursor", "point": {"x": 500.0, "y": 300.0}} in operations
    assert any(
        operation.get("op") == "previewAction" and operation["presentation"].get("queue") for operation in operations
    )
    assert any(command.get("op") == "pulse" for command in commands)
    assert operations.index({"op": "lead"}) < len(operations)

    operations.clear()
    await controller.present({"type": "action", "name": "key_press", "arguments": {"key": "cmd+tab"}})
    assert {"op": "clearThought"} in operations
    assert any(
        operation.get("op") == "previewAction"
        and operation["presentation"]["badge"] == {"type": "command", "keys": ["Tab"]}
        for operation in operations
    )


async def test_capture_ids_are_monotonic_and_a_stale_transition_degrades(monkeypatch):
    restored = False

    async def restore():
        nonlocal restored
        restored = True
        return "yutori.default"

    controller = _active_controller()
    controller._restore_native_cursor = restore

    async def send_command(command, **_kwargs):
        if command["op"] == "captureHide":
            return {"capture_id": command["capture_id"], "state": "hidden"}
        return {"capture_id": command["capture_id"], "state": "visible"}

    monkeypatch.setattr(controller, "_send_command", send_command)
    assert await controller.before_capture(1)
    assert await controller.after_capture(1, 2000, 1200)
    assert not await controller.before_capture(1)
    assert controller.status.state == "degraded"
    assert controller.status.degradation_reason == "capture_hide_failed"
    assert restored


async def test_presentation_failure_is_fail_soft_and_status_is_immutable(monkeypatch):
    controller = _active_controller()

    async def fail(_operation, **_kwargs):
        raise MacOSPresentationError("webkit died")

    monkeypatch.setattr(controller, "_send_operation", fail)
    await controller.present({"type": "reasoning", "text": "continue anyway"})
    assert controller.status.available is False
    assert controller.status.degradation_reason == "presentation_failed:MacOSPresentationError"
    with pytest.raises(FrozenInstanceError):
        controller.status.state = "active"


async def test_degraded_overlay_no_longer_blocks_its_former_stop_region():
    controller = _active_controller()
    point = (900, 20)
    assert controller.blocks_point(point)
    await controller._degrade("host_exited")
    assert not controller.blocks_point(point)


async def test_malformed_host_reply_and_lost_acknowledgement_are_classified(monkeypatch):
    controller = _active_controller()
    reader = asyncio.StreamReader()
    reader.feed_data(b"not-json\n")
    reader.feed_eof()
    controller._process = SimpleNamespace(stdout=reader)
    degraded: list[str] = []

    async def degrade(reason, _error=None):
        degraded.append(reason)

    monkeypatch.setattr(controller, "_degrade", degrade)
    await controller._read_host()
    assert degraded == ["malformed_host_reply"]

    class Stdin:
        def write(self, _data):
            return None

        async def drain(self):
            return None

    controller = _active_controller()
    controller._process = SimpleNamespace(stdin=Stdin(), returncode=None)
    with pytest.raises(MacOSPresentationError, match="timed out"):
        await controller._send_envelope({"command": {"op": "arm"}}, timeout=0.001)
    assert controller._pending == {}


async def test_late_unknown_reply_is_ignored_but_stop_event_still_latches(monkeypatch):
    controller = _active_controller()
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"id":999,"ok":true}\n{"event":"stop"}\n')
    reader.feed_eof()
    controller._process = SimpleNamespace(stdout=reader)
    degraded: list[str] = []

    async def degrade(reason, _error=None):
        degraded.append(reason)

    monkeypatch.setattr(controller, "_degrade", degrade)
    await controller._read_host()
    await asyncio.sleep(0)
    assert controller.cancellation.cause == "operator_stop"
    assert degraded == ["host_exited"]


async def test_cancelled_request_does_not_poison_the_next_overlay_reply(monkeypatch):
    class Stdin:
        def write(self, _data):
            return None

        async def drain(self):
            return None

    controller = _active_controller()
    reader = asyncio.StreamReader()
    controller._process = SimpleNamespace(stdin=Stdin(), stdout=reader, returncode=None)
    monkeypatch.setattr(controller, "_degrade", lambda *_args: asyncio.sleep(0))

    cancelled = asyncio.create_task(controller._send_envelope({"command": {"op": "first"}}))
    while 1 not in controller._pending:
        await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert controller._pending == {}

    second = asyncio.create_task(controller._send_envelope({"command": {"op": "second"}}))
    while 2 not in controller._pending:
        await asyncio.sleep(0)
    reader.feed_data(b'{"id":1,"ok":true}\n{"id":2,"ok":true}\n')
    reader.feed_eof()
    host = asyncio.create_task(controller._read_host())
    assert await second == {"id": 2, "ok": True}
    await host


def _shell(task_id: str, command: str, state: str, *, background: bool = False, exit_code: "int | None" = None):
    return {"type": "shell", "event": ShellPresentationEvent(task_id, command, background, state, exit_code)}


def _rail_controller(monkeypatch):
    """An active controller whose renderer traffic and sleeps are recorded instead of sent."""
    controller = _active_controller()
    operations: list[dict] = []
    commands: list[dict] = []
    sleeps: list[float] = []

    async def send_operation(operation, **_kwargs):
        operations.append(operation)
        return {"ok": True}

    async def send_command(command, **_kwargs):
        commands.append(command)
        return {"ok": True}

    async def sleep(seconds):
        sleeps.append(seconds)
        return True

    monkeypatch.setattr(controller, "_send_operation", send_operation)
    monkeypatch.setattr(controller, "_send_command", send_command)
    monkeypatch.setattr(controller, "_sleep", sleep)
    return controller, operations, commands, sleeps


def _rail_renders(commands: list[dict]) -> list[dict]:
    return [command for command in commands if command.get("op") == "shellCommands"]


async def test_foreground_shell_command_is_shown_in_the_rail_and_the_capsule(monkeypatch):
    controller, operations, commands, sleeps = _rail_controller(monkeypatch)

    await controller.present(_shell("shell-1", "ls -la ~/Documents", "starting"))
    await controller.present(_shell("shell-1", "ls -la ~/Documents", "running"))

    rail = _rail_renders(commands)
    assert [entry["state"] for render in rail for entry in render["commands"]] == ["starting", "running"]
    assert rail[-1]["commands"][0]["command"] == "ls -la ~/Documents"
    assert rail[-1]["commands"][0]["run_in_background"] is False
    assert rail[-1]["overflow"] == 0
    assert any(
        operation.get("op") == "showThought" and "Run command · $ ls -la ~/Documents" in operation["markdown"]
        for operation in operations
    )

    await controller.present(_shell("shell-1", "ls -la ~/Documents", "completed", exit_code=0))
    finished = _rail_renders(commands)[-1]["commands"][0]
    assert (finished["state"], finished["exit_code"]) == ("completed", 0)
    assert controller._shell_rail_removals
    await asyncio.gather(*controller._shell_rail_removals)
    assert _rail_renders(commands)[-1] == {"op": "shellCommands", "commands": [], "overflow": 0}
    assert 4.0 in sleeps


async def test_shell_rail_lists_newest_first_with_overflow_and_keeps_background_commands(monkeypatch):
    controller, _operations, commands, _sleeps = _rail_controller(monkeypatch)

    await controller.present(_shell("bash-1", "sleep 30", "running", background=True))
    await controller.present(_shell("shell-2", "pwd", "running"))
    await controller.present(_shell("shell-3", "whoami", "running"))
    await controller.present(_shell("shell-4", "date", "running"))

    render = _rail_renders(commands)[-1]
    assert [entry["task_id"] for entry in render["commands"]] == ["shell-4", "shell-3", "shell-2"]
    assert render["overflow"] == 1
    assert not controller._shell_rail_removals

    await controller.present(_shell("shell-4", "date", "failed", exit_code=1))
    await asyncio.gather(*controller._shell_rail_removals)
    render = _rail_renders(commands)[-1]
    assert [entry["task_id"] for entry in render["commands"]] == ["shell-3", "shell-2", "bash-1"]
    assert render["commands"][-1]["run_in_background"] is True
    assert render["overflow"] == 0
    assert controller.background_counts == {"started": 1, "completed": 0, "failed": 0, "cancelled": 0}


async def test_shell_rail_does_not_resend_an_unchanged_render(monkeypatch):
    controller, _operations, commands, _sleeps = _rail_controller(monkeypatch)

    await controller.present(_shell("shell-1", "pwd", "running"))
    await controller.present(_shell("shell-1", "pwd", "running"))

    assert len(_rail_renders(commands)) == 1


async def test_overlay_mode_streams_the_same_conversation_to_the_activity_window(monkeypatch):
    """The activity window does not depend on how the run drives the Mac."""
    controller, _operations, commands, _sleeps = _rail_controller(monkeypatch)

    await controller.present({"type": "task", "text": "Rename the file"})
    await controller.present({"type": "reasoning", "text": "Finder is frontmost"})
    await controller.present({"type": "action", "name": "left_click", "arguments": {"coordinates": [100, 20]}})
    await controller.present(_shell("shell-1", "pwd", "running"))
    await controller.present({"type": "final", "text": "Renamed."})

    entries = [command["entry"] for command in commands if command.get("op") == "transcript"]
    assert [entry["kind"] for entry in entries] == ["task", "thinking", "action", "shell", "final"]
    assert entries[3]["id"] == "shell-shell-1"


def _status_controller() -> MacOSPresentationController:
    controller = MacOSPresentationController(native_width=0, native_height=0, mode="status", title="Yutori n2 test")
    capabilities = MacOSPresentationCapabilities(2, 0, 0, 2.0, True, None)
    controller._status = MacOSPresentationStatus(True, True, "active", "hidden", capabilities)
    controller._viewport = (0, 0)
    return controller


def test_status_mode_handshake_skips_geometry_and_never_has_a_stop_region():
    controller = MacOSPresentationController(native_width=0, native_height=0, mode="status")
    capabilities = controller._validate_status_ready(
        {"protocol_version": 2, "mode": "status", "stop_control": "menu_bar", "backing_scale": 2, "hotkey": True}
    )
    assert (capabilities.viewport_width, capabilities.viewport_height) == (0, 0)
    assert capabilities.backing_scale == 2.0 and capabilities.hotkey is True and capabilities.stop_region is None
    with pytest.raises(MacOSPresentationError, match="status mode"):
        controller._validate_status_ready({"protocol_version": 2, "width": 1000, "height": 600, "backing_scale": 2})
    with pytest.raises(ValueError, match="mode"):
        MacOSPresentationController(native_width=0, native_height=0, mode="pill")


def _status_traffic(monkeypatch, controller):
    """Record what a status-mode controller sends, and fail on any page operation."""
    commands: list[dict] = []

    async def send_command(command, **_kwargs):
        commands.append(command)
        return {"ok": True, "state": "shown"}

    async def unexpected_operation(operation, **_kwargs):
        raise AssertionError(f"status mode must not send page operations: {operation}")

    async def sleep(_seconds):
        return True

    monkeypatch.setattr(controller, "_send_command", send_command)
    monkeypatch.setattr(controller, "_send_operation", unexpected_operation)
    monkeypatch.setattr(controller, "_sleep", sleep)
    return commands


def _of_op(commands: list[dict], op: str) -> list[dict]:
    return [command for command in commands if command.get("op") == op]


async def test_status_mode_maps_events_and_thumbnails_to_menu_commands(monkeypatch):
    controller = _status_controller()
    commands = _status_traffic(monkeypatch, controller)

    await controller.present({"type": "status", "text": "Driving Calculator (pid 4, window 7)"})
    await controller.present({"type": "reasoning", "text": "  Clear the display  "})
    await controller.present({"type": "action", "name": "left_click", "arguments": {"coordinates": [100.4, 20]}})
    await controller.present({"type": "action", "name": "left_click", "arguments": {"coordinates": [100.4, 20]}})
    await controller.present({"type": "final"})
    await controller.present({"type": "shell", "event": ShellPresentationEvent("t1", "ls -la", False, "running")})
    assert await controller.show_thumbnail(b"jpeg-bytes", caption="Frame 3") is True
    # The thumbnail caption replaced the shell status, so that same status must be sent again.
    await controller.present({"type": "shell", "event": ShellPresentationEvent("t1", "ls -la", False, "running")})
    await controller.present({"type": "shell", "event": ShellPresentationEvent("t1", "ls -la", False, "running")})
    assert await controller.before_capture(1) is False
    assert await controller.encode_observation(b"png") is None
    assert not controller.blocks_point((5, 5))

    captions = _of_op(commands, "status")
    assert [caption["text"] for caption in captions] == [
        "Driving Calculator (pid 4, window 7)",
        "Thinking: Clear the display",
        "left click at (100, 20)",
        "Finished",
        "Shell (running): ls -la",
        "Shell (running): ls -la",
    ]
    thumbnail = _of_op(commands, "thumbnail")[0]
    assert thumbnail["caption"] == "Frame 3"
    assert base64.b64decode(thumbnail["data"]) == b"jpeg-bytes"


async def test_status_mode_streams_the_conversation_to_the_activity_window(monkeypatch):
    controller = _status_controller()
    commands = _status_traffic(monkeypatch, controller)

    await controller.present({"type": "task", "text": "Add up the quarterly totals"})
    await controller.present({"type": "status", "text": "Driving Numbers (pid 4, window 7)"})
    await controller.present({"type": "reasoning", "text": "  Select column D  "})
    await controller.present({"type": "action", "name": "type", "arguments": {"text": "=SUM(D2:D9)"}})
    await controller.present({"type": "action_done", "call_id": "c1", "refused": True})
    await controller.present({"type": "shell", "event": ShellPresentationEvent("t1", "pwd", False, "running")})
    await controller.present({"type": "shell", "event": ShellPresentationEvent("t1", "pwd", False, "completed", 0)})
    await controller.present({"type": "action_done", "call_id": "c1"})
    await controller.present({"type": "final", "text": "The total is 48,912."})

    entries = [command["entry"] for command in _of_op(commands, "transcript")]
    assert [entry["kind"] for entry in entries] == [
        "task",
        "system",
        "thinking",
        "action",
        "error",
        "shell",
        "shell",
        "final",
    ]
    assert entries[2]["text"] == "Select column D"
    assert (entries[3]["text"], entries[3]["icon"]) == ("type =SUM(D2:D9)", "type")
    # One card per command, revised in place: both lifecycle events carry the same id.
    assert entries[5]["id"] == entries[6]["id"] == "shell-t1"
    assert (entries[6]["state"], entries[6]["exitCode"]) == ("completed", 0)
    # Every other row is a new one, so nothing overwrites the step above it.
    assert len({entry["id"] for entry in entries}) == len(entries) - 1
    assert entries[-1] == {"id": entries[-1]["id"], "kind": "final", "text": "The total is 48,912."}


async def test_status_mode_shows_shell_commands_on_the_desktop_rail_and_counts_them(monkeypatch):
    controller = _status_controller()
    commands = _status_traffic(monkeypatch, controller)

    await controller.present({"type": "shell", "event": ShellPresentationEvent("t1", "sleep 30", True, "running")})
    await controller.present({"type": "shell", "event": ShellPresentationEvent("t2", "pwd", False, "running")})

    rail = _of_op(commands, "shellCommands")
    assert [entry["task_id"] for entry in rail[-1]["commands"]] == ["t2", "t1"]
    assert rail[-1]["overflow"] == 0
    # A background run's commands are counted the same as a foreground run's.
    assert controller.background_counts == {"started": 1, "completed": 0, "failed": 0, "cancelled": 0}


def test_transcript_entries_skip_empty_text_and_clamp_a_runaway_block():
    from yutori.navigator.macos.presentation import _TRANSCRIPT_TEXT_MAX_CHARACTERS, _transcript_entry

    assert _transcript_entry({"type": "final", "text": "  "}, 0) is None
    assert _transcript_entry({"type": "action_done", "call_id": "c1"}, 0) is None
    assert _transcript_entry({"type": "shell", "event": None}, 0) is None
    assert _transcript_entry({"type": "reasoning", "text": "x" * 9000}, 3) == {
        "id": "entry-3",
        "kind": "thinking",
        "text": "x" * (_TRANSCRIPT_TEXT_MAX_CHARACTERS - 1) + "\u2026",
    }


async def test_status_mode_thumbnail_rejection_degrades_fail_soft(monkeypatch):
    controller = _status_controller()

    async def send_command(_command, **_kwargs):
        return {"ok": True, "state": "stale"}

    async def restore() -> str:
        return "hidden"

    monkeypatch.setattr(controller, "_send_command", send_command)
    controller._restore_native_cursor = restore
    assert await controller.show_thumbnail(b"jpeg-bytes") is False
    assert not controller.status.available
    assert controller.status.degradation_reason == "thumbnail_failed"


def test_status_line_truncates_long_captions():
    from yutori.navigator.macos.presentation import _status_line

    assert _status_line({"type": "reasoning", "text": "x" * 200}) == "Thinking: " + "x" * 79 + "\u2026"
    assert _status_line({"type": "action_done"}) is None
    assert _status_line({"type": "status", "text": "   "}) is None


async def test_send_operation_skips_unchanged_dedupe_eligible_renders_but_not_others(monkeypatch):
    controller = _active_controller()
    envelopes: list[dict] = []

    async def send_envelope(envelope, **_kwargs):
        envelopes.append(envelope)
        return {"ok": True}

    monkeypatch.setattr(controller, "_send_envelope", send_envelope)

    first = await controller._send_operation({"op": "showThought", "markdown": "a"})
    repeat = await controller._send_operation({"op": "showThought", "markdown": "a"})
    changed = await controller._send_operation({"op": "showThought", "markdown": "b"})
    # "mount" is not in the dedupe-eligible op set, so it is always sent even when unchanged.
    await controller._send_operation({"op": "mount", "snapshot": {}})
    await controller._send_operation({"op": "mount", "snapshot": {}})

    assert first == {"ok": True}
    assert repeat == {"ok": True}
    assert changed == {"ok": True}
    assert envelopes == [
        {"operation": {"op": "showThought", "markdown": "a"}},
        {"operation": {"op": "showThought", "markdown": "b"}},
        {"operation": {"op": "mount", "snapshot": {}}},
        {"operation": {"op": "mount", "snapshot": {}}},
    ]


async def test_status_mode_preview_demand_events_drive_the_owner_callback():
    controller = _status_controller()
    demands: list[bool] = []
    controller.on_preview_demand = demands.append
    stream = asyncio.StreamReader()
    for event in (
        {"event": "previewDemand", "menuOpen": True, "activityOpen": False},
        {"event": "previewDemand", "menuOpen": False, "activityOpen": False},
        {"event": "previewDemand", "menuOpen": False, "activityOpen": True},
    ):
        stream.feed_data(json.dumps(event).encode() + b"\n")
    stream.feed_eof()
    controller._process = SimpleNamespace(stdout=stream, stderr=None, returncode=None)
    controller._stopping = True  # an EOF while stopping is not a host failure
    await controller._read_host()
    assert demands == [True, False, True]
    assert controller.preview_demand is True
    assert [event["active"] for event in controller.telemetry if event["type"] == "preview_demand"] == [
        True,
        False,
        True,
    ]


async def test_status_mode_preview_frames_reach_the_host_and_never_degrade(monkeypatch):
    controller = _status_controller()
    commands: list[dict] = []
    replies = iter([{"ok": True, "state": "shown"}, {"ok": True, "state": "stale"}])

    async def send_command(command, **_kwargs):
        commands.append(command)
        return next(replies)

    monkeypatch.setattr(controller, "_send_command", send_command)
    assert await controller.show_preview_frame(b"jpeg-bytes") is True
    assert await controller.show_preview_frame(b"jpeg-bytes") is False
    assert controller.status.available

    async def failing(_command, **_kwargs):
        raise MacOSPresentationError("Overlay host is not running.")

    monkeypatch.setattr(controller, "_send_command", failing)
    assert await controller.show_preview_frame(b"jpeg-bytes") is False
    assert controller.status.available
    assert [command["op"] for command in commands] == ["previewFrame", "previewFrame"]
    assert base64.b64decode(commands[0]["data"]) == b"jpeg-bytes"


async def test_overlay_mode_has_no_preview_frames():
    controller = MacOSPresentationController(native_width=100, native_height=100)
    assert await controller.show_preview_frame(b"jpeg-bytes") is False
