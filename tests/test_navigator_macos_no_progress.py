"""Tests for telemetry-only macOS no-progress detection and cancellation."""

from __future__ import annotations

import asyncio
import base64
import io

from PIL import Image

from yutori.navigator.macos.no_progress import NoProgressWatchdog, action_signature
from yutori.navigator.macos.types import CancellationLatch, N2Observation


def _frame(color: int, capture_id: int) -> N2Observation:
    output = io.BytesIO()
    Image.new("RGB", (160, 100), (color, color, color)).save(output, format="PNG")
    return N2Observation(capture_id, 160, 100, 160, 100, "image/png", output.getvalue())


def test_action_signature_never_retains_text_or_command_contents():
    signature = action_signature(
        "computer_batch",
        {
            "actions": [
                {"name": "type", "arguments": {"text": "private text"}},
                {"name": "bash", "arguments": {"command": "echo secret"}},
            ]
        },
    )
    assert "private text" not in signature
    assert "echo secret" not in signature
    assert "text-short" in signature


def test_period_one_and_two_cycles_trigger_within_six_samples():
    period_one = NoProgressWatchdog()
    period_one.record_frame(_frame(20, 0))
    for capture_id in range(1, 4):
        period_one.record_action("left_click", {"coordinates": [500, 500]})
        period_one.record_frame(_frame(20, capture_id))
    assert period_one.triggers == 1

    period_two = NoProgressWatchdog()
    period_two.record_frame(_frame(20, 0))
    for capture_id in range(1, 7):
        action = "left_click" if capture_id % 2 else "key_press"
        period_two.record_action(action, {"coordinates": [500, 500]})
        period_two.record_frame(_frame(20 if capture_id % 2 else 21, capture_id))
    assert period_two.triggers == 1


def test_wait_and_background_shell_reset_cycle_detection():
    watchdog = NoProgressWatchdog()
    watchdog.record_frame(_frame(20, 0))
    for capture_id in range(1, 3):
        watchdog.record_action("left_click", {"coordinates": [500, 500]})
        watchdog.record_frame(_frame(20, capture_id))
    watchdog.record_action("wait", {"duration": 1})
    watchdog.record_action("bash", {"command": "sleep 1", "run_in_background": True})
    watchdog.record_frame(_frame(20, 3))
    assert watchdog.triggers == 0


async def test_cancellation_latch_uses_same_tick_priority_and_then_stays_latched():
    latch = CancellationLatch()
    latch.request("model_request")
    latch.request("deadline")
    latch.request("operator_stop")
    await asyncio.sleep(0)
    assert await latch.wait() == "operator_stop"
    latch.request("target_crash")
    assert latch.cause == "operator_stop"


def test_observation_base64_and_data_url_are_consistent():
    observation = _frame(20, 1)
    assert base64.b64decode(observation.base64) == observation.encoded_bytes
    assert observation.data_url == f"data:image/png;base64,{observation.base64}"
