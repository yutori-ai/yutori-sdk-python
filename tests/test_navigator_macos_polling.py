from __future__ import annotations

import asyncio
import base64
import io
import time

from PIL import Image

from yutori.navigator.macos.polling import (
    FRAME_POLL_ACTION_MAX_MS,
    frame_poll_interval_ms,
    frame_poll_wait_budget_ms,
    poll_until_frame_changes,
)
from yutori.navigator.macos.types import CancellationLatch


def _frame(color: tuple[int, int, int]) -> str:
    output = io.BytesIO()
    Image.new("RGB", (320, 200), color).save(output, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode()}"


def test_polling_policy_matches_the_desktop_reference():
    assert frame_poll_wait_budget_ms(0) == 5_000
    assert frame_poll_wait_budget_ms(3_000) == 9_000
    assert frame_poll_wait_budget_ms(100_000) == 15_000
    assert frame_poll_interval_ms(0, 20) == 150
    assert frame_poll_interval_ms(4_999, 900) == 500
    assert frame_poll_interval_ms(5_000, 900) == 900
    assert FRAME_POLL_ACTION_MAX_MS == 8_000


async def test_polling_returns_on_first_changed_frame():
    reference = _frame((0, 0, 0))
    changed = _frame((255, 255, 255))
    captures = 0

    async def capture():
        nonlocal captures
        captures += 1
        return changed

    result = await poll_until_frame_changes(
        capture=capture,
        reference=reference,
        first_frame=changed,
        mode="strict",
        budget_ms=8_000,
    )
    assert result.outcome == "changed"
    assert result.polls == 1
    assert captures == 0


async def test_polling_aborts_promptly_on_cancellation():
    frame = _frame((0, 0, 0))
    latch = CancellationLatch()

    async def capture():
        return frame

    polling = asyncio.create_task(
        poll_until_frame_changes(
            capture=capture,
            reference=frame,
            mode="strict",
            budget_ms=8_000,
            cancellation=latch,
        )
    )
    await asyncio.sleep(0)
    latch.request("operator_stop")
    await asyncio.sleep(0)
    result = await polling
    assert result.outcome == "aborted"
    assert result.waited_ms < 500


async def test_polling_uses_at_most_half_the_remaining_deadline():
    frame = _frame((0, 0, 0))

    async def capture():
        return frame

    result = await poll_until_frame_changes(
        capture=capture,
        reference=frame,
        mode="strict",
        budget_ms=8_000,
        deadline=time.monotonic() + 0.1,
        min_wait_ms=0,
    )
    assert result.outcome == "exhausted"
    assert result.waited_ms < 100
