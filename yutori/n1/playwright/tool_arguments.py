"""Shared parsing helpers for chat-completions tool arguments."""

from __future__ import annotations

import json
from typing import Any

from .errors import PlaywrightActionError


def parse_tool_arguments(tool_call: Any) -> dict[str, Any]:
    """Parse a tool-call object's JSON arguments into a dictionary."""

    arguments = getattr(getattr(tool_call, "function", tool_call), "arguments", "{}") or "{}"
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise PlaywrightActionError(f"tool arguments were not valid JSON: {arguments}") from exc
    if not isinstance(parsed, dict):
        raise PlaywrightActionError(f"tool arguments must decode to an object: {arguments}")
    return parsed
