"""Live view of the driven window for window-scope runs.

The status item shows the model's frames, which arrive only when the model looks. While the
user has the menu open or the activity window shown, this streamer captures the driven
window itself, over its own driver connection so the model's actions and captures never queue
behind it, and hands each frame to the presentation. cua-driver already holds the Screen
Recording grant, so no new permission is involved; the driver's per-frame cost bounds the rate
at roughly two frames per second.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from PIL import Image

from .transport import CuaDriverConnectionError, CuaDriverToolError, CuaDriverTransport, inline_image_data
from .types import CancellationLatch, MacOSWindowTarget

PREVIEW_INTERVAL_SECONDS = 0.5
PREVIEW_RETRY_SECONDS = 1.0
PREVIEW_LONG_SIDE = 960
PREVIEW_QUALITY = 70

FrameSink = Callable[[bytes], Awaitable[bool]]
TargetSource = Callable[[], "MacOSWindowTarget | None"]


def inline_image(result: dict[str, Any]) -> "bytes | None":
    """The inline base64 image part of a driver capture result, decoded, or None."""
    data = inline_image_data(result)
    if data is None:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except ValueError:
        return None


def encode_preview(png_bytes: bytes, *, long_side: int = PREVIEW_LONG_SIDE, quality: int = PREVIEW_QUALITY) -> bytes:
    """Downscale one window frame to a JPEG small enough to push to the host twice a second."""
    with Image.open(io.BytesIO(png_bytes)) as source:
        image = source.convert("RGB")
        image.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality)
        return output.getvalue()


class WindowPreviewStreamer:
    """Streams frames of the target window to a sink while someone is looking."""

    def __init__(
        self,
        *,
        target: TargetSource,
        sink: FrameSink,
        transport_factory: Callable[[], CuaDriverTransport] = CuaDriverTransport,
        cancellation: "CancellationLatch | None" = None,
        interval_seconds: float = PREVIEW_INTERVAL_SECONDS,
        retry_seconds: float = PREVIEW_RETRY_SECONDS,
    ) -> None:
        self._target = target
        self._sink = sink
        self._transport_factory = transport_factory
        self._cancellation = cancellation
        self._interval = interval_seconds
        self._retry = retry_seconds
        self._active = False
        self._closed = False
        self._task: "asyncio.Task[None] | None" = None
        self._frames_sent = 0
        self._failures = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def frames_sent(self) -> int:
        return self._frames_sent

    @property
    def failures(self) -> int:
        return self._failures

    def set_active(self, active: bool) -> None:
        """Start streaming (a fresh driver connection per streaming period) or let the loop wind down."""
        if self._closed:
            return
        self._active = active
        if active and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        self._closed = True
        self._active = False
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _cancelled(self) -> bool:
        return self._cancellation is not None and self._cancellation.cancelled

    async def _run(self) -> None:
        transport = self._transport_factory()
        try:
            await transport.start()
            while self._active and not self._cancelled():
                started = time.monotonic()
                target = self._target()
                if target is None:
                    await asyncio.sleep(self._retry)
                    continue
                try:
                    result = await transport.call_tool(
                        "get_window_state",
                        {
                            "pid": target.pid,
                            "window_id": target.window_id,
                            "include_screenshot": True,
                            "max_elements": 1,
                            "max_depth": 1,
                        },
                        read_only=True,
                    )
                except CuaDriverConnectionError:
                    # The dedicated connection is gone; the next demand starts a fresh one.
                    self._failures += 1
                    return
                except CuaDriverToolError:
                    # Window gone or off-Space for the moment; the owner may rebind shortly.
                    self._failures += 1
                    await asyncio.sleep(self._retry)
                    continue
                png = inline_image(result)
                if png is not None:
                    try:
                        frame = await asyncio.to_thread(encode_preview, png)
                    except (OSError, ValueError):
                        frame = None
                    if frame is not None and await self._sink(frame):
                        self._frames_sent += 1
                remaining = self._interval - (time.monotonic() - started)
                if remaining > 0:
                    await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the live view is advisory and must never take the run down
            self._failures += 1
        finally:
            self._active = False
            with suppress(Exception):
                await transport.close()
