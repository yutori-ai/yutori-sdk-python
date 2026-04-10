#!/usr/bin/env python
"""
A web browsing agent using Yutori's n1.5 API.

n1.5 introduces a new action space with renamed tools, selectable tool sets,
optional structured JSON output, and lowercase key names.

Key differences from n1:
- model: "n1.5-latest" (instead of "n1-latest")
- tool_set / disable_tools: select which built-in tools the model can use
- json_schema: request structured output (returned as parsed_json on the response)
- Renamed actions: hover → mouse_move, key_comb → key_press (param: key)
- New actions: middle_click, mouse_down, mouse_up, go_forward, hold_key
- type no longer has press_enter_after / clear_before_typing
- Key names are lowercase (e.g. ctrl+c, enter, left) instead of Playwright names

Usage:
    export YUTORI_API_KEY=...

    # Basic
    python examples/n1_5.py --task "List the team member names" --start-url "https://www.yutori.com"

    # Expanded tool set (adds extract_elements, find, set_element_value, execute_js)
    python examples/n1_5.py --tool-set expanded --task "Fill out the contact form" --start-url "https://example.com"

    # Disable specific tools
    python examples/n1_5.py --disable-tools hold_key drag --task "Search for flights" --start-url "https://google.com/flights"

    # Structured JSON output via --json-schema
    python examples/n1_5.py \
        --task "List the team member names" \
        --start-url "https://www.yutori.com" \
        --json-schema '{"type":"object","properties":{"names":{"type":"array","items":{"type":"string"}}},"required":["names"]}'
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from loguru import logger
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from yutori import AsyncYutoriClient
from yutori.n1 import (
    N1_5_MODEL,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
    aplaywright_screenshot_to_data_url,
    denormalize_coordinates,
    estimate_messages_size_bytes,
    map_key_to_playwright,
    trimmed_messages_to_fit,
)

# ---------------------------------------------------------------------------
# JavaScript helpers for expanded tool set actions.
# Loaded from examples/tools/
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).parent / "tools"


def _load_js(name: str) -> str:
    return (_TOOLS_DIR / name).read_text()


async def _evaluate_js(page, name: str, *args) -> any:
    """Load a JS IIFE from tools/ and evaluate it with the given arguments.

    Builds a call expression with JSON-serialized arguments so multi-argument
    JS functions work correctly with Playwright's page.evaluate() (which only
    passes a single argument).
    """
    script = _load_js(name)
    escaped_args = ", ".join(json.dumps(arg) for arg in args)
    return await page.evaluate(f"({script})({escaped_args})")


RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

# Shorthand aliases for --tool-set
_TOOL_SET_ALIASES = {
    "core": TOOL_SET_CORE,
    "expanded": TOOL_SET_EXPANDED,
}


class Config(BaseModel):
    # task
    task: str = Field(default="List the team member names")
    start_url: str = "https://www.yutori.com"
    # model
    api_key: str = Field(default_factory=lambda: os.getenv("YUTORI_API_KEY"))
    base_url: str = "https://api.yutori.com/v1"
    model: str = N1_5_MODEL
    temperature: float = 0.3
    tool_set: str = TOOL_SET_CORE
    disable_tools: list[str] = Field(default_factory=list)
    json_schema: dict | None = None
    # agent
    max_steps: int = 100
    # browser
    viewport_width: int = 1280
    viewport_height: int = 800
    headless: bool = False
    # payload management
    max_request_bytes: int = 9_500_000
    keep_recent_screenshots: int = 6


class Agent:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.yutori.com/v1",
        model: str = N1_5_MODEL,
        temperature: float = 0.3,
        tool_set: str = TOOL_SET_CORE,
        disable_tools: list[str] | None = None,
        json_schema: dict | None = None,
        max_steps: int = 100,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        headless: bool = False,
        max_request_bytes: int = 9_500_000,
        keep_recent_screenshots: int = 6,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.tool_set = tool_set
        self.disable_tools = disable_tools or []
        self.json_schema = json_schema
        self.max_steps = max_steps
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.headless = headless
        self.max_request_bytes = max_request_bytes
        self.keep_recent_screenshots = keep_recent_screenshots

        self._client: AsyncYutoriClient | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._messages: list = []
        self._step_count = 0

    async def run(self, task: str, start_url: str) -> str:
        logger.info(f"Task: {task}")
        logger.info(f"Starting URL: {start_url}")

        self._messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]
        self._message_index = 0
        self._step_count = 0

        final_response = ""

        async with (
            AsyncYutoriClient(api_key=self.api_key, base_url=self.base_url) as client,
            async_playwright() as playwright,
        ):
            try:
                self._client = client
                await self._init_browser(playwright)
                await self._page.goto(start_url)
                await self._page.wait_for_load_state("domcontentloaded")

                while self._step_count < self.max_steps:
                    self._step_count += 1
                    logger.debug(f"Step {self._step_count}, URL: {self._page.url}")

                    response = await self._predict()

                    # Log raw model prediction
                    logger.info(f"Response: {response}")

                    # Store the assistant's response
                    self._messages.append(response.model_dump(exclude_none=True))
                    self._message_index = len(self._messages)

                    if response.content:
                        final_response = response.content

                    # Stop when there are no tool calls
                    if not response.tool_calls:
                        logger.info("Task completed (no more tool calls)")
                        break

                    # Execute the action(s)
                    for tool_call in response.tool_calls:
                        result = await self._execute(tool_call)
                        content = [{"type": "text", "text": result}] if result else []
                        self._messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})

                if self._step_count >= self.max_steps:
                    logger.warning(f"Reached maximum steps ({self.max_steps})")

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                await self._close_browser()

        return final_response

    async def _init_browser(self, playwright) -> None:
        self._browser = await playwright.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height}
        )
        self._page = await context.new_page()
        await asyncio.sleep(1)

    async def _close_browser(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None

    async def _take_screenshot(self) -> str:
        return await aplaywright_screenshot_to_data_url(
            self._page,
            resize_to=(self.viewport_width, self.viewport_height),
        )

    def _clip_image_url(self, url: str, max_len: int = 50) -> str:
        if url.startswith("data:image"):
            prefix_end = url.find(",") + 1
            if prefix_end > 0 and len(url) > prefix_end + max_len:
                return url[: prefix_end + 20] + "...[clipped]"
        return url if len(url) <= max_len else url[:max_len] + "..."

    def _format_message_for_log(self, message: dict) -> dict:
        result = {}
        for key, value in message.items():
            if key == "content" and isinstance(value, list):
                clipped_content = []
                for item in value:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        clipped_item = dict(item)
                        if "image_url" in clipped_item and "url" in clipped_item["image_url"]:
                            clipped_item["image_url"] = {"url": self._clip_image_url(clipped_item["image_url"]["url"])}
                        clipped_content.append(clipped_item)
                    else:
                        clipped_content.append(item)
                result[key] = clipped_content
            else:
                result[key] = value
        return result

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _call_llm_with_retries(self) -> ChatCompletion:
        size_bytes = estimate_messages_size_bytes(self._messages)
        if size_bytes > self.max_request_bytes:
            self._messages, size_bytes, removed = trimmed_messages_to_fit(
                self._messages,
                max_bytes=self.max_request_bytes,
                keep_recent=self.keep_recent_screenshots,
            )
            if removed:
                logger.info(f"Trimmed {removed} old screenshot(s); payload ~{size_bytes / (1024 * 1024):.2f} MB")

        return await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                temperature=self.temperature,
                tool_set=self.tool_set,
                disable_tools=self.disable_tools or None,
                json_schema=self.json_schema,
            ),
            timeout=120.0,  # 2 minutes
        )

    async def _predict(self) -> ChatCompletion:
        screenshot_url = await self._take_screenshot()
        current_url = self._page.url

        last_content = self._messages[-1]["content"]
        if len(last_content) == 0:
            last_content.append({"type": "text", "text": f"Current URL: {current_url}"})
        last_content.append(
            {
                "type": "image_url",
                "image_url": {"url": screenshot_url, "detail": "high"},
            }
        )

        for i in range(self._message_index, len(self._messages)):
            logger.info(f"Message: {self._format_message_for_log(self._messages[i])}")

        response = await self._call_llm_with_retries()
        return response.choices[0].message

    # ------------------------------------------------------------------
    # Coordinate resolution — supports both normalized coordinates and
    # element refs from the expanded tool set.
    # ------------------------------------------------------------------

    async def _resolve_coordinates(self, arguments: dict) -> tuple[int, int] | str:
        """Return absolute pixel coordinates from arguments, or an error string.

        Resolution order (matching the Yutori agent loop):
        1. If ``ref`` is present, try to resolve it to viewport pixels.
           Ref resolution also scrolls the element into view.
        2. If ref resolution fails (or no ref), fall back to denormalizing
           ``coordinates`` from n1's 1000x1000 space.
        3. If neither ref nor coordinates are usable, return an error.
        """
        coords = arguments.get("coordinates")
        ref = arguments.get("ref")

        # Try ref first — it also scrolls the element into view.
        if ref:
            result_json = await _evaluate_js(self._page, "get_element_by_ref.js", ref)
            result = json.loads(result_json)
            if result.get("success"):
                px = result["coordinates"]
                return int(px[0]), int(px[1])
            msg = result.get("message", "Unknown error")
            if coords and len(coords) == 2:
                logger.warning(f"Ref {ref} failed ({msg}), falling back to coordinates {coords}")
            else:
                return f"[ERROR] Ref resolution failed for {ref}: {msg}"

        if coords and len(coords) == 2:
            return denormalize_coordinates(coords, self.viewport_width, self.viewport_height)

        return "[ERROR] No coordinates or ref provided"

    @staticmethod
    def _map_modifier(modifier: str | None) -> str | None:
        """Map n1.5 modifier name to Playwright key name via the full key map."""
        if not modifier:
            return None
        mapped = map_key_to_playwright(modifier)
        return mapped[0] if mapped else modifier

    # ------------------------------------------------------------------
    # Action execution — n1.5 action space
    # ------------------------------------------------------------------

    async def _execute(self, tool_call: ChatCompletionMessageToolCall) -> str | None:
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            modifier = self._map_modifier(arguments.get("modifier"))

            # Helper: resolve coordinates, returning error string on failure.
            async def _coords(args: dict = arguments) -> tuple[int, int] | None:
                result = await self._resolve_coordinates(args)
                if isinstance(result, str):
                    nonlocal _coord_error
                    _coord_error = result
                    return None
                return result

            _coord_error: str | None = None

            # ---- Mouse click actions ----
            if action_name in ("left_click", "double_click", "triple_click", "middle_click", "right_click"):
                resolved = await _coords()
                if resolved is None:
                    return _coord_error
                abs_x, abs_y = resolved
                button = {"middle_click": "middle", "right_click": "right"}.get(action_name, "left")
                click_count = {"double_click": 2, "triple_click": 3}.get(action_name, 1)

                if modifier:
                    await self._page.keyboard.down(modifier)
                await self._page.mouse.move(abs_x, abs_y)
                await asyncio.sleep(0.1)
                await self._page.mouse.click(abs_x, abs_y, button=button, click_count=click_count)
                if modifier:
                    await self._page.keyboard.up(modifier)
                await asyncio.sleep(0.5)
                return f"Clicked {click_count}x with {button}"

            # ---- Mouse movement actions ----
            elif action_name == "mouse_move":
                resolved = await _coords()
                if resolved is None:
                    return _coord_error
                abs_x, abs_y = resolved
                await self._page.mouse.move(abs_x, abs_y)
                await asyncio.sleep(0.3)
                return "Mouse moved and hovering"

            elif action_name == "mouse_down":
                resolved = await _coords()
                if resolved is None:
                    return _coord_error
                abs_x, abs_y = resolved
                await self._page.mouse.move(abs_x, abs_y)
                await self._page.mouse.down()
                await asyncio.sleep(0.3)
                return "Mouse button pressed"

            elif action_name == "mouse_up":
                resolved = await _coords()
                if resolved is None:
                    return _coord_error
                abs_x, abs_y = resolved
                await self._page.mouse.move(abs_x, abs_y)
                await self._page.mouse.up()
                await asyncio.sleep(0.3)
                return "Mouse button released"

            elif action_name == "drag":
                start_coords = arguments.get("start_coordinates", [0, 0])
                end_coords = arguments.get("coordinates", [0, 0])

                start = await _coords({"coordinates": start_coords})
                end = await _coords({"coordinates": end_coords})
                if start is None or end is None:
                    return _coord_error
                start_x, start_y = start
                end_x, end_y = end

                await self._page.mouse.move(start_x, start_y)
                await self._page.mouse.down()
                await self._page.mouse.move(end_x, end_y)
                await self._page.mouse.up()
                await asyncio.sleep(0.5)
                return "Dragged successfully"

            # ---- Scroll ----
            elif action_name == "scroll":
                direction = arguments.get("direction", "down")
                amount = arguments.get("amount", 3)
                coords = arguments.get("coordinates")

                px = amount * 100  # 1 unit ≈ 100px

                delta_x, delta_y = 0, 0
                if direction == "up":
                    delta_y = -px
                elif direction == "down":
                    delta_y = px
                elif direction == "left":
                    delta_x = -px
                elif direction == "right":
                    delta_x = px

                if modifier:
                    await self._page.keyboard.down(modifier)
                if coords and len(coords) == 2:
                    resolved = await _coords()
                    if resolved is None:
                        return _coord_error
                    abs_x, abs_y = resolved
                    await self._page.mouse.move(abs_x, abs_y)
                    await self._page.mouse.wheel(delta_x, delta_y)
                else:
                    await self._page.evaluate(f"window.scrollBy({delta_x}, {delta_y})")
                if modifier:
                    await self._page.keyboard.up(modifier)
                await asyncio.sleep(0.5)
                return f"Scrolled {direction}"

            # ---- Keyboard actions ----
            elif action_name == "type":
                text = arguments.get("text", "")
                chunk_size = 50
                for i in range(0, len(text), chunk_size):
                    await self._page.keyboard.type(text[i : i + chunk_size])
                await asyncio.sleep(0.5)
                return f"Typed {len(text)} characters"

            elif action_name == "key_press":
                key_expr = arguments.get("key", "")
                key_presses = map_key_to_playwright(key_expr)
                for key in key_presses:
                    await self._page.keyboard.press(key)
                await asyncio.sleep(0.3)
                return f"Pressed key: {key_expr}"

            elif action_name == "hold_key":
                key_expr = arguments.get("key", "")
                duration = arguments.get("duration")
                key_presses = map_key_to_playwright(key_expr)
                if duration is not None and duration > 0:
                    individual_keys = []
                    for key in key_presses:
                        individual_keys.extend(key.split("+"))
                    for key in individual_keys:
                        await self._page.keyboard.down(key)
                    await asyncio.sleep(min(duration, 100))
                    for key in reversed(individual_keys):
                        await self._page.keyboard.up(key)
                    await asyncio.sleep(0.3)
                    return f"Held key '{key_expr}' for {duration}s"
                else:
                    for key in key_presses:
                        await self._page.keyboard.press(key)
                    await asyncio.sleep(0.3)
                    return f"Pressed key: {key_expr}"

            # ---- Navigation actions ----
            elif action_name == "goto_url":
                url = arguments.get("url", "")
                if "://" not in url:
                    url = f"https://{url}"
                await self._page.goto(url)
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
                return f"Navigated to {url}"

            elif action_name == "go_back":
                await self._page.go_back()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.5)
                return "Navigated back"

            elif action_name == "go_forward":
                await self._page.go_forward()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.5)
                return "Navigated forward"

            elif action_name == "refresh":
                await self._page.reload()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
                return "Refreshed the page"

            elif action_name == "wait":
                duration = max(0, min(arguments.get("duration", 5), 100))
                await asyncio.sleep(duration)
                return f"Waited {duration}s"

            # ---- Expanded tool set actions ----
            elif action_name == "extract_elements":
                filter_type = arguments.get("filter", "visible")
                result = await _evaluate_js(self._page, "extract_dom_elements.js", filter_type)
                dom_data = json.loads(result) if isinstance(result, str) else result
                return dom_data.get("pageContent", "") if isinstance(dom_data, dict) else str(result)

            elif action_name == "find":
                text = arguments.get("text", "")
                # Get full DOM tree then do a simple text search
                result = await _evaluate_js(self._page, "extract_dom_elements.js", "all")
                dom_data = json.loads(result) if isinstance(result, str) else result
                dom_tree = dom_data.get("pageContent", "") if isinstance(dom_data, dict) else str(result)
                lines = [line for line in dom_tree.split("\n") if text.lower() in line.lower()]
                if lines:
                    return f"Found {len(lines)} element(s) matching \"{text}\":\n" + "\n".join(lines[:20])
                return f'No elements matching "{text}" found on the page.'

            elif action_name == "set_element_value":
                ref = arguments.get("ref", "")
                value = arguments.get("value", "")
                result_json = await _evaluate_js(self._page, "set_element_value.js", ref, value)
                result_data = json.loads(result_json)
                return result_data.get("message", "set_element_value completed")

            elif action_name == "execute_js":
                js_code = arguments.get("text", "")
                raw = await self._page.evaluate(js_code)
                if raw is None:
                    return "undefined"
                if isinstance(raw, (dict, list)):
                    return json.dumps(raw, indent=2)
                return str(raw)

            else:
                logger.warning(f"Unknown action: {action_name}")
                return f"[ERROR] Unknown action: {action_name}"

        except Exception as e:
            logger.error(f"Error executing {action_name}: {e}")
            return f"[ERROR] Error executing {action_name}: {e}"


async def main():
    logger.remove()
    logger.level("DEBUG", color="<fg #808080>")
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{file}</cyan>:<cyan>{line:>3}</cyan> | "
            "<level>{message}</level>{exception}"
        ),
        colorize=True,
    )

    default_config = Config()
    parser = argparse.ArgumentParser(description="Example of using Yutori n1.5 API to perform a web browsing task")
    parser.add_argument("--task", default=default_config.task, help="The task to perform")
    parser.add_argument("--start-url", default=default_config.start_url, help="Starting URL")
    parser.add_argument(
        "--api-key",
        default=default_config.api_key,
        help="Yutori API key, or set YUTORI_API_KEY in environment variables",
    )
    parser.add_argument("--base-url", default=default_config.base_url, help="Yutori n1.5 base URL")
    parser.add_argument("--model", default=default_config.model, help="Yutori n1.5 model")
    parser.add_argument("--temperature", type=float, default=default_config.temperature, help="Yutori n1.5 temperature")
    parser.add_argument(
        "--tool-set", default=default_config.tool_set,
        choices=[TOOL_SET_CORE, TOOL_SET_EXPANDED, "core", "expanded"],
        help="Tool set to use (default: core)",
    )
    parser.add_argument(
        "--disable-tools", nargs="*", default=default_config.disable_tools,
        help="Tool names to disable from the tool set",
    )
    parser.add_argument(
        "--json-schema", type=json.loads, default=None,
        help='JSON Schema for structured output, e.g. \'{"type":"object","properties":{"names":{"type":"array","items":{"type":"string"}}},"required":["names"]}\'',
    )
    parser.add_argument("--max-steps", type=int, default=default_config.max_steps, help="Maximum number of steps")
    parser.add_argument("--viewport-width", type=int, default=default_config.viewport_width, help="Viewport width")
    parser.add_argument("--viewport-height", type=int, default=default_config.viewport_height, help="Viewport height")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument(
        "--max-request-bytes", type=int, default=default_config.max_request_bytes,
        help="Max payload size in bytes before trimming old screenshots",
    )
    parser.add_argument(
        "--keep-recent-screenshots", type=int, default=default_config.keep_recent_screenshots,
        help="Number of recent screenshots to protect from trimming",
    )
    args = parser.parse_args()
    args.tool_set = _TOOL_SET_ALIASES.get(args.tool_set, args.tool_set)
    config = Config.model_validate(vars(args))

    agent = Agent(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        tool_set=config.tool_set,
        disable_tools=config.disable_tools,
        json_schema=config.json_schema,
        max_steps=config.max_steps,
        viewport_width=config.viewport_width,
        viewport_height=config.viewport_height,
        headless=config.headless,
        max_request_bytes=config.max_request_bytes,
        keep_recent_screenshots=config.keep_recent_screenshots,
    )

    result = await agent.run(config.task, config.start_url)
    logger.info(f"Final result: {result or '(No final response from model)'}")


if __name__ == "__main__":
    asyncio.run(main())
