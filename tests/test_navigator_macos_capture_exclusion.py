"""Tests for keeping Yutori's drawing out of desktop captures: the capture opt-out and its probe."""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw

from yutori.navigator.macos import computer as computer_module
from yutori.navigator.macos.computer import MacOSComputer
from yutori.navigator.macos.presentation import MacOSPresentationController, _probe_verdict, _valid_probe
from yutori.navigator.macos.types import MacOSPresentationCapabilities, MacOSPresentationStatus

# The host's reply for a 1000x600-point overlay: a 32-point probe inset 16 points from the bottom-right,
# and what `_valid_probe` makes of it.
PROBE_REPLY = {"x": 952, "y": 552, "size": 32, "cells": 4}
PROBE = {"x": 952.0, "y": 552.0, "size": 32.0, "cells": 4.0}
SCALE = (2.0, 2.0)
MAGENTA = (255, 0, 255)
GREEN = (0, 255, 0)


def _frame(
    with_probe: bool,
    *,
    covered_rows: int = 0,
    magenta: tuple[int, int, int] = MAGENTA,
    green: tuple[int, int, int] = GREEN,
    size: tuple[int, int] = (2000, 1200),
) -> bytes:
    """A desktop frame at backing scale 2, with the probe's checkerboard drawn where the host put it."""
    image = Image.new("RGB", size, (40, 44, 52))
    if with_probe:
        draw = ImageDraw.Draw(image)
        cell = 16
        for row in range(covered_rows, 4):
            for column in range(4):
                x0, y0 = 1904 + column * cell, 1104 + row * cell
                color = magenta if (row + column) % 2 == 0 else green
                draw.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _controller(**kwargs) -> MacOSPresentationController:
    controller = MacOSPresentationController(native_width=2000, native_height=1200, **kwargs)
    capabilities = MacOSPresentationCapabilities(2, 1000, 600, 2.0, True, (850, 10, 140, 50))
    controller._status = MacOSPresentationStatus(True, True, "active", "yutori", capabilities)
    controller._viewport = (1000, 600)
    return controller


def _frame_reply(frame: bytes, **overrides) -> dict:
    """The host's `captureDesktop` reply for a frame."""
    with Image.open(io.BytesIO(frame)) as image:
        width, height = image.size
    return {"frame": {"data": base64.b64encode(frame).decode("ascii"), "width": width, "height": height, **overrides}}


class _Host:
    """A fake overlay host that records commands and answers the probe, the hide/reveal pair, and captures."""

    def __init__(self, probe=PROBE_REPLY, show_state: str = "shown", desktop=None) -> None:
        self.commands: list[dict] = []
        self.probe = probe
        self.show_state = show_state
        # What `captureDesktop` answers: a reply dict, or an exception to raise.
        self.desktop = desktop if desktop is not None else _frame_reply(_frame(False))

    async def __call__(self, command, **_kwargs):
        self.commands.append(command)
        if command["op"] == "captureProbe":
            if command["phase"] == "show":
                return {"state": self.show_state, "probe": self.probe}
            return {"state": "hidden"}
        if command["op"] == "captureDesktop":
            if isinstance(self.desktop, Exception):
                raise self.desktop
            return self.desktop
        if command["op"] == "captureHide":
            return {"capture_id": command["capture_id"], "state": "hidden"}
        if command["op"] == "captureReveal":
            return {"capture_id": command["capture_id"], "state": "visible"}
        return {}

    @property
    def ops(self) -> list[tuple[str, str | None]]:
        return [(command["op"], command.get("phase")) for command in self.commands]


async def _capture_of(frame: bytes):
    async def capture():
        with Image.open(io.BytesIO(frame)) as image:
            width, height = image.size
        return frame, width, height

    return capture


def test_probe_verdict_finds_the_checkerboard_and_tolerates_the_display_profile():
    assert _probe_verdict(_frame(True), PROBE, SCALE) == ("leaked", 16)
    # What a Display P3 panel made of the sRGB probe in the live check.
    assert _probe_verdict(_frame(True, magenta=(236, 52, 248), green=(60, 240, 80)), PROBE, SCALE) == ("leaked", 16)
    assert _probe_verdict(_frame(False), PROBE, SCALE) == ("excluded", 0)
    # A mostly covered probe still counts as captured: one row is four cells, the threshold.
    assert _probe_verdict(_frame(True, covered_rows=3), PROBE, SCALE) == ("leaked", 4)
    # Cells off the frame are skipped rather than crashing.
    assert _probe_verdict(_frame(False, size=(1900, 1100)), PROBE, SCALE) == ("excluded", 0)


