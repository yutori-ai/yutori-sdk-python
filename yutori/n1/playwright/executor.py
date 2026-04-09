"""Generic Playwright runtime for Yutori n1/n1.5 browser tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from ..coordinates import denormalize_coordinates
from ..keys import map_key_to_playwright, map_keys_individual
from .errors import PlaywrightActionError
from .page_scripts import (
    EXECUTE_JS_SCRIPT,
    EXTRACT_ELEMENTS_SCRIPT,
    FIND_TEXT_SCRIPT,
    GET_ELEMENT_BY_REF_SCRIPT,
    SET_ELEMENT_VALUE_SCRIPT,
    coerce_script_result,
    prepare_page_for_model,
)
from .tool_arguments import parse_tool_arguments

logger = logging.getLogger(__name__)

DEFAULT_NAVIGATION_TIMEOUT_MS = 20_000
DEFAULT_WAIT_SECONDS = 1.0
MAX_ACCESSIBLE_SNAPSHOT_CHARS = 4_000
_ARIA_LINK_PATTERN = re.compile(r'- link "([^"]*)"')
_ARIA_URL_PATTERN = re.compile(r"- /url: (.+)")

ACTION_DELAY_SECONDS: dict[str, float] = {
    "left_click": 0.25,
    "double_click": 0.25,
    "triple_click": 0.25,
    "middle_click": 0.25,
    "right_click": 0.25,
    "drag": 0.35,
    "type": 0.2,
    "hover": 0.15,
    "mouse_move": 0.15,
    "mouse_down": 0.15,
    "mouse_up": 0.15,
    "scroll": 0.15,
    "key_press": 0.15,
    "goto_url": 0.8,
    "go_back": 0.8,
    "go_forward": 0.8,
    "refresh": 0.8,
    "screenshot": 0.0,
    "wait": 0.0,
    "hold_key": 0.0,
}

ACTION_NAME_ALIASES: dict[str, str] = {
    "back": "go_back",
    "goto": "goto_url",
    "key": "key_press",
}

KEY_COMBINATION_ACTIONS: dict[str, str] = {
    "Alt+ArrowLeft": "go_back",
    "Alt+ArrowRight": "go_forward",
    "F5": "refresh",
}

DISALLOWED_ZOOM_KEYS = {"-", "+", "=", "0", "Minus", "Equal"}
UNSUPPORTED_BROWSER_TOOLS = {"zoom"}


def _has_coordinates(coordinates: Any) -> bool:
    return isinstance(coordinates, (list, tuple)) and len(coordinates) == 2


def expand_key_sequence(key_text: str) -> list[str]:
    """Normalize key input across legacy n1 and SDK-backed n1.5 formats."""

    stripped = key_text.strip()
    if not stripped:
        return []

    token_candidates = [token for token in re.split(r"[\s,]+", stripped) if token]
    if any(any(char.isupper() for char in token) for token in token_candidates):
        return token_candidates
    return map_key_to_playwright(stripped)


def is_disallowed_zoom_shortcut(key_text: str) -> bool:
    """Return true when the key chord would change the browser zoom level."""

    key_sequence = expand_key_sequence(key_text)
    if len(key_sequence) != 1:
        return False
    parts = [part for part in key_sequence[0].split("+") if part]
    return any(part in {"ControlOrMeta", "Control", "Meta"} for part in parts) and any(
        part in DISALLOWED_ZOOM_KEYS for part in parts[1:]
    )


def _map_modifier(modifier: str | None) -> str | None:
    """Map the single-action modifier field to one Playwright key name."""

    if not modifier:
        return None
    mapped_keys = map_keys_individual(modifier)
    if not mapped_keys:
        return modifier
    return mapped_keys[0]


def render_action_trace(
    action_name: str,
    arguments: dict[str, Any],
    *,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Render a compact trace line for a tool call."""

    canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)
    modifier = arguments.get("modifier")

    if canonical_name in {
        "left_click",
        "double_click",
        "triple_click",
        "middle_click",
        "right_click",
        "hover",
        "mouse_move",
        "mouse_down",
        "mouse_up",
    } and width and height:
        coordinates = arguments.get("coordinates")
        if _has_coordinates(coordinates):
            x, y = denormalize_coordinates(coordinates, width=width, height=height)
            if modifier:
                return f"{canonical_name}([{x}, {y}], modifier={modifier!r})"
            return f"{canonical_name}([{x}, {y}])"
        if arguments.get("ref"):
            extra = f", modifier={modifier!r}" if modifier else ""
            return f"{canonical_name}(ref={arguments['ref']!r}{extra})"

    if canonical_name == "drag" and width and height:
        start = arguments.get("start_coordinates")
        end = arguments.get("coordinates")
        if _has_coordinates(start) and _has_coordinates(end):
            start_x, start_y = denormalize_coordinates(start, width=width, height=height)
            end_x, end_y = denormalize_coordinates(end, width=width, height=height)
            return f"drag([{start_x}, {start_y}], [{end_x}, {end_y}])"

    if canonical_name == "scroll" and width and height:
        coordinates = arguments.get("coordinates")
        direction = str(arguments.get("direction", "down")).lower()
        amount = arguments.get("amount", 1)
        if _has_coordinates(coordinates):
            x, y = denormalize_coordinates(coordinates, width=width, height=height)
            if modifier:
                return f"scroll([{x}, {y}], direction={direction}, amount={amount}, modifier={modifier!r})"
            return f"scroll([{x}, {y}], direction={direction}, amount={amount})"
        if arguments.get("ref"):
            extra = f", modifier={modifier!r}" if modifier else ""
            return f"scroll(ref={arguments['ref']!r}, direction={direction}, amount={amount}{extra})"

    if canonical_name == "type":
        text = json.dumps(str(arguments.get("text", "")))
        press_enter = bool(arguments.get("press_enter_after"))
        clear_before = bool(
            arguments.get("clear_before_typing") or arguments.get("clear_before") or arguments.get("clear_before_type")
        )
        return f"type({text}, press_enter_after={press_enter}, clear_before={clear_before})"

    if canonical_name == "key_press":
        key_comb = str(arguments.get("key") or arguments.get("key_comb") or "")
        key_sequence = expand_key_sequence(key_comb)
        if not key_sequence:
            return "key_press()"
        if len(key_sequence) == 1:
            return f"key_press({key_sequence[0]})"
        return f"key_press_sequence({', '.join(key_sequence)})"

    if canonical_name == "hold_key":
        key_text = str(arguments.get("key") or "")
        duration = arguments.get("duration")
        key_sequence = expand_key_sequence(key_text) if key_text.strip() else []
        key_label = key_sequence[0] if key_sequence else ""
        if duration is None:
            return f"hold_key({key_label})"
        return f"hold_key({key_label}, duration={duration!r})"

    if canonical_name == "goto_url":
        return f'goto_url({json.dumps(str(arguments.get("url") or arguments.get("href") or ""))})'

    if canonical_name == "wait":
        duration = arguments.get("duration", arguments.get("seconds", DEFAULT_WAIT_SECONDS))
        return f"wait(duration={duration!r})"

    if not arguments:
        return f"{canonical_name}()"

    ordered_parts = ", ".join(f"{key}={arguments[key]!r}" for key in sorted(arguments))
    return f"{canonical_name}({ordered_parts})"


