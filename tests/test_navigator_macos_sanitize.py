from __future__ import annotations

from yutori.navigator.macos import (
    OUTPUT_PREVIEW_MAX_LINE_CHARACTERS,
    CancellationLatch,
    sanitize_command_preview,
    sanitize_output_preview,
)


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


def test_output_preview_keeps_the_last_lines_not_the_first():
    feed = "\n".join(f"line {index}" for index in range(1, 41))

    # The opposite end from a command preview, because this is a feed: a
    # head-truncated one freezes on its first screenful for the rest of the run.
    assert sanitize_output_preview(feed, max_lines=2) == "…line 39\nline 40"
    assert sanitize_output_preview(feed) == "…line 37\nline 38\nline 39\nline 40"


def test_output_preview_caps_each_line_to_one_rendered_row():
    """The card wraps, so a long line would eat rows the newest lines need."""
    preview = sanitize_output_preview("short\n" + "y" * 300, max_lines=2, max_line_characters=10)

    assert preview == "…short\nyyyyyyyyy…"
    assert all(len(line) <= 10 for line in preview.lstrip("…").split("\n"))


def test_output_preview_keeps_newlines_unlike_a_command_preview():
    assert sanitize_output_preview("a\nb", max_lines=4) == "a\nb"
    assert sanitize_command_preview("a\nb") == "a b"


def test_output_preview_does_not_spend_a_line_on_a_trailing_newline():
    assert sanitize_output_preview("only\n", max_lines=2) == "only"


def test_output_preview_redacts_what_a_command_printed():
    printed = "AWS_SECRET_ACCESS_KEY=abcd1234abcd1234\nAuthorization: Bearer tok-abcdefghijklmnop"

    preview = sanitize_output_preview(printed, max_lines=4)

    assert "abcd1234abcd1234" not in preview
    assert "tok-abcdefghijklmnop" not in preview
    assert preview.count("[REDACTED]") == 2


def test_output_preview_redacts_known_secrets_a_command_echoed():
    secret = "short-active-key"

    assert secret not in sanitize_output_preview(f"value is {secret}", known_secrets=secret)


def test_output_preview_caps_one_enormous_line():
    preview = sanitize_output_preview("z" * 5_000)

    # Bounded by the per-line row budget, which bites long before the total.
    assert preview.startswith("…")
    assert len(preview) == OUTPUT_PREVIEW_MAX_LINE_CHARACTERS + 1


def test_output_preview_expands_tabs_and_normalizes_carriage_returns():
    assert sanitize_output_preview("a\tb", max_lines=2) == "a   b"
    assert sanitize_output_preview("a\r\nb", max_lines=2) == "a\nb"


def test_output_preview_redacts_before_clipping_so_a_straddling_secret_cannot_leak():
    """Redaction must precede every cut, or a secret sliced by one survives.

    The per-line cap is the dangerous one: it slices mid-token, and the surviving
    prefix no longer matches the pattern that would have removed it.
    """
    secret = "supersecretvalue123"
    line = "fetched with " + "a" * 25 + " " + secret

    preview = sanitize_output_preview(line, known_secrets=secret, max_lines=2, max_line_characters=45)

    assert not any(secret[:length] in preview for length in range(6, len(secret) + 1))
    assert "REDA" in preview
