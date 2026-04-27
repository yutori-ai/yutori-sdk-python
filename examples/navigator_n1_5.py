#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API with the Navigator-n1.5 model.

Navigator-n1.5 introduces a new action space with renamed tools, selectable
tool sets, optional structured JSON output, and lowercase key names.

Replay logging in this example is optional. Here, "replay" means saving the
agent trajectory to local files so you can inspect screenshots, actions, and
raw request/response payloads in `visualization.html` after the run.

Key differences from Navigator-n1:
- model: "n1.5-latest" (instead of "n1-latest")
- tool_set / disable_tools: select which built-in tools the model can use
- json_schema: request structured output (returned as parsed_json on the response)
- Renamed actions: hover → mouse_move; key_press param renamed from key_comb → key
- New actions: middle_click, mouse_down, mouse_up, go_forward, hold_key
- type no longer has press_enter_after / clear_before_typing
- Key names are lowercase (e.g. ctrl+c, enter, left) instead of Playwright names

Usage:
    yutori auth login  # or export YUTORI_API_KEY=...
    uv sync --extra examples

    # Basic
    uv run python examples/navigator_n1_5.py --task "List the team member names" --start-url "https://www.yutori.com"

    # Expanded tool set (adds extract_elements, find, set_element_value, execute_js)
    uv run python examples/navigator_n1_5.py --tool-set expanded --task "Fill out the contact form" --start-url "https://example.com"

    # Disable specific tools
    uv run python examples/navigator_n1_5.py --disable-tools hold_key drag --task "Search for flights" --start-url "https://google.com/flights"

    # Structured JSON output via --json-schema
    uv run python examples/navigator_n1_5.py \
        --task "List the team member names" \
        --start-url "https://www.yutori.com" \
        --json-schema '{"type":"object","properties":{"names":{"type":"array","items":{"type":"string"}}},\
"required":["names"]}'
"""

import argparse
import asyncio
import json

from loguru import logger
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from _common import (
    RETRYABLE_EXCEPTIONS,
    add_agent_arguments,
    add_browser_arguments,
    add_model_arguments,
    add_payload_trim_arguments,
    add_replay_arguments,
    add_task_arguments,
    configure_example_logging,
)
from yutori import AsyncYutoriClient
from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import (
    N1_5_MODEL,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
    aplaywright_screenshot_to_data_url,
    denormalize_coordinates,
    format_stop_and_summarize,
    format_task_with_context,
    map_key_to_playwright,
    map_keys_individual,
)
from yutori.navigator.loop import update_trimmed_history
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.replay import TrajectoryRecorder, make_run_id, sanitize_step_payload  # Optional replay helpers.
from yutori.navigator.tools import (
    EXTRACT_ELEMENTS_SCRIPT,
    EXECUTE_JS_SCRIPT,
    FIND_SCRIPT,
    GET_ELEMENT_BY_REF_SCRIPT,
    SET_ELEMENT_VALUE_SCRIPT,
    evaluate_tool_script,
)

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
    base_url: str = DEFAULT_BASE_URL
    model: str = N1_5_MODEL
    temperature: float = 0.3
    tool_set: str = TOOL_SET_CORE
    disable_tools: list[str] = Field(default_factory=list)
    json_schema: dict | None = None
    # user context
    user_timezone: str = "America/Los_Angeles"
    user_location: str = "San Francisco, CA, US"
    # agent
    max_steps: int = 100
    # browser
    viewport_width: int = 1280
    viewport_height: int = 800
    headless: bool = False
    # payload management
    max_request_bytes: int = 9_500_000
    keep_recent_screenshots: int = 6
    # optional local replay artifacts
    replay_dir: str | None = None
    replay_id: str | None = None


class Agent:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = N1_5_MODEL,
        temperature: float = 0.3,
        tool_set: str = TOOL_SET_CORE,
        disable_tools: list[str] | None = None,
        json_schema: dict | None = None,
        user_timezone: str = "America/Los_Angeles",
        user_location: str = "San Francisco, CA, US",
        max_steps: int = 100,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        headless: bool = False,
        max_request_bytes: int = 9_500_000,
        keep_recent_screenshots: int = 6,
        replay_dir: str | None = None,
        replay_id: str | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.tool_set = tool_set
        self.disable_tools = disable_tools or []
        self.json_schema = json_schema
        self.user_timezone = user_timezone
        self.user_location = user_location
        self.max_steps = max_steps
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.headless = headless
        self.max_request_bytes = max_request_bytes
        self.keep_recent_screenshots = keep_recent_screenshots
        self.replay_dir = replay_dir
        self.replay_id = replay_id

        self._client: AsyncYutoriClient | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._page_ready_checker = PageReadyChecker(
            timeout=30,
            initial_wait=2.0,
            wait_after_ready=1.0,
            replace_native_select_dropdown=True,
            disable_new_tabs=True,
            disable_printing=True,
        )
        # Replay bookkeeping is optional and only used when writing local artifacts.
        self._replay: TrajectoryRecorder | None = None
        self._messages: list = []
        self._request_messages: list | None = None
        # Stored only so the replay viewer can show raw request/response JSON per step.
        self._step_payloads: list[dict] = []
        self._step_count = 0

    async def run(self, task: str, start_url: str) -> str:
        # Keep original task for stop-and-summarize; format with context for the model
        original_task = task
        task = format_task_with_context(
            task,
            user_timezone=self.user_timezone,
            user_location=self.user_location,
        )

        logger.info(f"Task: {task}")
        logger.info(f"Starting URL: {start_url}")

        self._messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]
        self._message_index = 0
        self._step_count = 0
        self._request_messages = None
        self._step_payloads = []
        self._replay = None

        final_response = ""
        # Replay output is opt-in; the loop still works without any of this.
        if self.replay_dir:
            replay_id = self.replay_id or make_run_id(prefix="navigator_1_5", label=task)
            self._replay = TrajectoryRecorder(self.replay_dir, replay_id)
            logger.info(f"Replay artifacts: {self._replay.item_dir}")

        async with (
            AsyncYutoriClient(base_url=self.base_url) as client,
            async_playwright() as playwright,
        ):
            try:
                self._client = client
                await self._init_browser(playwright)
                await self._page.goto(start_url)
                await self._page.wait_for_load_state("domcontentloaded")
                await self._wait_for_page_ready()

                while self._step_count < self.max_steps:
                    self._step_count += 1
                    logger.debug(f"Step {self._step_count}, URL: {self._page.url}")

                    response = await self._predict()

                    # Log raw model prediction
                    logger.info(f"Response: {response}")

                    # Store the assistant's response
                    self._messages.append(response.model_dump(exclude_none=True))
                    self._message_index = len(self._messages)
                    await self._persist_replay()

                    if response.content:
                        final_response = response.content

                    # Stop when there are no tool calls
                    if not response.tool_calls:
                        logger.info("Task completed (no more tool calls)")
                        break

                    # Execute the action(s)
                    for tool_call in response.tool_calls:
                        result = await self._execute(tool_call)
                        # Append current URL to every tool result
                        if result:
                            result += self._url_suffix()
                        content = [{"type": "text", "text": result}] if result else []
                        self._messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})
                    await self._persist_replay()
                else:
                    # Loop exhausted without break — model was still working when limit hit
                    logger.warning(f"Reached maximum steps ({self.max_steps})")
                    final_response = await self._stop_and_summarize(original_task)
                    await self._persist_replay()

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                final_response = await self._stop_and_summarize(original_task)
            except Exception as e:
                logger.error(f"Agent error: {e}")
                final_response = await self._stop_and_summarize(original_task)
            finally:
                await self._persist_replay()
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
        await self._wait_for_page_ready(fast_mode=True)
        return await aplaywright_screenshot_to_data_url(
            self._page,
            resize_to=(self.viewport_width, self.viewport_height),
        )

    async def _wait_for_page_ready(self, fast_mode: bool = False) -> None:
        if not await self._page_ready_checker.wait_until_ready(self._page, fast_mode=fast_mode):
            logger.warning(f"Page did not fully stabilize before continuing: {self._page.url}")

    def _clip_image_url(self, url: str, max_len: int = 50) -> str:
        if url.startswith("data:image"):
            prefix_end = url.find(",") + 1
            if prefix_end > 0 and len(url) > prefix_end + max_len:
                return url[: prefix_end + 20] + "...[clipped]"
        return url if len(url) <= max_len else url[:max_len] + "..."

    def _format_message_for_log(self, message: dict) -> dict:
        content = message.get("content")
        if not isinstance(content, list):
            return message

        clipped_content = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url" and "url" in item.get("image_url", {}):
                clipped_content.append({
                    **item,
                    "image_url": {"url": self._clip_image_url(item["image_url"]["url"])},
                })
            else:
                clipped_content.append(item)
        return {**message, "content": clipped_content}

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _call_llm_with_retries(self) -> ChatCompletion:
        self._request_messages, size_bytes, removed = update_trimmed_history(
            self._messages,
            self._request_messages,
            max_bytes=self.max_request_bytes,
            keep_recent=self.keep_recent_screenshots,
        )
        if removed:
            logger.info(f"Trimmed {removed} old screenshot(s); payload ~{size_bytes / (1024 * 1024):.2f} MB")

        # This copy is only for replay output; the request itself just uses the same fields directly.
        request_payload = {
            "model": self.model,
            "messages": self._request_messages,
            "temperature": self.temperature,
            "tool_set": self.tool_set,
            "disable_tools": self.disable_tools or None,
            "json_schema": self.json_schema,
        }
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=self._request_messages,
                temperature=self.temperature,
                tool_set=self.tool_set,
                disable_tools=self.disable_tools or None,
                json_schema=self.json_schema,
            ),
            timeout=120.0,  # 2 minutes
        )
        # Replay output records the sanitized raw request/response pair for this step.
        self._step_payloads.append(
            sanitize_step_payload(
                {
                    "step_num": self._step_count,
                    "request": request_payload,
                    "response": response.model_dump(exclude_none=True),
                }
            )
        )
        return response

    async def _predict(self) -> ChatCompletion:
        screenshot_url = await self._take_screenshot()

        last_content = self._messages[-1]["content"]
        # Content separator between text and image
        last_content.append({"type": "text", "text": "\n\n"})
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

    async def _stop_and_summarize(self, task: str) -> str:
        """Send a final stop message to get the model to summarize its progress.

        Takes a screenshot, appends a "Stop here. Summarize..." user message,
        and calls the model one last time to produce a text summary rather
        than returning nothing on max steps, errors, or interruption.
        """
        try:
            # Take a final screenshot so the model can see the current state
            screenshot_url = await self._take_screenshot()
            stop_message = format_stop_and_summarize(task)
            self._messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": stop_message},
                    {"type": "text", "text": "\n\n"},
                    {"type": "image_url", "image_url": {"url": screenshot_url, "detail": "high"}},
                ],
            })

            logger.info("Requesting final summary from model...")
            response = await self._call_llm_with_retries()
            message = response.choices[0].message
            self._messages.append(message.model_dump(exclude_none=True))
            return message.content or ""
        except Exception as e:
            logger.error(f"Failed to get stop summary: {e}")
            return ""

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
           ``coordinates`` from the Navigator 1000x1000 space.
        3. If neither ref nor coordinates are usable, return an error.
        """
        coords = arguments.get("coordinates")
        ref = arguments.get("ref")

        # Try ref first — it also scrolls the element into view.
        if ref:
            result = await evaluate_tool_script(self._page, GET_ELEMENT_BY_REF_SCRIPT, ref)
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
        """Map a single Navigator-n1.5 modifier name to a Playwright key name.

        The modifier field is always a single key (ctrl, shift, alt, meta,
        command, super) — not a combo. Uses the key map for lookup but
        rejects combo/sequence expressions since keyboard.down()/up()
        only accept single key names.
        """
        if not modifier:
            return None
        # map_key_to_playwright handles combos/sequences; we only want a
        # single key, so split the result and take just the first token.
        mapped = map_key_to_playwright(modifier)
        if not mapped:
            return modifier
        # If somehow a combo slipped through (e.g. "ctrl+shift"), take
        # only the first individual key.
        return mapped[0].split("+")[0]

    # ------------------------------------------------------------------
    # Action execution — Navigator-n1.5 action space
    # ------------------------------------------------------------------

    def _url_suffix(self) -> str:
        """Current page URL, appended to every tool result."""
        return f"\nCurrent URL: {self._page.url}"

    async def _finish_action(self, result: str | None) -> str | None:
        await self._wait_for_page_ready()
        return result

    async def _execute(self, tool_call: ChatCompletionMessageToolCall) -> str | None:
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            modifier = self._map_modifier(arguments.get("modifier"))

            # ---- Mouse click actions ----
            if action_name in ("left_click", "double_click", "triple_click", "middle_click", "right_click"):
                resolved = await self._resolve_coordinates(arguments)
                if isinstance(resolved, str):
                    return resolved
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
                return await self._finish_action(f"Clicked {click_count}x with {button}")

            # ---- Mouse movement actions ----
            elif action_name == "mouse_move":
                resolved = await self._resolve_coordinates(arguments)
                if isinstance(resolved, str):
                    return resolved
                abs_x, abs_y = resolved
                await self._page.mouse.move(abs_x, abs_y)
                await asyncio.sleep(0.3)
                return await self._finish_action("Mouse moved and hovering")

            elif action_name == "mouse_down":
                resolved = await self._resolve_coordinates(arguments)
                if isinstance(resolved, str):
                    return resolved
                abs_x, abs_y = resolved
                await self._page.mouse.move(abs_x, abs_y)
                await self._page.mouse.down()
                await asyncio.sleep(0.3)
                return await self._finish_action("Mouse button pressed")

            elif action_name == "mouse_up":
                resolved = await self._resolve_coordinates(arguments)
                if isinstance(resolved, str):
                    return resolved
                abs_x, abs_y = resolved
                await self._page.mouse.move(abs_x, abs_y)
                await self._page.mouse.up()
                await asyncio.sleep(0.3)
                return await self._finish_action("Mouse button released")

            elif action_name == "drag":
                start_coords = arguments.get("start_coordinates", [0, 0])
                end_coords = arguments.get("coordinates", [0, 0])

                start = await self._resolve_coordinates({"coordinates": start_coords})
                if isinstance(start, str):
                    return start
                end = await self._resolve_coordinates({"coordinates": end_coords})
                if isinstance(end, str):
                    return end
                start_x, start_y = start
                end_x, end_y = end

                await self._page.mouse.move(start_x, start_y)
                await self._page.mouse.down()
                await self._page.mouse.move(end_x, end_y)
                await self._page.mouse.up()
                await asyncio.sleep(0.5)
                return await self._finish_action("Dragged successfully")

            # ---- Scroll ----
            elif action_name == "scroll":
                ref = arguments.get("ref")
                coords = arguments.get("coordinates")

                if ref:
                    # Ref-based scroll: get_element_by_ref.js calls scrollIntoView(),
                    # which handles the scrolling. No additional mouse.wheel needed.
                    resolved = await self._resolve_coordinates(arguments)
                    if isinstance(resolved, str):
                        return resolved
                    await asyncio.sleep(0.5)
                    return await self._finish_action("Scrolled to element")
                elif coords and len(coords) == 2:
                    abs_x, abs_y = denormalize_coordinates(coords, self.viewport_width, self.viewport_height)
                    direction = arguments.get("direction", "down")
                    amount = arguments.get("amount", 3)

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
                    await self._page.mouse.move(abs_x, abs_y)
                    await self._page.mouse.wheel(delta_x, delta_y)
                    if modifier:
                        await self._page.keyboard.up(modifier)
                    await asyncio.sleep(0.5)
                    return await self._finish_action(f"Scrolled {direction}")
                else:
                    return "[ERROR] No coordinates or ref provided for scroll"

            # ---- Keyboard actions ----
            elif action_name == "type":
                text = arguments.get("text", "")
                chunk_size = 50
                for i in range(0, len(text), chunk_size):
                    await self._page.keyboard.type(text[i : i + chunk_size])
                await asyncio.sleep(0.5)
                return await self._finish_action(f"Typed {len(text)} characters")

            elif action_name == "key_press":
                key_expr = arguments.get("key", "")
                key_presses = map_key_to_playwright(key_expr)
                for key in key_presses:
                    await self._page.keyboard.press(key)
                await asyncio.sleep(0.3)
                return await self._finish_action(f"Pressed key: {key_expr}")

            elif action_name == "hold_key":
                key_expr = arguments.get("key", "")
                duration = arguments.get("duration")
                if duration is not None and duration > 0:
                    individual_keys = map_keys_individual(key_expr)
                    for key in individual_keys:
                        await self._page.keyboard.down(key)
                    await asyncio.sleep(min(duration, 100))
                    for key in reversed(individual_keys):
                        await self._page.keyboard.up(key)
                    await asyncio.sleep(0.3)
                    return await self._finish_action(f"Held key '{key_expr}' for {duration}s")
                else:
                    key_presses = map_key_to_playwright(key_expr)
                    for key in key_presses:
                        await self._page.keyboard.press(key)
                    await asyncio.sleep(0.3)
                    return await self._finish_action(f"Pressed key: {key_expr}")

            # ---- Navigation actions ----
            elif action_name == "goto_url":
                url = arguments.get("url", "")
                if "://" not in url:
                    url = f"https://{url}"
                await self._page.goto(url)
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
                return await self._finish_action(f"Navigated to {url}")

            elif action_name == "go_back":
                await self._page.go_back()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.5)
                return await self._finish_action("Navigated back")

            elif action_name == "go_forward":
                await self._page.go_forward()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.5)
                return await self._finish_action("Navigated forward")

            elif action_name == "refresh":
                await self._page.reload()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
                return await self._finish_action("Refreshed the page")

            elif action_name == "wait":
                duration = max(0, min(arguments.get("duration", 5), 100))
                await asyncio.sleep(duration)
                return await self._finish_action(f"Waited {duration}s")

            # ---- Expanded tool set actions ----
            elif action_name == "extract_elements":
                filter_type = arguments.get("filter", "visible")
                dom_data = await evaluate_tool_script(self._page, EXTRACT_ELEMENTS_SCRIPT, filter_type)
                content = dom_data.get("pageContent", "")
                return await self._finish_action(content)

            elif action_name == "find":
                text = arguments.get("text", "")
                result = await evaluate_tool_script(self._page, FIND_SCRIPT, text)
                if not result.get("success", False):
                    return await self._finish_action(f'[ERROR] {result.get("message", "find failed")}')
                matches = result.get("matches", [])
                total_matches = int(result.get("totalMatches", len(matches)))
                if total_matches:
                    return await self._finish_action(
                        f'Found {total_matches} element(s) matching "{text}":\n' + "\n".join(matches[:20])
                    )
                return await self._finish_action(f'No elements matching "{text}" found on the page.')

            elif action_name == "set_element_value":
                ref = arguments.get("ref", "")
                value = arguments.get("value", "")
                result_data = await evaluate_tool_script(self._page, SET_ELEMENT_VALUE_SCRIPT, ref, value)
                return await self._finish_action(result_data.get("message", "set_element_value completed"))

            elif action_name == "execute_js":
                js_code = arguments.get("text", "")
                result_data = await evaluate_tool_script(self._page, EXECUTE_JS_SCRIPT, js_code)
                if not result_data.get("success", False):
                    return await self._finish_action(f'[ERROR] {result_data.get("message", "execute_js failed")}')
                if not result_data.get("hasResult"):
                    return await self._finish_action("undefined")
                raw = result_data.get("result")
                return await self._finish_action(str(raw))

            else:
                logger.warning(f"Unknown action: {action_name}")
                return f"[ERROR] Unknown action: {action_name}"

        except Exception as e:
            logger.error(f"Error executing {action_name}: {e}")
            return f"[ERROR] Error executing {action_name}: {e}"

    async def _persist_replay(self) -> None:
        # Replay persistence is best-effort and not part of the agent loop itself.
        if self._replay is None:
            return
        try:
            await self._replay.save_messages(self._messages)
            await self._replay.save_step_payloads(self._step_payloads)
            await self._replay.save_html(self._messages, step_payloads=self._step_payloads)
        except Exception:
            logger.opt(exception=True).warning("Failed to write replay artifacts")