def test_valid_probe_requires_finite_geometry_and_at_least_two_cells():
    assert _valid_probe(PROBE_REPLY) == PROBE
    assert _valid_probe({"x": 952.5, "y": 552.0, "size": 32.0, "cells": 4}) == {**PROBE, "x": 952.5}
    for invalid in (
        None,
        {"x": -1.0, "y": 552.0, "size": 32.0, "cells": 4},
        {"x": 952.0, "y": 552.0, "size": 0.0, "cells": 4},
        {"x": 952.0, "y": 552.0, "size": 32.0, "cells": 1},
        {"x": 952.0, "y": 552.0, "size": 32.0, "cells": 4.0},
        {"x": True, "y": 552.0, "size": 32.0, "cells": 4},
        {"x": float("nan"), "y": 552.0, "size": 32.0, "cells": 4},
        {"x": 952.0, "y": 552.0, "cells": 4},
    ):
        assert _valid_probe(invalid) is None, invalid


async def test_verified_exclusion_skips_the_capture_hide(monkeypatch):
    controller = _controller()
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)

    assert await controller.verify_capture_exclusion(await _capture_of(_frame(False))) == "excluded"

    assert controller.capture_exclusion == "excluded"
    assert host.ops == [("captureProbe", "show"), ("captureProbe", "hide")]
    assert await controller.before_capture(1) is False
    assert ("captureHide", None) not in host.ops
    assert controller.status.available and controller.status.state == "active"
    assert controller.capture_source == "driver"
    assert await controller.capture_desktop() is None
    assert ("captureDesktop", None) not in host.ops
    assert controller.telemetry[-1] == {
        "type": "capture_exclusion",
        "mechanism": "sharing",
        "state": "excluded",
        "matches": 0,
        "error_type": None,
    }


async def test_a_captured_probe_keeps_hiding_the_overlay_for_every_capture(monkeypatch):
    controller = _controller()
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)

    assert await controller.verify_capture_exclusion(await _capture_of(_frame(True))) == "leaked"

    assert controller.capture_exclusion == "leaked"
    assert await controller.before_capture(1) is True
    assert await controller.after_capture(1, 2000, 1200) is True
    assert host.ops[-2:] == [("captureHide", None), ("captureReveal", None)]
    assert controller.telemetry[-1]["matches"] == 16


async def test_a_failing_probe_is_advisory_and_still_hides_the_probe_panel(monkeypatch):
    controller = _controller()
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)

    async def capture():
        raise RuntimeError("driver hiccup")

    assert await controller.verify_capture_exclusion(capture) == "unverifiable"

    assert host.ops == [("captureProbe", "show"), ("captureProbe", "hide")]
    assert controller.status.available and controller.status.degradation_reason is None
    assert controller.telemetry[-1] == {
        "type": "capture_exclusion",
        "mechanism": "sharing",
        "state": "unverifiable",
        "matches": None,
        "error_type": "RuntimeError",
    }
    # Unverified means the old path: every capture hides the overlay first.
    assert await controller.before_capture(1) is True


async def test_probe_replies_without_geometry_or_with_a_mismatched_frame_are_unverifiable(monkeypatch):
    controller = _controller()
    monkeypatch.setattr(controller, "_send_command", _Host(probe=None))
    assert await controller.verify_capture_exclusion(await _capture_of(_frame(False))) == "unverifiable"

    controller = _controller()
    monkeypatch.setattr(controller, "_send_command", _Host(show_state="stale"))
    assert await controller.verify_capture_exclusion(await _capture_of(_frame(False))) == "unverifiable"

    controller = _controller()
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)
    # A frame at the wrong Retina scale cannot be sampled at the probe's position.
    capture = await _capture_of(_frame(False, size=(1000, 600)))
    assert await controller.verify_capture_exclusion(capture) == "unverifiable"
    assert host.ops == [("captureProbe", "show"), ("captureProbe", "hide")]


async def test_status_mode_never_probes(monkeypatch):
    status = _controller(mode="status")
    host = _Host()
    monkeypatch.setattr(status, "_send_command", host)
    assert await status.verify_capture_exclusion(await _capture_of(_frame(False))) == "unverified"
    assert host.ops == []
    assert await status.capture_desktop() is None


async def _driver_capture_never_called():
    raise AssertionError("a recordable overlay probes through the host's capture, not the driver's")


