#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API with the Navigator n1.5 model.

Navigator n1.5 introduces a new action space with renamed tools, selectable
tool sets, optional structured JSON output, and lowercase key names.

Replay logging in this example is optional. Here, "replay" means saving the
agent trajectory to local files so you can inspect screenshots, actions, and
raw request/response payloads in `visualization.html` after the run.

Custom tools: `navigator_n1_5_custom_tools.py` and `navigator_n1_5_memo.py` subclass
the `Agent` below, set `self.custom_tools`, and override `_dispatch_custom_tool`.

Navigator n1.5 features:
- model: "n1.5-latest"
- tool_set / disable_tools: select which built-in tools the model can use
- json_schema: request structured output (returned as parsed_json on the response)
- Actions include mouse_move, middle_click, mouse_down, mouse_up, go_forward, hold_key, and more
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
from typing import Any

from _common import (
    BrowserAgentMixin,
    add_agent_arguments,
    add_browser_arguments,
    add_model_arguments,
    add_payload_trim_arguments,
    add_replay_arguments,
    add_task_arguments,
    configure_example_logging,
    llm_retry,
    run_example_agent,
)
from loguru import logger
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

from yutori import AsyncYutoriClient
from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import (
    NAVIGATOR_N1_5_MODEL,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
    denormalize_coordinates,
    format_stop_and_summarize,
    format_task_with_context,
    map_key_to_playwright,
    map_keys_individual,
)
from yutori.navigator.payload import DEFAULT_KEEP_RECENT_SCREENSHOTS, DEFAULT_MAX_REQUEST_BYTES
from yutori.navigator.tools import (
    EXECUTE_JS_SCRIPT,
    EXTRACT_ELEMENTS_SCRIPT,
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
    model: str = NAVIGATOR_N1_5_MODEL
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
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    keep_recent_screenshots: int = DEFAULT_KEEP_RECENT_SCREENSHOTS
    # optional local replay artifacts
    replay_dir: str | None = None
    replay_id: str | None = None


class Agent(BrowserAgentMixin):
    # Prefix for replay run ids; custom-tool subclasses override it.
    replay_prefix = "navigator_1_5"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = NAVIGATOR_N1_5_MODEL,
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
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        keep_recent_screenshots: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
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

        self._init_agent_state()
        self._request_messages: list | None = None
        # Extra OpenAI-style function tools sent alongside the built-in browser actions;
        # custom-tool subclasses populate this and handle the calls in _dispatch_custom_tool.
        self.custom_tools: list[dict] = []

    async def run(self, task: str, start_url: str) -> str:
        # Keep original task for stop-and-summarize; format with context for the model
        original_task = task
        task = format_task_with_context(
            task,
            user_timezone=self.user_timezone,
            user_location=self.user_location,
        )

        self._start_run(task, start_url, replay_prefix=self.replay_prefix)
        self._request_messages = None

        final_response = ""

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

    @llm_retry
    async def _call_llm_with_retries(self) -> ChatCompletion:
        extra_fields: dict[str, Any] = {
            "tool_set": self.tool_set,
            "disable_tools": self.disable_tools or None,
            "json_schema": self.json_schema,
        }
        if self.custom_tools:
            extra_fields["tools"] = self.custom_tools
        return await self._call_llm(self._trim_request_messages(), extra_fields=extra_fields)

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
        """Map a single Navigator n1.5 modifier name to a Playwright key name.

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
    # Action execution — Navigator n1.5 action space
    # ------------------------------------------------------------------

    def _url_suffix(self) -> str:
        """Current page URL, appended to every tool result."""
        return f"\nCurrent URL: {self._page.url}"

    async def _finish_action(self, result: str | None) -> str | None:
        await self._wait_for_page_ready()
        return result

    async def _navigate_and_finish(self, nav_coro, *, sleep: float, message: str) -> str | None:
        """Await a Playwright navigation call, wait for load, settle, then finish the action.

        Shared by the ``goto_url``/``go_back``/``go_forward``/``refresh`` branches of
        :meth:`_execute`, which each awaited a different navigation call, waited for
        ``"domcontentloaded"``, slept for their own fixed duration, and finished with
        their own message -- otherwise identical.
        """
        await nav_coro
        await self._page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(sleep)
        return await self._finish_action(message)

    async def _move_with_modifier_held(
        self,
        modifier: str | None,
        abs_x: int,
        abs_y: int,
        action: Any,
        *,
        pre_action_sleep: float = 0,
    ) -> None:
        """Move the mouse to ``(abs_x, abs_y)``, optionally holding ``modifier`` down, run ``action``, then release it.

        Shared by the click and scroll branches of :meth:`_execute`, which each conditionally
        pressed a modifier key, moved the mouse, performed their own follow-up mouse action,
        then released the modifier -- otherwise identical bracketing. ``action`` is an
        already-created coroutine (e.g. ``self._page.mouse.click(...)``); ``pre_action_sleep``
        covers the click branch's extra settle delay between the move and the click itself.
        """
        if modifier:
            await self._page.keyboard.down(modifier)
        await self._page.mouse.move(abs_x, abs_y)
        if pre_action_sleep:
            await asyncio.sleep(pre_action_sleep)
        await action
        if modifier:
            await self._page.keyboard.up(modifier)

    async def _move_mouse_and_finish(self, arguments: dict, *, mouse_action: str | None, message: str) -> str | None:
        """Resolve coordinates, move the mouse there, optionally press/release, then finish.

        Shared by the ``mouse_move``/``mouse_down``/``mouse_up`` branches of :meth:`_execute`,
        which each resolved coordinates, moved the mouse there, performed at most one of
        ``mouse.down()``/``mouse.up()``, slept 0.3s, and finished with their own message --
        otherwise identical. ``mouse_action`` is ``"down"``, ``"up"``, or ``None`` (plain move).
        """
        resolved = await self._resolve_coordinates(arguments)
        if isinstance(resolved, str):
            return resolved
        abs_x, abs_y = resolved
        await self._page.mouse.move(abs_x, abs_y)
        if mouse_action == "down":
            await self._page.mouse.down()
        elif mouse_action == "up":
            await self._page.mouse.up()
        await asyncio.sleep(0.3)
        return await self._finish_action(message)

    async def _press_key_and_finish(self, key_expr: str) -> str | None:
        """Map ``key_expr`` to Playwright key names, press each in sequence, then finish.

        Shared by the ``key_press`` branch and ``hold_key``'s no-duration fallback of
        :meth:`_execute`, which each mapped the same key expression, pressed the resulting
        keys, slept 0.3s, and finished with the same "Pressed key: ..." message --
        otherwise identical.
        """
        key_presses = map_key_to_playwright(key_expr)
        for key in key_presses:
            await self._page.keyboard.press(key)
        await asyncio.sleep(0.3)
        return await self._finish_action(f"Pressed key: {key_expr}")

    async def _execute(self, tool_call: ChatCompletionMessageToolCall) -> str | None:
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            custom_result = await self._dispatch_custom_tool(action_name, arguments)
            if custom_result is not None:
                return custom_result

            modifier = self._map_modifier(arguments.get("modifier"))

            # ---- Mouse click actions ----
            if action_name in ("left_click", "double_click", "triple_click", "middle_click", "right_click"):
                resolved = await self._resolve_coordinates(arguments)
                if isinstance(resolved, str):
                    return resolved
                abs_x, abs_y = resolved
                button = {"middle_click": "middle", "right_click": "right"}.get(action_name, "left")
                click_count = {"double_click": 2, "triple_click": 3}.get(action_name, 1)

                await self._move_with_modifier_held(
                    modifier,
                    abs_x,
                    abs_y,
                    self._page.mouse.click(abs_x, abs_y, button=button, click_count=click_count),
                    pre_action_sleep=0.1,
                )
                await asyncio.sleep(0.5)
                return await self._finish_action(f"Clicked {click_count}x with {button}")

            # ---- Mouse movement actions ----
            elif action_name == "mouse_move":
                return await self._move_mouse_and_finish(
                    arguments, mouse_action=None, message="Mouse moved and hovering"
                )

            elif action_name == "mouse_down":
                return await self._move_mouse_and_finish(arguments, mouse_action="down", message="Mouse button pressed")

            elif action_name == "mouse_up":
                return await self._move_mouse_and_finish(arguments, mouse_action="up", message="Mouse button released")

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

                    await self._move_with_modifier_held(
                        modifier, abs_x, abs_y, self._page.mouse.wheel(delta_x, delta_y)
                    )
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
                return await self._press_key_and_finish(key_expr)

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
                    return await self._press_key_and_finish(key_expr)

            # ---- Navigation actions ----
            elif action_name == "goto_url":
                url = arguments.get("url", "")
                if "://" not in url:
                    url = f"https://{url}"
                return await self._navigate_and_finish(self._page.goto(url), sleep=1, message=f"Navigated to {url}")

            elif action_name == "go_back":
                return await self._navigate_and_finish(self._page.go_back(), sleep=0.5, message="Navigated back")

            elif action_name == "go_forward":
                return await self._navigate_and_finish(self._page.go_forward(), sleep=0.5, message="Navigated forward")

            elif action_name == "refresh":
                return await self._navigate_and_finish(self._page.reload(), sleep=1, message="Refreshed the page")

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


async def main():
    configure_example_logging()

    default_config = Config()
    parser = argparse.ArgumentParser(
        description="Example of using the Yutori Navigator API (Navigator n1.5) to perform a web browsing task"
    )
    add_task_arguments(parser, default_config)
    add_model_arguments(parser, default_config, api_label="Yutori Navigator n1.5")
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

    await run_example_agent(Agent, config)


if __name__ == "__main__":
    asyncio.run(main())