async def main():
    configure_example_logging()

    default_config = Config()
    parser = argparse.ArgumentParser(
        description="Example of using the Yutori Navigator API (Navigator-n1.5) to perform a web browsing task"
    )
    add_task_arguments(parser, default_config)
    add_model_arguments(parser, default_config, api_label="Yutori Navigator-n1.5")
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
        "--json-schema",
        type=json.loads,
        default=None,
        help=(
            'JSON Schema for structured output, e.g. '
            '\'{"type":"object","properties":{"names":{"type":"array","items":{"type":"string"}}},'
            '"required":["names"]}\''
        ),
    )
    parser.add_argument(
        "--timezone",
        dest="user_timezone",
        default=default_config.user_timezone,
        help="User timezone (e.g. America/New_York)",
    )
    parser.add_argument(
        "--location",
        dest="user_location",
        default=default_config.user_location,
        help="User location (e.g. New York, NY, US)",
    )
    add_agent_arguments(parser, default_config)
    add_browser_arguments(parser, default_config)
    add_payload_trim_arguments(parser, default_config)
    add_replay_arguments(parser, default_config)
    args = parser.parse_args()
    args.tool_set = _TOOL_SET_ALIASES.get(args.tool_set, args.tool_set)
    config = Config.model_validate(vars(args))

    agent = Agent(
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        tool_set=config.tool_set,
        disable_tools=config.disable_tools,
        json_schema=config.json_schema,
        user_timezone=config.user_timezone,
        user_location=config.user_location,
        max_steps=config.max_steps,
        viewport_width=config.viewport_width,
        viewport_height=config.viewport_height,
        headless=config.headless,
        max_request_bytes=config.max_request_bytes,
        keep_recent_screenshots=config.keep_recent_screenshots,
        replay_dir=config.replay_dir,
        replay_id=config.replay_id,
    )

    result = await agent.run(config.task, config.start_url)
    logger.info(f"Final result: {result or '(No final response from model)'}")


if __name__ == "__main__":
    asyncio.run(main())
