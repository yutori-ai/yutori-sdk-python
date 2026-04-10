"""Shared Playwright helpers for custom n1/n1.5 browser loops."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import sys
from functools import lru_cache
from typing import Any, Callable

from ._assets import load_js_asset
from .coordinates import denormalize_coordinates
from .keys import map_key_to_playwright, map_keys_individual
from .page_ready import PageReadyChecker

logger = logging.getLogger(__name__)

EXTRACT_CONTENT_AND_LINKS_TOOL_NAME = "extract_content_and_links"
_GET_ELEMENT_BY_REF_JS = load_js_asset("get_element_by_ref.js")
_EXTRACT_DOM_ELEMENTS_JS = load_js_asset("extract_dom_elements.js")
_SET_ELEMENT_VALUE_JS = load_js_asset("set_element_value.js")
_START_PAGE_MARKER_JS = load_js_asset("start_page_marker.js")
_CHECK_START_PAGE_MARKER_JS = load_js_asset("check_start_page_marker.js")

_LINK_PATTERN = re.compile(r'- link "([^"]*)"')
_URL_PATTERN = re.compile(r"- /url: (.+)")
_TITLE_CLEANER_PATTERN = re.compile(r"\s+\d+$")

ToolHandler = Callable[..., Any]


def extract_content_and_links_tool_schema(name: str = EXTRACT_CONTENT_AND_LINKS_TOOL_NAME) -> dict[str, Any]:
    """Return an OpenAI-style function tool schema for read-only page extraction."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": (
                "Extracts page content and hyperlinks relevant to the user task. "
                "This operation is strictly read-only and never interacts with or alters the page"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


async def extract_content_and_links(page: Any, **_: Any) -> str:
    """Extract all links from the page's ARIA snapshot with lightweight deduping."""

    url_to_title: dict[str, str] = {}
    snapshot = await page.locator("body").aria_snapshot()
    lines = snapshot.split("\n")

    for index, line in enumerate(lines):
        link_match = _LINK_PATTERN.search(line)
        if not link_match:
            continue
        title = link_match.group(1)

        url = None
        child_indent = " " * (len(line) - len(line.lstrip()) + 2)
        next_index = index + 1
        while next_index < len(lines):
            next_line = lines[next_index]
            if next_line.strip() and not next_line.startswith(child_indent):
                break
            url_match = _URL_PATTERN.search(next_line)
            if url_match:
                url = url_match.group(1).strip()
                break
            next_index += 1

        if not url:
            continue

        title = _TITLE_CLEANER_PATTERN.sub("", title).strip()
        existing = url_to_title.get(url)
        if existing is None or len(title) > len(existing):
            url_to_title[url] = title

    result = f"Current URL: {page.url}"
    if url_to_title:
        result += "\nLinks on the entire page:\n"
        result += "\n".join(f"- [{title}]({url})" for url, title in url_to_title.items())
    return result


@lru_cache(maxsize=None)
def _handler_signature(handler: ToolHandler) -> inspect.Signature:
    return inspect.signature(handler)


async def _evaluate_js_function(page: Any, script: str, *args: Any) -> Any:
    escaped_args = ", ".join(json.dumps(arg) for arg in args)
    return await page.evaluate(f"({script})({escaped_args})")


class AsyncPlaywrightActionExecutor:
    """Execute n1/n1.5 browser actions against an async Playwright page."""

    def __init__(
        self,
        page: Any,
        *,
        viewport_width: int,
        viewport_height: int,
        page_ready_checker: PageReadyChecker | None = None,
        custom_tools: dict[str, ToolHandler] | None = None,
        tool_call_timeout: float = 30.0,
        num_tool_retries: int = 1,
        retry_delay: float = 1.0,
        max_retry_delay: float = 60.0,
    ) -> None:
        self.page = page
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.page_ready_checker = page_ready_checker or PageReadyChecker()
        self.custom_tools = custom_tools or {}
        self.tool_call_timeout = tool_call_timeout
        self.num_tool_retries = max(1, num_tool_retries)
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay

    async def mark_current_page_as_start(self) -> None:
        """Mark the current history entry as the loop's start page."""

        try:
            await self.page.evaluate(_START_PAGE_MARKER_JS)
        except Exception as exc:
            logger.debug("Failed to mark start page: %s", exc)

    async def wait_until_ready(self, fast_mode: bool = False) -> bool:
        return await self.page_ready_checker.wait_until_ready(self.page, fast_mode=fast_mode)

    async def execute_tool_call(self, tool_call: Any) -> str | None:
        """Parse and execute an OpenAI-style tool call."""

        action_name = tool_call.function.name
        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            return f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"
        return await self.execute(action_name, arguments)

    async def execute(self, action_name: str, arguments: dict[str, Any] | None = None) -> str | None:
        """Execute a single action with a simple timeout/retry wrapper."""

        arguments = arguments or {}
        last_error: Exception | None = None

        for attempt in range(self.num_tool_retries):
            try:
                return await asyncio.wait_for(
                    self._execute_once(action_name, arguments),
                    timeout=self.tool_call_timeout,
                )
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self.num_tool_retries:
                    break
                await asyncio.sleep(min(self.retry_delay * (2**attempt), self.max_retry_delay))

        return f"[ERROR] Error executing {action_name}: {last_error}"

    async def _execute_once(self, action_name: str, arguments: dict[str, Any]) -> str | None:
        custom_handler = self.custom_tools.get(action_name)
        if custom_handler is not None:
            await self.wait_until_ready()
            return await self._call_tool_handler(custom_handler, arguments)

        if action_name == EXTRACT_CONTENT_AND_LINKS_TOOL_NAME:
            await self.wait_until_ready()
            return await extract_content_and_links(self.page, **arguments)

        modifier = self._map_modifier(arguments.get("modifier"))

        if action_name in ("left_click", "double_click", "triple_click", "middle_click", "right_click"):
            abs_x, abs_y = await self._require_coordinates(arguments)
            button = {"middle_click": "middle", "right_click": "right"}.get(action_name, "left")
            click_count = {"double_click": 2, "triple_click": 3}.get(action_name, 1)

            if modifier:
                await self.page.keyboard.down(modifier)
            await self.page.mouse.move(abs_x, abs_y)
            await self.page.mouse.click(abs_x, abs_y, button=button, click_count=click_count)
            if modifier:
                await self.page.keyboard.up(modifier)
            await self.wait_until_ready()
            return f"Clicked {click_count}x with {button}"

        if action_name in ("hover", "mouse_move"):
            abs_x, abs_y = await self._require_coordinates(arguments)
            await self.page.mouse.move(abs_x, abs_y)
            await self.wait_until_ready()
            return "Mouse moved and hovering"

        if action_name == "mouse_down":
            abs_x, abs_y = await self._require_coordinates(arguments)
            await self.page.mouse.move(abs_x, abs_y)
            await self.page.mouse.down()
            await self.wait_until_ready()
            return "Mouse button pressed"

        if action_name == "mouse_up":
            abs_x, abs_y = await self._require_coordinates(arguments)
            await self.page.mouse.move(abs_x, abs_y)
            await self.page.mouse.up()
            await self.wait_until_ready()
            return "Mouse button released"

        if action_name == "drag":
            start_coords = arguments.get("start_coordinates") or arguments.get("startCoordinates")
            end_coords = arguments.get("coordinates") or arguments.get("endCoordinates")
            if not start_coords or not end_coords:
                raise ValueError("Drag requires both start and end coordinates")
            start_x, start_y = denormalize_coordinates(start_coords, self.viewport_width, self.viewport_height)
            end_x, end_y = denormalize_coordinates(end_coords, self.viewport_width, self.viewport_height)
            await self.page.mouse.move(start_x, start_y)
            await self.page.mouse.down()
            await self.page.mouse.move(end_x, end_y)
            await self.page.mouse.up()
            await self.wait_until_ready()
            return "Dragged successfully"

        if action_name == "scroll":
            ref = arguments.get("ref")
            coords = arguments.get("coordinates") or arguments.get("coordinate")
            if ref:
                await self._require_coordinates(arguments)
                await self.wait_until_ready()
                return "Scrolled to element"
            if not coords or len(coords) != 2:
                raise ValueError("Scroll requires coordinates or ref")

            abs_x, abs_y = denormalize_coordinates(coords, self.viewport_width, self.viewport_height)
            direction = arguments.get("direction", "down")
            amount = arguments.get("amount", 3)

            if isinstance(amount, (int, float)) and amount <= 10:
                px = float(amount) * 100
            else:
                px = float(amount)

            delta_x = 0.0
            delta_y = 0.0
            if direction == "up":
                delta_y = -px
            elif direction == "down":
                delta_y = px
            elif direction == "left":
                delta_x = -px
            elif direction == "right":
                delta_x = px

            if modifier:
                await self.page.keyboard.down(modifier)
            await self.page.mouse.move(abs_x, abs_y)
            await self.page.mouse.wheel(delta_x, delta_y)
            if modifier:
                await self.page.keyboard.up(modifier)
            await self.wait_until_ready()
            return f"Scrolled {direction}"

        if action_name == "type":
            text = arguments.get("text", "")
            clear_first = arguments.get("clear_before_typing", False)
            press_enter = arguments.get("press_enter_after", False)

            if clear_first:
                await self.page.keyboard.press("Control+a" if sys.platform != "darwin" else "Meta+a")
                await self.page.keyboard.press("Backspace")

            chunk_size = 50
            for index in range(0, len(text), chunk_size):
                await self.page.keyboard.type(text[index : index + chunk_size])

            if press_enter:
                await self.page.keyboard.press("Enter")
            await self.wait_until_ready()
            return f"Typed {len(text)} characters"

        if action_name in ("key", "key_press"):
            if "key_comb" in arguments:
                key = arguments.get("key_comb", "")
                key = "+".join("ControlOrMeta" if token == "Meta" else token for token in key.split("+"))
                await self.page.keyboard.press(key)
            else:
                key_expr = arguments.get("key", "")
                for key in map_key_to_playwright(key_expr):
                    await self.page.keyboard.press(key)
            await self.wait_until_ready()
            return f"Pressed key: {arguments.get('key') or arguments.get('key_comb', '')}"

        if action_name == "hold_key":
            key_expr = arguments.get("key") or arguments.get("key_comb", "")
            duration = arguments.get("duration")
            if duration is not None and duration > 0:
                individual_keys = map_keys_individual(key_expr)
                for key in individual_keys:
                    await self.page.keyboard.down(key)
                await asyncio.sleep(min(duration, 100))
                for key in reversed(individual_keys):
                    await self.page.keyboard.up(key)
            else:
                for key in map_key_to_playwright(key_expr):
                    await self.page.keyboard.press(key)
            await self.wait_until_ready()
            return f"Pressed key: {key_expr}"

        if action_name in ("goto", "goto_url"):
            url = arguments.get("url", "")
            if "://" not in url:
                url = f"https://{url}"
            await self.page.goto(url, wait_until="load")
            await self.wait_until_ready()
            return f"Navigated to {url}"

        if action_name in ("back", "go_back"):
            await self.page.go_back()
            await self.wait_until_ready()
            if await self._is_on_start_page():
                await self.page.go_forward()
                await self.wait_until_ready()
                return (
                    "The page is already on the start page and cannot go back further. "
                    "The go_back tool did not execute, because it is not a valid action type here."
                )
            return "Navigated back"

        if action_name == "go_forward":
            await self.page.go_forward()
            await self.wait_until_ready()
            return "Navigated forward"

        if action_name == "refresh":
            await self.page.reload()
            await self.wait_until_ready()
            return "Refreshed the page"

        if action_name == "wait":
            duration = max(0, min(arguments.get("duration", 5), 100))
            await asyncio.sleep(duration)
            await self.wait_until_ready()
            return f"Waited {duration}s"

        if action_name == "extract_elements":
            await self.wait_until_ready()
            filter_type = arguments.get("filter", "visible")
            result = await _evaluate_js_function(self.page, _EXTRACT_DOM_ELEMENTS_JS, filter_type)
            dom_data = json.loads(result) if isinstance(result, str) else result
            return dom_data.get("pageContent", "") if isinstance(dom_data, dict) else str(result)

        if action_name == "find":
            await self.wait_until_ready()
            text = arguments.get("text", "")
            result = await _evaluate_js_function(self.page, _EXTRACT_DOM_ELEMENTS_JS, "all")
            dom_data = json.loads(result) if isinstance(result, str) else result
            dom_tree = dom_data.get("pageContent", "") if isinstance(dom_data, dict) else str(result)
            matches = [line for line in dom_tree.split("\n") if text.lower() in line.lower()]
            if matches:
                return f'Found {len(matches)} element(s) matching "{text}":\n' + "\n".join(matches[:20])
            return f'No elements matching "{text}" found on the page.'

        if action_name == "set_element_value":
            result_json = await _evaluate_js_function(
                self.page,
                _SET_ELEMENT_VALUE_JS,
                arguments.get("ref", ""),
                arguments.get("value", ""),
            )
            result_data = json.loads(result_json)
            await self.wait_until_ready()
            return result_data.get("message", "set_element_value completed")

        if action_name == "execute_js":
            raw = await self.page.evaluate(arguments.get("text", ""))
            await self.wait_until_ready()
            if raw is None:
                return "undefined"
            if isinstance(raw, (dict, list)):
                return json.dumps(raw, indent=2)
            return str(raw)

        raise ValueError(f"Unknown action: {action_name}")

    async def _call_tool_handler(self, handler: ToolHandler, arguments: dict[str, Any]) -> str | None:
        kwargs = dict(arguments)
        signature = _handler_signature(handler)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        if "page" in signature.parameters or accepts_kwargs:
            kwargs["page"] = self.page

        result = handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result

        if result is None:
            return None
        if isinstance(result, str):
            return result
        if isinstance(result, (dict, list)):
            return json.dumps(result, indent=2)
        return str(result)

    async def _require_coordinates(self, arguments: dict[str, Any]) -> tuple[int, int]:
        resolved = await self._resolve_coordinates(arguments)
        if isinstance(resolved, str):
            raise ValueError(resolved)
        return resolved

    async def _resolve_coordinates(self, arguments: dict[str, Any]) -> tuple[int, int] | str:
        coords = arguments.get("coordinates") or arguments.get("coordinate")
        ref = arguments.get("ref")

        if ref:
            result_json = await _evaluate_js_function(self.page, _GET_ELEMENT_BY_REF_JS, ref)
            result = json.loads(result_json)
            if result.get("success"):
                px = result["coordinates"]
                return int(px[0]), int(px[1])
            if not coords:
                return f'Ref resolution failed for "{ref}": {result.get("message", "Unknown error")}'

        if coords and len(coords) == 2:
            return denormalize_coordinates(coords, self.viewport_width, self.viewport_height)
        return "No coordinates or ref provided"

    async def _is_on_start_page(self) -> bool:
        try:
            return bool(await self.page.evaluate(_CHECK_START_PAGE_MARKER_JS))
        except Exception:
            return False

    @staticmethod
    def _map_modifier(modifier: str | None) -> str | None:
        if not modifier:
            return None
        mapped = map_key_to_playwright(modifier)
        if not mapped:
            return modifier
        return mapped[0].split("+")[0]


__all__ = [
    "AsyncPlaywrightActionExecutor",
    "EXTRACT_CONTENT_AND_LINKS_TOOL_NAME",
    "extract_content_and_links",
    "extract_content_and_links_tool_schema",
]
