"""Bundled browser tool scripts for navigator agents."""

from __future__ import annotations

import json
from typing import Any, Protocol

from ._loader import load_tool_script

EXECUTE_JS_SCRIPT = load_tool_script("execute_js.js")
EXTRACT_ELEMENTS_SCRIPT = load_tool_script("extract_elements.js")
FIND_SCRIPT = load_tool_script("find.js")
GET_ELEMENT_BY_REF_SCRIPT = load_tool_script("get_element_by_ref.js")
SET_ELEMENT_VALUE_SCRIPT = load_tool_script("set_element_value.js")


class SupportsAsyncEvaluate(Protocol):
    """A Playwright-style page that supports async JS evaluation."""

    async def evaluate(self, expression: str) -> Any: ...


def coerce_result(raw: Any) -> dict[str, Any]:
    """Normalize ``page.evaluate()`` output into a consistent dict.

    - ``None`` → ``{"success": False, "message": "Script returned no result"}``
    - ``dict`` → passed through unchanged
    - JSON string that parses to a ``dict`` → that dict
    - Anything else → ``{"value": raw}``
    """
    if raw is None:
        return {"success": False, "message": "Script returned no result"}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"value": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": raw}


async def evaluate_tool_script(
    page: SupportsAsyncEvaluate, script: str, *args: Any
) -> dict[str, Any]:
    """Evaluate a bundled JS tool script against a Playwright page.

    Wraps *script* in an IIFE call with JSON-serialized *args*, invokes
    ``page.evaluate()``, and normalizes the result via :func:`coerce_result`.
    """
    escaped_args = ", ".join(json.dumps(arg) for arg in args)
    result = await page.evaluate(f"({script})({escaped_args})")
    return coerce_result(result)


__all__ = [
    "EXECUTE_JS_SCRIPT",
    "EXTRACT_ELEMENTS_SCRIPT",
    "FIND_SCRIPT",
    "GET_ELEMENT_BY_REF_SCRIPT",
    "SET_ELEMENT_VALUE_SCRIPT",
    "coerce_result",
    "evaluate_tool_script",
    "load_tool_script",
]
