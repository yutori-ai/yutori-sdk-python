"""Tests for protocol-v2 presentation mapping and degradation."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from yutori.navigator.macos.presentation import MacOSPresentationController, MacOSPresentationError
from yutori.navigator.macos.types import (
    MacOSPresentationCapabilities,
    MacOSPresentationStatus,
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
