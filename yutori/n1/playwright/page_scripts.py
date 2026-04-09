"""Load packaged JavaScript helpers for the SDK Playwright browser-use runtime."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from importlib.resources import files
from typing import Any

logger = logging.getLogger(__name__)

_SCRIPTS_PACKAGE = "yutori.n1.playwright.scripts"


@lru_cache(maxsize=None)
def load_script(name: str) -> str:
    """Read a packaged JavaScript asset once and memoize it."""

    return files(_SCRIPTS_PACKAGE).joinpath(name).read_text(encoding="utf-8")


PREPARE_PAGE_SCRIPT = load_script("prepare_page.js")
GET_ELEMENT_BY_REF_SCRIPT = load_script("get_element_by_ref.js")
EXTRACT_ELEMENTS_SCRIPT = load_script("extract_dom_elements.js")
FIND_TEXT_SCRIPT = load_script("find_text.js")
SET_ELEMENT_VALUE_SCRIPT = load_script("set_element_value.js")
EXECUTE_JS_SCRIPT = load_script("execute_js.js")


def coerce_script_result(result: Any) -> dict[str, Any]:
    """Normalize evaluate() output into a JSON-like dict."""

    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return {"value": result}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    if result is None:
        return {}
    return {"value": result}


async def prepare_page_for_model(page: Any) -> None:
    """Best-effort page preparation before screenshots or model-driven actions."""

    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        await page.evaluate(PREPARE_PAGE_SCRIPT)
    except Exception:
        logger.debug("prepare_page_for_model failed", exc_info=True)
