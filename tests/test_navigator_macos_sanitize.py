from __future__ import annotations

from yutori.navigator.macos import CancellationLatch, sanitize_command_preview


def test_command_preview_normalizes_bounds_and_redacts_secret_sources():
    preview = sanitize_command_preview(
        "export API_KEY=visible\n curl --token second -H 'Authorization: Bearer third' "
        "https://example.test?key=yt-abcdefghijklmnopqrstuvwxyz",
        known_secrets=["second"],
        environment={"SERVICE_PASSWORD": "third"},
        max_characters=120,
    )
    assert "\n" not in preview
    assert "visible" not in preview
    assert "second" not in preview
    assert "third" not in preview
    assert "yt-abcdefghijklmnopqrstuvwxyz" not in preview
    assert len(preview) <= 120


async def test_cancellation_latch_uses_same_tick_priority_and_first_cause_wins():
    latch = CancellationLatch()
    latch.request("transport_failure")
    latch.request("deadline")
    latch.request("operator_stop")
    assert await latch.wait() == "operator_stop"
    latch.request("target_crash")
    assert latch.cause == "operator_stop"


def test_command_preview_treats_a_string_secret_as_one_value():
    secret = "short-active-key"
    assert sanitize_command_preview(f"echo {secret}", known_secrets=secret) == "echo [REDACTED]"