async def test_a_recordable_overlay_verifies_the_host_filter_and_then_serves_the_frames(monkeypatch):
    controller = _controller(exclude_from_capture=False)
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)
    assert controller.capture_exclusion == "unverified" and controller.capture_source == "driver"

    assert await controller.verify_capture_exclusion(_driver_capture_never_called) == "excluded"

    assert host.ops == [("captureProbe", "show"), ("captureDesktop", None), ("captureProbe", "hide")]
    assert controller.capture_source == "overlay"
    assert controller.telemetry[-1] == {
        "type": "capture_exclusion",
        "mechanism": "filter",
        "state": "excluded",
        "matches": 0,
        "error_type": None,
    }
    # From here on the frames come from the host and nothing is hidden.
    frame = await controller.capture_desktop()
    assert frame is not None and frame[1:] == (2000, 1200) and frame[0] == _frame(False)
    assert await controller.before_capture(1) is False
    assert ("captureHide", None) not in host.ops


async def test_a_host_filter_that_leaks_the_probe_leaves_the_frames_to_the_driver(monkeypatch):
    controller = _controller(exclude_from_capture=False)
    host = _Host(desktop=_frame_reply(_frame(True)))
    monkeypatch.setattr(controller, "_send_command", host)

    assert await controller.verify_capture_exclusion(_driver_capture_never_called) == "leaked"

    assert controller.capture_source == "driver"
    assert controller.telemetry[-1]["mechanism"] == "filter" and controller.telemetry[-1]["matches"] == 16
    assert await controller.capture_desktop() is None
    assert await controller.before_capture(1) is True
    assert host.ops[-1] == ("captureHide", None)


async def test_a_failing_host_capture_hands_the_frames_back_to_the_driver_without_degrading(monkeypatch):
    controller = _controller(exclude_from_capture=False)
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)
    assert await controller.verify_capture_exclusion(_driver_capture_never_called) == "excluded"

    host.desktop = RuntimeError("screen recording permission revoked")
    assert await controller.capture_desktop() is None

    assert controller.capture_source == "driver"
    assert controller.capture_exclusion == "unverifiable"
    assert controller.status.available and controller.status.state == "active"
    assert controller.telemetry[-1] == {"type": "capture_source", "source": "driver", "error_type": "RuntimeError"}
    # Not asked again, and the old path is back: hide, capture through the driver, reveal.
    host.desktop = _frame_reply(_frame(False))
    assert await controller.capture_desktop() is None
    assert host.ops.count(("captureDesktop", None)) == 2
    assert await controller.before_capture(1) is True
    assert await controller.after_capture(1, 2000, 1200) is True


async def test_host_frames_of_another_shape_or_with_a_wrong_size_are_refused(monkeypatch):
    # A frame that is not the driver's shape would break the model's coordinates.
    controller = _controller(exclude_from_capture=False)
    host = _Host(desktop=_frame_reply(_frame(False, size=(1000, 600))))
    monkeypatch.setattr(controller, "_send_command", host)
    assert await controller.verify_capture_exclusion(_driver_capture_never_called) == "unverifiable"
    assert controller.telemetry[-1]["error_type"] == "MacOSPresentationError"
    assert controller.capture_source == "driver"

    for desktop in (
        _frame_reply(_frame(False), width=1999),
        {"frame": {"data": "", "width": 2000, "height": 1200}},
        {"frame": "nope"},
        {},
    ):
        controller = _controller(exclude_from_capture=False)
        host = _Host(desktop=desktop)
        monkeypatch.setattr(controller, "_send_command", host)
        assert await controller.verify_capture_exclusion(_driver_capture_never_called) == "unverifiable", desktop
        assert host.ops[-1] == ("captureProbe", "hide")


