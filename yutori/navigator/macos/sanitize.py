"""Sanitize command identity before presentation, diagnostics, or telemetry."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

COMMAND_PREVIEW_MAX_CHARACTERS = 160
# Sized for the renderer's run-command card, whose output block is clipped to
# `outputLineHeight * OUTPUT_PREVIEW_MAX_LINES` with `overflow: hidden`. Sending more
# than fits is not merely wasteful: the clip keeps the TOP of what it is handed, so
# the overflow that gets hidden is the NEWEST output — the opposite of what a feed
# should drop.
# MUST equal the vendored renderer's output-block row count. They move together or
# the newest output is what gets hidden: the block clips from the bottom, so sending
# more lines than the renderer shows silently drops the tail — the opposite end from
# the one a feed should lose. The bundled renderer
# (`yutori-navigator-overlay-runtime==0.5.0`, see `assets/provenance.json`) clips at
# two, so this is two. Raising it means releasing a renderer that shows more rows and
# re-vendoring it here in the same change.
OUTPUT_PREVIEW_MAX_LINES = 2
# The card wraps (`white-space: pre-wrap; overflow-wrap: anywhere`), so the budget is
# RENDERED rows, not logical lines: one long line silently eats several rows and
# pushes the newest lines out of the clip. Each line is therefore capped to one row.
# The card is monospace at `outputFontSize` 9 in `terminal.width` 314 less
# `paddingX` 13 a side — 288 units at ~0.6em advance is ~53 columns, taken slightly
# under so a wide glyph cannot spill a line onto a second row.
OUTPUT_PREVIEW_MAX_LINE_CHARACTERS = 50
OUTPUT_PREVIEW_MAX_CHARACTERS = (
    OUTPUT_PREVIEW_MAX_LINES * OUTPUT_PREVIEW_MAX_LINE_CHARACTERS + OUTPUT_PREVIEW_MAX_LINES - 1
)
REDACTION = "[REDACTED]"

_SECRET_NAME = r"(?:api[_-]?key|token|secret|password|passwd|credential|private[_-]?key)"
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)"
)
_OPTION = re.compile(
    rf"(?i)(\s--?[A-Z0-9_-]*{_SECRET_NAME}[A-Z0-9_-]*(?:\s+|=))"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)"
)
_BEARER = re.compile(r"(?i)(\bauthorization\s*:\s*bearer\s+|\bbearer\s+)[^\s,;]+")
_KNOWN_TOKEN_SHAPES = (
    re.compile(r"\byt[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_SENSITIVE_ENV_NAME = re.compile(_SECRET_NAME, re.IGNORECASE)


def _environment_secrets(environment: Mapping[str, str]) -> list[str]:
    return [
        value for name, value in environment.items() if value and len(value) >= 4 and _SENSITIVE_ENV_NAME.search(name)
    ]


def _redact(text: str, known_secrets: "Iterable[str] | None", environment: "Mapping[str, str] | None") -> str:
    """Remove secret values, leaving whitespace alone so callers choose their own shape."""
    redacted = _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{REDACTION}" if _SENSITIVE_ENV_NAME.search(match.group(2)) else match.group(0),
        text,
    )
    redacted = _OPTION.sub(rf"\1{REDACTION}", redacted)
    redacted = _BEARER.sub(rf"\1{REDACTION}", redacted)
    for pattern in _KNOWN_TOKEN_SHAPES:
        redacted = pattern.sub(REDACTION, redacted)

    secret_values = [known_secrets] if isinstance(known_secrets, str) else list(known_secrets or ())
    secret_values.extend(_environment_secrets(os.environ if environment is None else environment))
    for secret in sorted(set(secret_values), key=len, reverse=True):
        if secret:
            redacted = redacted.replace(secret, REDACTION)
    return redacted


def sanitize_command_preview(
    command: str,
    *,
    known_secrets: "Iterable[str] | None" = None,
    environment: "Mapping[str, str] | None" = None,
    max_characters: int = COMMAND_PREVIEW_MAX_CHARACTERS,
) -> str:
    """Return a bounded one-line command identity with secret values removed."""
    preview = _redact(" ".join(str(command).split()), known_secrets, environment)

    if max_characters < 1:
        return ""
    if len(preview) <= max_characters:
        return preview
    if max_characters == 1:
        return "…"
    return f"{preview[: max_characters - 1].rstrip()}…"


def sanitize_output_preview(
    output: str,
    *,
    known_secrets: "Iterable[str] | None" = None,
    environment: "Mapping[str, str] | None" = None,
    max_lines: int = OUTPUT_PREVIEW_MAX_LINES,
    max_characters: int = OUTPUT_PREVIEW_MAX_CHARACTERS,
    max_line_characters: int = OUTPUT_PREVIEW_MAX_LINE_CHARACTERS,
) -> str:
    """Return the TAIL of what a command has printed, with secret values removed.

    Two deliberate differences from :func:`sanitize_command_preview`, both because
    this is a feed rather than an identity:

    * Newlines survive. A command's output is read as lines; collapsing it to one
      would make the card unreadable the moment anything printed twice.
    * The END is kept, not the beginning. The card shows a couple of lines, and a
      head-truncated feed freezes on the first thing printed and never moves again
      -- which is precisely what this exists to avoid. Keeping the tail is what a
      terminal does.

    Redaction is the same, and matters more here than on the command line: a
    command's argv is chosen by the model, while its output is whatever the machine
    happened to print -- a ``cat`` of a dotfile, an SDK dumping its config.
    """
    text = str(output).replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    # A trailing newline would otherwise spend one of very few visible lines on an
    # empty one, which on a two-line card halves the window.
    lines = text.rstrip("\n").split("\n")
    truncated = len(lines) > max_lines
    kept = lines[-max_lines:] if max_lines > 0 else []
    # Per line, keep the HEAD. A wrapped line would otherwise occupy rows the newest
    # lines need, and a line is scanned from its start — a progress line's label is
    # what identifies it.
    if max_line_characters > 0:
        clipped = []
        for line in kept:
            if len(line) > max_line_characters:
                clipped.append(f"{line[: max_line_characters - 1]}…")
                truncated = True
            else:
                clipped.append(line)
        kept = clipped
    preview = "\n".join(kept)

    if len(preview) > max_characters:
        preview = preview[len(preview) - max_characters :]
        truncated = True

    preview = _redact(preview, known_secrets, environment)
    return f"…{preview.lstrip()}" if truncated else preview
