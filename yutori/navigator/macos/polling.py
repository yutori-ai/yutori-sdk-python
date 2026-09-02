"""Adaptive desktop-frame polling shared by macOS computer actions and waits."""

from __future__ import annotations

import asyncio
import base64
import io
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from PIL import Image

from .process_lifecycle import race_sleep_against_cancellation
from .types import CancellationLatch, N2Observation

FRAME_SIGNATURE_WIDTH = 160
FRAME_SIGNATURE_HEIGHT = 100
FRAME_DIFF_PER_PIXEL_TOLERANCE = 10
FRAME_DIFF_TOLERANT_FRACTION = 0.005
FRAME_DIFF_STRICT_FRACTION = 0.0
FRAME_POLL_MIN_MS = 500
FRAME_POLL_INTERVAL_MS = 500
FRAME_POLL_SLOW_INTERVAL_MS = 1_000
FRAME_POLL_FAST_WINDOW_MS = 5_000
FRAME_POLL_MIN_INTERVAL_MS = 150
FRAME_POLL_WAIT_MULTIPLE = 3
FRAME_POLL_WAIT_MIN_BUDGET_MS = 5_000
FRAME_POLL_WAIT_MAX_BUDGET_MS = 15_000
FRAME_POLL_ACTION_MAX_MS = 8_000
FRAME_POLL_DEADLINE_FRACTION = 0.5

FrameDiffMode = Literal["strict", "tolerant"]
FramePollOutcome = Literal["changed", "exhausted", "aborted", "undiffable"]


@dataclass(frozen=True)
class FramePollResult:
    outcome: FramePollOutcome
    waited_ms: int
    polls: int
    capture_ms: int
    last_frame: "N2Observation | str | None"
    changed_fraction: "float | None"


def frame_poll_interval_ms(elapsed_ms: int, last_capture_ms: int) -> int:
    ceiling = FRAME_POLL_INTERVAL_MS if elapsed_ms < FRAME_POLL_FAST_WINDOW_MS else FRAME_POLL_SLOW_INTERVAL_MS
    return min(max(last_capture_ms, FRAME_POLL_MIN_INTERVAL_MS), ceiling)


def frame_poll_wait_budget_ms(requested_ms: int) -> int:
    scaled = max(0, requested_ms) * FRAME_POLL_WAIT_MULTIPLE
    return min(max(scaled, FRAME_POLL_WAIT_MIN_BUDGET_MS), FRAME_POLL_WAIT_MAX_BUDGET_MS)


def _image_bytes(frame: "N2Observation | str") -> bytes:
    if isinstance(frame, N2Observation):
        return frame.encoded_bytes
    payload = frame.split(",", 1)[1] if frame.startswith("data:") and "," in frame else frame
    return base64.b64decode(payload)


def frame_signature(frame: "N2Observation | str") -> "bytes | None":
    try:
        with Image.open(io.BytesIO(_image_bytes(frame))) as source:
            image = source.convert("L").resize(
                (FRAME_SIGNATURE_WIDTH, FRAME_SIGNATURE_HEIGHT),
                Image.Resampling.LANCZOS,
            )
            return image.tobytes()
    except (OSError, ValueError, TypeError):
        return None


def frame_difference(left: bytes, right: bytes, *, pixel_tolerance: int = FRAME_DIFF_PER_PIXEL_TOLERANCE) -> float:
    if not left or len(left) != len(right):
        return 1.0
    changed = sum(abs(first - second) > pixel_tolerance for first, second in zip(left, right))
    return changed / len(left)


async def _sleep_or_cancel(delay: float, cancellation: "CancellationLatch | None") -> bool:
    if delay <= 0:
        return bool(cancellation and cancellation.cancelled)
    if cancellation is None:
        await asyncio.sleep(delay)
        return False
    _sleeper, cancelled, done = await race_sleep_against_cancellation(delay, cancellation)
    return cancelled in done


async def poll_until_frame_changes(
    *,
    capture: Callable[[], Awaitable["N2Observation | str"]],
    reference: "N2Observation | str",
    mode: FrameDiffMode,
    budget_ms: int,
    deadline: "float | None" = None,
    cancellation: "CancellationLatch | None" = None,
    first_frame: "N2Observation | str | None" = None,
    min_wait_ms: int = FRAME_POLL_MIN_MS,
) -> FramePollResult:
    """Poll until a material change, bounded by an action budget and deadline share."""
    started_at = time.monotonic()
    reference_signature = frame_signature(reference)
    last_frame: "N2Observation | str | None" = None
    polls = 0
    capture_ms = 0
    last_capture_ms = 0

    def result(outcome: FramePollOutcome, changed_fraction: "float | None" = None) -> FramePollResult:
        return FramePollResult(
            outcome=outcome,
            waited_ms=round((time.monotonic() - started_at) * 1000),
            polls=polls,
            capture_ms=capture_ms,
            last_frame=last_frame,
            changed_fraction=changed_fraction,
        )

    if reference_signature is None:
        return result("undiffable")

    ends_at = started_at + max(0, budget_ms) / 1000
    if deadline is not None:
        remaining_share = max(0.0, deadline - started_at) * FRAME_POLL_DEADLINE_FRACTION
        ends_at = min(ends_at, started_at + remaining_share)

    pending = first_frame
    if pending is None and await _sleep_or_cancel(
        min(min_wait_ms / 1000, max(0.0, ends_at - started_at)), cancellation
    ):
        return result("aborted")

    threshold = FRAME_DIFF_TOLERANT_FRACTION if mode == "tolerant" else FRAME_DIFF_STRICT_FRACTION
    while True:
        if cancellation and cancellation.cancelled:
            return result("aborted")
        if pending is not None:
            frame, pending = pending, None
        else:
            capture_started_at = time.monotonic()
            try:
                frame = await capture()
            except Exception:  # noqa: BLE001 - polling is an optional optimization
                return result("undiffable")
            last_capture_ms = round((time.monotonic() - capture_started_at) * 1000)
            capture_ms += last_capture_ms
        last_frame = frame
        polls += 1
        candidate = frame_signature(frame)
        if candidate is None:
            return result("undiffable")
        changed_fraction = frame_difference(reference_signature, candidate)
        if changed_fraction > threshold:
            return result("changed", changed_fraction)
        now = time.monotonic()
        if now >= ends_at:
            return result("exhausted", changed_fraction)
        interval = min(frame_poll_interval_ms(round((now - started_at) * 1000), last_capture_ms) / 1000, ends_at - now)
        if await _sleep_or_cancel(interval, cancellation):
            return result("aborted", changed_fraction)
