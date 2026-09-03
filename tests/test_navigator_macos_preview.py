"""Tests for the live-view streamer behind the status item."""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any

from PIL import Image

import yutori.navigator.macos.preview as preview
from yutori.navigator.macos.preview import WindowPreviewStreamer, encode_preview, inline_image
from yutori.navigator.macos.transport import CuaDriverConnectionError, CuaDriverToolError
from yutori.navigator.macos.types import MacOSWindowTarget


def _png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (40, 50, 60)).save(output, format="PNG")
    return output.getvalue()


class FakePreviewTransport:
    instances: list[FakePreviewTransport] = []

    def __init__(self, frame: bytes = _png(400, 300), errors: list[Exception] | None = None):
        self.frame = frame
        self.errors = list(errors or [])
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def call_tool(self, name, arguments, *, read_only=False, timeout_seconds=None):
        del timeout_seconds
        self.calls.append((name, arguments, read_only))
        if self.errors:
            raise self.errors.pop(0)
        return {"content": [{"type": "image", "data": base64.b64encode(self.frame).decode("ascii")}]}


def _sink(frames: list[bytes], accept: bool = True):
    async def sink(frame: bytes) -> bool:
        frames.append(frame)
        return accept

    return sink


async def _settle(condition, attempts: int = 200) -> None:
    for _ in range(attempts):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition not met")


def test_encode_preview_downscales_to_a_jpeg_and_inline_image_decodes_the_capture_result():
    frame = encode_preview(_png(2400, 1200), long_side=960)
    with Image.open(io.BytesIO(frame)) as image:
        assert image.format == "JPEG" and image.size == (960, 480)
    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    assert (
        inline_image({"content": [{"type": "text", "text": "x"}, {"type": "image", "data": encoded}]}) == b"png-bytes"
    )
    assert inline_image({"content": []}) is None
    assert inline_image({"content": [{"type": "image", "data": "not base64!"}]}) is None


async def test_streamer_streams_window_frames_to_the_sink_only_while_active():
    FakePreviewTransport.instances.clear()
    frames: list[bytes] = []
    target = MacOSWindowTarget(4242, 7)
    streamer = WindowPreviewStreamer(
        target=lambda: target,
        sink=_sink(frames),
        transport_factory=FakePreviewTransport,
        interval_seconds=0,
        retry_seconds=0,
    )
    assert not streamer.active
    streamer.set_active(True)
    await _settle(lambda: len(frames) >= 3)
    streamer.set_active(False)
    (transport,) = FakePreviewTransport.instances
    await _settle(lambda: transport.closed)  # the loop winds down and releases its connection
    assert not streamer.active and transport.started
    assert transport.calls[0] == (
        "get_window_state",
        {"pid": 4242, "window_id": 7, "include_screenshot": True, "max_elements": 1, "max_depth": 1},
        True,
    )
    assert streamer.frames_sent >= 3
    with Image.open(io.BytesIO(frames[0])) as image:
        assert image.format == "JPEG" and image.size == (400, 300)
    await streamer.aclose()
    streamer.set_active(True)  # closed streamers stay closed
    assert not streamer.active


async def test_streamer_waits_for_a_target_and_survives_a_window_loss():
    FakePreviewTransport.instances.clear()
    frames: list[bytes] = []
    targets: list[MacOSWindowTarget | None] = [None]
    transport = FakePreviewTransport(errors=[CuaDriverToolError("window_id_not_found")])
    streamer = WindowPreviewStreamer(
        target=lambda: targets[0],
        sink=_sink(frames),
        transport_factory=lambda: transport,
        interval_seconds=0,
        retry_seconds=0,
    )
    streamer.set_active(True)
    await asyncio.sleep(0.01)
    assert not transport.calls  # nothing to capture until a window is bound
    targets[0] = MacOSWindowTarget(4242, 9)
    await _settle(lambda: len(frames) >= 2)
    assert streamer.failures == 1 and streamer.active
    await streamer.aclose()
    assert not streamer.active and transport.closed


async def test_streamer_stops_on_connection_loss_and_restarts_on_the_next_demand():
    FakePreviewTransport.instances.clear()
    frames: list[bytes] = []
    transports = iter([FakePreviewTransport(errors=[CuaDriverConnectionError("gone")]), FakePreviewTransport()])
    streamer = WindowPreviewStreamer(
        target=lambda: MacOSWindowTarget(4242, 7),
        sink=_sink(frames),
        transport_factory=lambda: next(transports),
        interval_seconds=0,
        retry_seconds=0,
    )
    streamer.set_active(True)
    await _settle(lambda: not streamer.active)
    assert streamer.failures == 1 and not frames
    assert FakePreviewTransport.instances[0].closed
    streamer.set_active(True)
    await _settle(lambda: len(frames) >= 1)
    assert len(FakePreviewTransport.instances) == 2
    await streamer.aclose()


async def test_streamer_rejected_frames_are_not_counted_and_cancellation_stops_it():
    frames: list[bytes] = []
    cancellation = preview.CancellationLatch()
    streamer = WindowPreviewStreamer(
        target=lambda: MacOSWindowTarget(4242, 7),
        sink=_sink(frames, accept=False),
        transport_factory=FakePreviewTransport,
        cancellation=cancellation,
        interval_seconds=0,
        retry_seconds=0,
    )
    streamer.set_active(True)
    await _settle(lambda: len(frames) >= 2)
    assert streamer.frames_sent == 0
    cancellation.request("operator_stop")
    await asyncio.sleep(0)
    await _settle(lambda: not streamer.active)
    await streamer.aclose()