@dataclass(frozen=True)
class PlaywrightActionContext:
    """Minimal runtime context needed to execute browser-use tool calls."""

    page: Any
    viewport_width: int
    viewport_height: int

    @classmethod
    def from_page(cls, page: Any) -> "PlaywrightActionContext":
        """Create a context from a Playwright page with a configured viewport."""

        viewport = getattr(page, "viewport_size", None)
        if not isinstance(viewport, dict):
            raise ValueError("page.viewport_size must be available to derive viewport dimensions")
        width = int(viewport.get("width") or 0)
        height = int(viewport.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("page.viewport_size must include positive width and height values")
        return cls(page=page, viewport_width=width, viewport_height=height)

    @classmethod
    def from_viewport(cls, page: Any, viewport: Any) -> "PlaywrightActionContext":
        """Create a context from a page and any object exposing width/height attributes."""

        width = int(getattr(viewport, "width"))
        height = int(getattr(viewport, "height"))
        if width <= 0 or height <= 0:
            raise ValueError("viewport width and height must be positive integers")
        return cls(page=page, viewport_width=width, viewport_height=height)


@dataclass
class PlaywrightToolExecutionResult:
    """Structured result for read-like tools that produce output."""

    trace: str
    output_text: str | None = None
    current_url: str | None = None


class PlaywrightActionExecutor:
    """Execute Yutori browser-use model tool calls against a Playwright page."""

    def __init__(
        self,
        *,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        settle_delay_seconds: float | None = None,
    ) -> None:
        self.navigation_timeout_ms = navigation_timeout_ms
        self.settle_delay_seconds = settle_delay_seconds

    async def execute_tool_call(
        self,
        context: PlaywrightActionContext,
        tool_call: Any,
    ) -> PlaywrightToolExecutionResult | str:
        """Execute a tool-call object with ``function.name`` and JSON arguments."""

        action_name = getattr(getattr(tool_call, "function", tool_call), "name", "")
        canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)
        arguments = parse_tool_arguments(tool_call)
        if canonical_name == "extract_elements":
            return await self._execute_extract_elements(context, arguments)
        if canonical_name == "find":
            return await self._execute_find(context, arguments)
        if canonical_name == "set_element_value":
            return await self._execute_set_element_value(context, arguments)
        if canonical_name == "execute_js":
            return await self._execute_execute_js(context, arguments)
        if canonical_name == "extract_content":
            return await self._execute_extract_content(context)
        return await self.execute_action(context=context, action_name=action_name, arguments=arguments)

    async def execute_action(
        self,
        *,
        context: PlaywrightActionContext,
        action_name: str,
        arguments: dict[str, Any] | None,
    ) -> str:
        """Execute a single action tool call against the provided context."""

        raw_arguments = arguments or {}
        canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)
        debug_trace = render_action_trace(
            canonical_name,
            raw_arguments,
            width=context.viewport_width,
            height=context.viewport_height,
        )
        page = context.page
        width = context.viewport_width
        height = context.viewport_height
        needs_domcontentloaded_wait = canonical_name not in {"screenshot", "wait", "hold_key"}
        await prepare_page_for_model(page)

        try:
            if canonical_name in {"hover", "mouse_move"}:
                x, y = await self._resolve_action_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                await self._call_preview_action(action_type=canonical_name, x=x, y=y)
                await page.mouse.move(x, y)
                result_text = "Mouse moved and hovering"

            elif canonical_name in {
                "left_click",
                "double_click",
                "triple_click",
                "middle_click",
                "right_click",
            }:
                x, y = await self._resolve_action_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                click_count = 3 if canonical_name == "triple_click" else 2 if canonical_name == "double_click" else 1
                button = (
                    "middle"
                    if canonical_name == "middle_click"
                    else "right"
                    if canonical_name == "right_click"
                    else "left"
                )
                modifier = _map_modifier(raw_arguments.get("modifier"))
                await self._call_preview_action(action_type=canonical_name, x=x, y=y, num_clicks=click_count)
                async with self._temporary_keys_down(page, [modifier] if modifier else []):
                    await page.mouse.move(x, y)
                    await asyncio.sleep(0.1)
                    await page.mouse.click(x, y, button=button, click_count=click_count)
                result_text = f"Clicked {click_count}x with {button}"

            elif canonical_name == "drag":
                start = raw_arguments.get("start_coordinates")
                end = raw_arguments.get("coordinates")
                if not _has_coordinates(start) or not _has_coordinates(end):
                    raise PlaywrightActionError("drag requires start_coordinates and coordinates")
                start_x, start_y = denormalize_coordinates(start, width=width, height=height)
                end_x, end_y = denormalize_coordinates(end, width=width, height=height)
                await self._call_preview_action(
                    action_type="drag",
                    x=end_x,
                    y=end_y,
                    start_x=start_x,
                    start_y=start_y,
                )
                await page.mouse.move(start_x, start_y)
                await page.mouse.down()
                await page.mouse.move(end_x, end_y, steps=10)
                await page.mouse.up()
                result_text = "Dragged successfully"

            elif canonical_name == "mouse_down":
                x, y = await self._resolve_action_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                await self._call_preview_action(action_type="mouse_down", x=x, y=y)
                await page.mouse.move(x, y)
                await page.mouse.down()
                result_text = "Mouse button pressed"

            elif canonical_name == "mouse_up":
                x, y = await self._resolve_action_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                await self._call_preview_action(action_type="mouse_up", x=x, y=y)
                await page.mouse.move(x, y)
                await page.mouse.up()
                result_text = "Mouse button released"

            elif canonical_name == "scroll":
                direction = str(raw_arguments.get("direction", "down")).lower()
                amount = float(raw_arguments.get("amount", 1))
                modifier = _map_modifier(raw_arguments.get("modifier"))
                if direction not in {"up", "down", "left", "right"}:
                    raise PlaywrightActionError(f"unsupported scroll direction: {direction}")
                px = amount * 100
                delta_x = px if direction == "right" else -px if direction == "left" else 0.0
                delta_y = px if direction == "down" else -px if direction == "up" else 0.0

                coordinates = None
                if _has_coordinates(raw_arguments.get("coordinates")) or raw_arguments.get("ref"):
                    coordinates = await self._resolve_action_coordinates(
                        page,
                        raw_arguments,
                        width=width,
                        height=height,
                        action_name=canonical_name,
                    )

                async with self._temporary_keys_down(page, [modifier] if modifier else []):
                    if coordinates is not None:
                        x, y = coordinates
                        await self._call_preview_action(action_type="scroll", x=x, y=y, direction=direction)
                        await page.mouse.move(x, y)
                        await page.mouse.wheel(delta_x, delta_y)
                    else:
                        await page.evaluate(f"window.scrollBy({delta_x}, {delta_y})")
                result_text = f"Scrolled {direction}"

            elif canonical_name == "type":
                text = str(raw_arguments.get("text", ""))
                clear_before = bool(
                    raw_arguments.get("clear_before_typing")
                    or raw_arguments.get("clear_before")
                    or raw_arguments.get("clear_before_type")
                )
                press_enter = bool(raw_arguments.get("press_enter_after"))
                await self._call_preview_action(action_type="type")
                if clear_before:
                    await page.keyboard.press("ControlOrMeta+A")
                    await page.keyboard.press("Backspace")
                for offset in range(0, len(text), 50):
                    await page.keyboard.type(text[offset : offset + 50])
                if press_enter:
                    await page.keyboard.press("Enter")
                result_text = f"Typed {len(text)} characters"

            elif canonical_name == "key_press":
                key_text = str(raw_arguments.get("key") or raw_arguments.get("key_comb") or "")
                if not key_text:
                    raise PlaywrightActionError("key_press requires key")
                key_sequence = expand_key_sequence(key_text)
                if not key_sequence:
                    raise PlaywrightActionError("key_press requires key")
                if len(key_sequence) == 1:
                    semantic_action = KEY_COMBINATION_ACTIONS.get(key_sequence[0])
                    if semantic_action is not None:
                        return await self.execute_action(context=context, action_name=semantic_action, arguments={})
                await self._call_set_status("Pressing keys")
                for key_name in key_sequence:
                    if is_disallowed_zoom_shortcut(key_name):
                        continue
                    await page.keyboard.press(key_name)
                result_text = f"Pressed key: {key_text}"

            elif canonical_name == "goto_url":
                url = str(raw_arguments.get("url") or raw_arguments.get("href") or "")
                if not url:
                    raise PlaywrightActionError("goto_url requires url")
                if "://" not in url:
                    url = f"https://{url}"
                await self._call_set_status("Navigating")
                await page.goto(url, wait_until="domcontentloaded")
                result_text = f"Navigated to {url}"

            elif canonical_name == "go_back":
                await self._call_set_status("Navigating")
                await page.go_back(wait_until="domcontentloaded")
                result_text = "Navigated back"

            elif canonical_name == "go_forward":
                await self._call_set_status("Navigating")
                await page.go_forward(wait_until="domcontentloaded")
                result_text = "Navigated forward"

            elif canonical_name == "refresh":
                await self._call_set_status("Refreshing")
                await page.reload(wait_until="domcontentloaded")
                result_text = "Refreshed the page"

            elif canonical_name == "screenshot":
                result_text = "Screenshot captured"

            elif canonical_name == "wait":
                wait_duration = float(raw_arguments.get("duration", raw_arguments.get("seconds", DEFAULT_WAIT_SECONDS)))
                duration = max(
                    0.0,
                    min(wait_duration, 100.0),
                )
                await self._call_set_status("Waiting")
                await asyncio.sleep(duration)
                result_text = f"Waited {duration}s"

            elif canonical_name == "hold_key":
                key_text = str(raw_arguments.get("key") or "")
                if not key_text.strip():
                    raise PlaywrightActionError("hold_key requires key")
                duration = raw_arguments.get("duration")
                await self._call_set_status("Holding key")
                if duration is not None and float(duration) > 0:
                    hold_duration = min(float(duration), 100.0)
                    individual_keys = map_keys_individual(key_text)
                    if not individual_keys:
                        raise PlaywrightActionError("hold_key requires key")
                    async with self._temporary_keys_down(page, individual_keys):
                        await asyncio.sleep(hold_duration)
                    result_text = f"Held key '{key_text}' for {hold_duration}s"
                else:
                    key_sequence = expand_key_sequence(key_text)
                    if not key_sequence:
                        raise PlaywrightActionError("hold_key requires key")
                    for key_name in key_sequence:
                        if is_disallowed_zoom_shortcut(key_name):
                            continue
                        await page.keyboard.press(key_name)
                    result_text = f"Pressed key: {key_text}"

            elif canonical_name in UNSUPPORTED_BROWSER_TOOLS:
                raise PlaywrightActionError(f"unsupported action: {canonical_name}")

            else:
                raise PlaywrightActionError(f"unsupported action: {canonical_name}")

        except PlaywrightActionError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through integration tests
            raise PlaywrightActionError(f"failed to execute {debug_trace}: {exc}") from exc

        if needs_domcontentloaded_wait:
            await self._best_effort_wait_for_domcontentloaded(page)
            await asyncio.sleep(self._post_action_delay(canonical_name))
        return result_text

    async def _execute_extract_content(self, context: PlaywrightActionContext) -> PlaywrightToolExecutionResult:
        await self._call_set_status("Reading page")
        await prepare_page_for_model(context.page)
        snapshot = await self._accessible_page_snapshot(context.page)
        return PlaywrightToolExecutionResult(
            trace="extract_content()",
            output_text=(
                f"Accessible page snapshot:\n"
                f"{self._clip_multiline_text(snapshot, MAX_ACCESSIBLE_SNAPSHOT_CHARS)}"
                if snapshot
                else "No accessible page snapshot was available."
            ),
            current_url=context.page.url,
        )

    async def _execute_extract_elements(
        self,
        context: PlaywrightActionContext,
        arguments: dict[str, Any],
    ) -> PlaywrightToolExecutionResult:
        await self._call_set_status("Reading page")
        await prepare_page_for_model(context.page)
        result = coerce_script_result(await context.page.evaluate(EXTRACT_ELEMENTS_SCRIPT, arguments.get("filter")))
        if result.get("success") is False:
            raise PlaywrightActionError(str(result.get("message") or "Failed to extract page elements."))
        page_content = str(result.get("pageContent") or "").strip()
        output_text = page_content or "No matching elements were extracted from the current page."
        return PlaywrightToolExecutionResult(
            trace=f"extract_elements(filter={arguments.get('filter')!r})" if arguments else "extract_elements()",
            output_text=output_text,
            current_url=context.page.url,
        )

    async def _execute_find(
        self,
        context: PlaywrightActionContext,
        arguments: dict[str, Any],
    ) -> PlaywrightToolExecutionResult:
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise PlaywrightActionError("find requires text")
        await self._call_set_status("Finding text")
        await prepare_page_for_model(context.page)
        result = coerce_script_result(await context.page.evaluate(FIND_TEXT_SCRIPT, text))
        if result.get("success") is False:
            raise PlaywrightActionError(str(result.get("message") or "Failed to search the page text."))
        lines = [str(result.get("message") or "")]
        matches = result.get("matches") or []
        if matches:
            lines.append("Matches:")
            lines.extend(str(match) for match in matches)
        return PlaywrightToolExecutionResult(
            trace=f"find(text={text!r})",
            output_text="\n".join(line for line in lines if line).strip(),
            current_url=context.page.url,
        )

    async def _execute_set_element_value(
        self,
        context: PlaywrightActionContext,
        arguments: dict[str, Any],
    ) -> PlaywrightToolExecutionResult:
        ref = str(arguments.get("ref") or "").strip()
        if not ref:
            raise PlaywrightActionError("set_element_value requires ref")
        await self._call_set_status("Setting value")
        await prepare_page_for_model(context.page)
        result = coerce_script_result(
            await context.page.evaluate(SET_ELEMENT_VALUE_SCRIPT, {"ref": ref, "value": arguments.get("value")})
        )
        if result.get("success") is False:
            raise PlaywrightActionError(str(result.get("message") or "Failed to set the element value."))
        return PlaywrightToolExecutionResult(
            trace=f"set_element_value(ref={ref!r}, value={arguments.get('value')!r})",
            output_text=str(result.get("message") or "Set the element value."),
            current_url=context.page.url,
        )

    async def _execute_execute_js(
        self,
        context: PlaywrightActionContext,
        arguments: dict[str, Any],
    ) -> PlaywrightToolExecutionResult:
        source = str(arguments.get("text") or "").strip()
        if not source:
            raise PlaywrightActionError("execute_js requires text")
        result = coerce_script_result(await context.page.evaluate(EXECUTE_JS_SCRIPT, source))
        if result.get("success") is False:
            raise PlaywrightActionError(str(result.get("message") or "Failed to execute JavaScript."))
        output_text = "Executed JavaScript."
        if result.get("hasResult"):
            output_text = f"JavaScript result: {result.get('result')}"
        return PlaywrightToolExecutionResult(
            trace="execute_js()",
            output_text=output_text,
            current_url=context.page.url,
        )

    async def _accessible_page_snapshot(self, page: Any) -> str | None:
        locator = getattr(page, "locator", None)
        if callable(locator):
            try:
                body = locator("body")
                aria_snapshot = getattr(body, "aria_snapshot", None)
                if callable(aria_snapshot):
                    snapshot = await aria_snapshot()
                    if snapshot:
                        return str(snapshot).strip() or None
            except Exception:
                logger.debug("body aria_snapshot failed; falling back to innerText", exc_info=True)

        try:
            snapshot = await page.evaluate(
                """() => {
                    const text = (document.body?.innerText || "").replace(/\\n{3,}/g, "\\n\\n").trim();
                    return text || null;
                }"""
            )
        except Exception:
            logger.debug("innerText fallback failed for extract_content", exc_info=True)
            return None
        if not snapshot:
            return None
        return str(snapshot).strip() or None

    async def _best_effort_wait_for_domcontentloaded(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=self.navigation_timeout_ms)
        except Exception:
            return

    async def _call_preview_action(self, *, action_type: str, **kwargs: Any) -> None:
        try:
            await self._preview_action(action_type=action_type, **kwargs)
        except Exception:
            logger.debug("Playwright preview_action hook failed", exc_info=True)

    async def _call_set_status(self, label: str) -> None:
        try:
            await self._set_status(label)
        except Exception:
            logger.debug("Playwright set_status hook failed", exc_info=True)

    async def _preview_action(self, *, action_type: str, **kwargs: Any) -> None:
        del action_type, kwargs

    async def _set_status(self, label: str) -> None:
        del label

    def _post_action_delay(self, action_name: str) -> float:
        if self.settle_delay_seconds is not None:
            return self.settle_delay_seconds
        return ACTION_DELAY_SECONDS.get(action_name, 0.3)

    async def _resolve_action_coordinates(
        self,
        page: Any,
        arguments: dict[str, Any],
        *,
        width: int,
        height: int,
        action_name: str,
    ) -> tuple[int, int]:
        ref = arguments.get("ref")
        coordinates = arguments.get("coordinates")
        if ref:
            result = coerce_script_result(await page.evaluate(GET_ELEMENT_BY_REF_SCRIPT, ref))
            if result.get("success") is not False:
                resolved_coordinates = result.get("coordinates")
                if _has_coordinates(resolved_coordinates):
                    return int(float(resolved_coordinates[0])), int(float(resolved_coordinates[1]))
            if not _has_coordinates(coordinates):
                raise PlaywrightActionError(str(result.get("message") or f"Could not resolve ref {ref!r}"))

        if _has_coordinates(coordinates):
            x, y = denormalize_coordinates(coordinates, width=width, height=height)
            return x, y

        raise PlaywrightActionError(f"{action_name} requires coordinates or ref")

    @asynccontextmanager
    async def _temporary_keys_down(self, page: Any, keys: list[str]) -> Any:
        if not keys:
            yield
            return

        pressed: list[str] = []
        try:
            for key_name in keys:
                await page.keyboard.down(key_name)
                pressed.append(key_name)
            yield
        finally:
            for key_name in reversed(pressed):
                try:
                    await page.keyboard.up(key_name)
                except Exception:
                    logger.debug("Failed to release key %s", key_name, exc_info=True)

    @staticmethod
    def _clip_multiline_text(text: str, limit: int) -> str:
        normalized = text.strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(limit - 3, 0)].rstrip() + "..."
