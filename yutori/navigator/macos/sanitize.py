"""Sanitize command identity before presentation, diagnostics, or telemetry."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

COMMAND_PREVIEW_MAX_CHARACTERS = 160
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


def sanitize_command_preview(
    command: str,
    *,
    known_secrets: "Iterable[str] | None" = None,
    environment: "Mapping[str, str] | None" = None,
    max_characters: int = COMMAND_PREVIEW_MAX_CHARACTERS,
) -> str:
    """Return a bounded one-line command identity with secret values removed."""
    preview = " ".join(str(command).split())
    preview = _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{REDACTION}" if _SENSITIVE_ENV_NAME.search(match.group(2)) else match.group(0),
        preview,
    )
    preview = _OPTION.sub(rf"\1{REDACTION}", preview)
    preview = _BEARER.sub(rf"\1{REDACTION}", preview)
    for pattern in _KNOWN_TOKEN_SHAPES:
        preview = pattern.sub(REDACTION, preview)

    secret_values = [known_secrets] if isinstance(known_secrets, str) else list(known_secrets or ())
    secret_values.extend(_environment_secrets(os.environ if environment is None else environment))
    for secret in sorted(set(secret_values), key=len, reverse=True):
        if secret:
            preview = preview.replace(secret, REDACTION)

    if max_characters < 1:
        return ""
    if len(preview) <= max_characters:
        return preview
    if max_characters == 1:
        return "…"
    return f"{preview[: max_characters - 1].rstrip()}…"