class _DesktopTransport:
    """The driver, reduced to a desktop frame and an OK for everything else."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def call_tool(self, name, arguments, *, read_only=False, timeout_seconds=None):
        del arguments, read_only, timeout_seconds
        self.calls.append(name)
        if name == "get_desktop_state":
            frame = _frame(False)
            return {
                "content": [{"type": "image", "data": base64.b64encode(frame).decode("ascii")}],
                "structuredContent": {"screenshot_width": 2000, "screenshot_height": 1200},
            }
        return {"structuredContent": {"ok": True}}


class _FakeController:
    """Records how the computer builds and verifies the overlay controller."""

    instances: list[_FakeController] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.captures: list[tuple[bytes, int, int]] = []
        self.capture_exclusion = "unverified"
        self.capture_source = "driver"
        # What `capture_desktop` answers while the source is the overlay; None hands back to the driver.
        self.host_frame: "tuple[bytes, int, int] | None" = (_frame(False), 2000, 1200)
        self.host_captures = 0
        self.status = MacOSPresentationStatus(True, True, "active", "yutori")
        self.telemetry: tuple[dict, ...] = ()
        self.hides = 0
        self.__class__.instances.append(self)

    async def start(self) -> None:
        pass

    async def verify_capture_exclusion(self, capture) -> str:
        if self.kwargs["exclude_from_capture"]:
            self.captures.append(await capture())
        else:
            self.capture_source = "overlay"
        self.capture_exclusion = "excluded"
        return self.capture_exclusion

    async def capture_desktop(self):
        if self.capture_source != "overlay":
            return None
        self.host_captures += 1
        if self.host_frame is None:
            self.capture_source = "driver"
            self.capture_exclusion = "unverifiable"
        return self.host_frame

    async def reveal(self) -> None:
        pass

    async def present(self, event) -> None:
        pass

    async def before_capture(self, _capture_id: int) -> bool:
        self.hides += 1
        return self.capture_exclusion != "excluded"

    async def after_capture(self, *_args) -> bool:
        return False

    async def encode_observation(self, _png: bytes):
        return None

    def blocking_surface(self, _point) -> "str | None":
        return None

    async def stop(self) -> None:
        pass


async def test_computer_verifies_exclusion_with_a_desktop_frame_before_revealing(monkeypatch):
    _FakeController.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeController)
    transport = _DesktopTransport()
    async with MacOSComputer(transport, owns_transport=False, presentation=True) as computer:
        controller = _FakeController.instances[-1]
        assert controller.kwargs["exclude_from_capture"] is True
        # The probe's frame is a second desktop capture, taken through the driver like any other.
        assert [(w, h) for _, w, h in controller.captures] == [(2000, 1200)]
        assert transport.calls.count("get_desktop_state") == 2
        assert computer.presentation is controller and controller.capture_exclusion == "excluded"
        await computer.screenshot()
        assert controller.hides == 1


async def test_a_recordable_overlay_serves_the_frames_and_the_driver_takes_over_when_it_cannot(monkeypatch):
    _FakeController.instances.clear()
    monkeypatch.setattr(computer_module, "MacOSPresentationController", _FakeController)
    transport = _DesktopTransport()
    async with MacOSComputer(
        transport, owns_transport=False, presentation=True, exclude_overlay_from_capture=False
    ) as computer:
        controller = _FakeController.instances[-1]
        assert controller.kwargs["exclude_from_capture"] is False
        # The first frame is the driver's, before the overlay exists; the probe did not need another.
        assert transport.calls.count("get_desktop_state") == 1 and controller.captures == []
        assert controller.capture_source == "overlay"

        observation = await computer.screenshot()
        assert (observation.native_width, observation.native_height) == (2000, 1200)
        assert controller.host_captures == 1 and transport.calls.count("get_desktop_state") == 1
        await computer.screenshot()
        assert controller.host_captures == 2 and transport.calls.count("get_desktop_state") == 1
        assert controller.hides == 0

        controller.host_frame = None
        await computer.screenshot()
        # The host could not: this frame and the next come from the driver, hidden around each.
        assert controller.host_captures == 3 and transport.calls.count("get_desktop_state") == 2
        assert controller.hides == 1 and controller.capture_source == "driver"
        await computer.screenshot()
        assert controller.host_captures == 3 and transport.calls.count("get_desktop_state") == 3
        assert controller.hides == 2


async def test_host_window_ids_ride_on_the_desktop_capture_command(monkeypatch):
    """A host application's panels are left out of the model's frame like the host's own windows."""
    controller = _controller(exclude_from_capture=False, exclude_capture_window_ids=(101, 202))
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)
    controller._capture_source = "overlay"
    frame = await controller.capture_desktop()
    assert frame is not None
    capture = next(command for command in host.commands if command["op"] == "captureDesktop")
    assert capture["excludeWindowIDs"] == [101, 202]


async def test_without_host_window_ids_the_capture_command_stays_bare(monkeypatch):
    controller = _controller(exclude_from_capture=False)
    host = _Host()
    monkeypatch.setattr(controller, "_send_command", host)
    controller._capture_source = "overlay"
    assert await controller.capture_desktop() is not None
    capture = next(command for command in host.commands if command["op"] == "captureDesktop")
    assert "excludeWindowIDs" not in capture
