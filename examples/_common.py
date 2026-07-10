"""Shared helpers for runnable example scripts."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from typing import Any, Protocol

from loguru import logger
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from openai.types.chat import ChatCompletion
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from yutori.navigator import aplaywright_screenshot_to_data_url, denormalize_coordinates
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.replay import TrajectoryRecorder
from yutori.navigator.replay import _clip_image_url as _clip_image_url_impl

RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

llm_retry = retry(
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{file}</cyan>:<cyan>{line:>3}</cyan> | "
    "<level>{message}</level>{exception}"
)


def configure_example_logging() -> None:
    logger.remove()
    logger.level("DEBUG", color="<fg #808080>")
    logger.add(sys.stdout, format=_LOG_FORMAT, colorize=True)


def add_task_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument("--task", default=default_config.task, help="The task to perform")
    parser.add_argument("--start-url", default=default_config.start_url, help="Starting URL")


def add_model_arguments(parser: argparse.ArgumentParser, default_config, *, api_label: str) -> None:
    parser.add_argument("--base-url", default=default_config.base_url, help=f"{api_label} base URL")
    parser.add_argument("--model", default=default_config.model, help=f"{api_label} model")
    parser.add_argument(
        "--temperature",
        type=float,
        default=default_config.temperature,
        help=f"{api_label} temperature",
    )


def add_agent_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument("--max-steps", type=int, default=default_config.max_steps, help="Maximum number of steps")


def add_browser_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument("--viewport-width", type=int, default=default_config.viewport_width, help="Viewport width")
    parser.add_argument("--viewport-height", type=int, default=default_config.viewport_height, help="Viewport height")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")


def add_payload_trim_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=default_config.max_request_bytes,
        help="Max payload size in bytes before trimming old screenshots",
    )
    parser.add_argument(
        "--keep-recent-screenshots",
        type=int,
        default=default_config.keep_recent_screenshots,
        help="Number of recent screenshots to protect from trimming",
    )


def add_replay_arguments(parser: argparse.ArgumentParser, default_config) -> None:
    parser.add_argument(
        "--replay-dir",
        default=default_config.replay_dir,
        help="Optional directory for replay artifacts",
    )
    parser.add_argument("--replay-id", default=default_config.replay_id, help="Optional replay run id")


def build_agent_arg_parser(
    description: str,
    default_config: Any,
    *,
    api_label: str,
    include_payload_trim: bool = False,
) -> argparse.ArgumentParser:
    """Build the task/model/agent/browser/replay parser shared by the n1 example scripts' ``main()``.

    ``navigator_n1.py``, ``navigator_n1_memo.py``, and ``navigator_n1_custom_tools.py``
    previously each assembled this same argument list by hand (identical except for
    ``navigator_n1.py``'s extra payload-trim arguments, toggled here via
    ``include_payload_trim``). ``navigator_n1_5.py`` adds its own tool-set/json-schema
    arguments on top of a differently-ordered base set and builds its parser directly.
    """
    parser = argparse.ArgumentParser(description=description)
    add_task_arguments(parser, default_config)
    add_model_arguments(parser, default_config, api_label=api_label)
    add_agent_arguments(parser, default_config)
    add_browser_arguments(parser, default_config)
    if include_payload_trim:
        add_payload_trim_arguments(parser, default_config)
    add_replay_arguments(parser, default_config)
    return parser


def _click_coordinates(
    arguments: dict[str, Any],
    viewport_width: int,
    viewport_height: int,
) -> tuple[int, int]:
    """Resolve the ``coordinates`` argument (default ``[0, 0]``) to absolute pixel coordinates.

    Shared by the pointer actions in :func:`execute_n1_primitive_action` (the click
    variants and hover) that all read the same ``coordinates`` argument and denormalize
    it the same way.
    """
    coords = arguments.get("coordinates", [0, 0])
    return denormalize_coordinates(coords, viewport_width, viewport_height)


async def execute_n1_primitive_action(
    page: Any,
    action_name: str,
    arguments: dict[str, Any],
    viewport_width: int,
    viewport_height: int,
) -> bool:
    """Execute one of Navigator n1's built-in browser actions on ``page``.

    Shared by the n1 example agents that expose the full, unmodified n1
    action vocabulary (``navigator_n1.py``, ``navigator_n1_memo.py``, and
    ``navigator_n1_custom_tools.py``, which layers its own custom tool on
    top); ``navigator_n1_5.py`` targets a different model with a different
    action vocabulary, so it does not use this helper.

    Returns True if ``action_name`` was recognized and executed, False
    otherwise -- callers should treat False as "unknown action" (after first
    checking their own custom tool names, if any). Exceptions raised by
    Playwright calls propagate to the caller, which already wraps action
    execution in its own try/except.
    """
    if action_name == "left_click":
        abs_x, abs_y = _click_coordinates(arguments, viewport_width, viewport_height)
        await page.mouse.click(abs_x, abs_y)
        await asyncio.sleep(0.5)

    elif action_name == "double_click":
        abs_x, abs_y = _click_coordinates(arguments, viewport_width, viewport_height)
        await page.mouse.dblclick(abs_x, abs_y)
        await asyncio.sleep(0.5)

    elif action_name == "right_click":
        abs_x, abs_y = _click_coordinates(arguments, viewport_width, viewport_height)
        await page.mouse.click(abs_x, abs_y, button="right")
        await asyncio.sleep(0.5)

    elif action_name == "triple_click":
        abs_x, abs_y = _click_coordinates(arguments, viewport_width, viewport_height)
        await page.mouse.click(abs_x, abs_y, click_count=3)
        await asyncio.sleep(0.5)

    elif action_name == "type":
        text = arguments.get("text", "")
        press_enter = arguments.get("press_enter_after", True)
        clear_first = arguments.get("clear_before_typing", True)

        if clear_first:
            await page.keyboard.press("Control+a" if sys.platform != "darwin" else "Meta+a")
            await page.keyboard.press("Backspace")

        await page.keyboard.type(text)

        if press_enter:
            await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)

    elif action_name in ("key", "key_press"):
        key = arguments.get("key") or arguments.get("key_comb", "")
        key = "+".join("ControlOrMeta" if k == "Meta" else k for k in key.split("+"))
        await page.keyboard.press(key)
        await asyncio.sleep(0.3)

    elif action_name == "scroll":
        coords = arguments.get("coordinates") or arguments.get("coordinate", [500, 500])
        direction = arguments.get("direction", "down")
        amount = arguments.get("amount", 3)

        abs_x, abs_y = denormalize_coordinates(coords, viewport_width, viewport_height)
        scroll_delta = amount * (viewport_height * 0.1)

        delta_y = scroll_delta if direction == "down" else (-scroll_delta if direction == "up" else 0)
        delta_x = scroll_delta if direction == "right" else (-scroll_delta if direction == "left" else 0)

        await page.mouse.move(abs_x, abs_y)
        await page.mouse.wheel(delta_x, delta_y)
        await asyncio.sleep(0.5)

    elif action_name == "hover":
        abs_x, abs_y = _click_coordinates(arguments, viewport_width, viewport_height)
        await page.mouse.move(abs_x, abs_y)
        await asyncio.sleep(0.3)

    elif action_name == "drag":
        start_coords = arguments.get("start_coordinates") or arguments.get("startCoordinates", [0, 0])
        end_coords = arguments.get("coordinates") or arguments.get("endCoordinates", [0, 0])

        start_x, start_y = denormalize_coordinates(start_coords, viewport_width, viewport_height)
        end_x, end_y = denormalize_coordinates(end_coords, viewport_width, viewport_height)

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(end_x, end_y)
        await page.mouse.up()
        await asyncio.sleep(0.5)

    elif action_name in ("goto", "goto_url"):
        url = arguments.get("url", "")
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)

    elif action_name in ("back", "go_back"):
        await page.go_back()
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(0.5)

    elif action_name == "refresh":
        await page.reload()
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)

    elif action_name == "wait":
        await asyncio.sleep(5)

    else:
        return False

    return True


class SupportsAgentRun(Protocol):
    async def run(self, task: str, start_url: str) -> str: ...


async def run_example_agent(agent_cls: Callable[..., SupportsAgentRun], config: Any) -> str:
    """Build ``agent_cls`` from ``config`` and run it -- the shared tail of every example ``main()``.

    Config's fields (other than task/start_url, which go to ``agent.run()``) map 1:1 onto the
    agent's constructor kwargs by name.
    """
    agent = agent_cls(**config.model_dump(exclude={"task", "start_url"}))
    result = await agent.run(config.task, config.start_url)
    logger.info(f"Final result: {result or '(No final response from model)'}")
    return result


class SupportsBrowserAgentState(Protocol):
    """Instance attributes :class:`BrowserAgentMixin` methods expect from ``self``.

    Each example ``Agent.__init__`` sets these before the browser lifecycle
    or replay-persistence methods below are called.
    """

    headless: bool
    viewport_width: int
    viewport_height: int
    _browser: Any
    _page: Any
    _page_ready_checker: PageReadyChecker
    _replay: TrajectoryRecorder | None
    _messages: list
    _message_index: int
    _step_payloads: list[dict]

    async def _call_llm_with_retries(self) -> ChatCompletion: ...


class BrowserAgentMixin:
    """Playwright lifecycle, screenshot, and replay-persistence helpers shared by the example agents.

    Every ``examples/navigator_*.py`` script defines its own ``Agent`` class
    because the action-execution logic differs meaningfully per Navigator
    version (that divergence is the point of having separate examples).
    These methods, however, are identical boilerplate across all of
    them -- launching/closing the browser, taking a screenshot, waiting for
    page readiness, clipping image URLs for log output, formatting messages
    for log output, building the next model request from the latest
    screenshot, and persisting optional replay artifacts -- so they live
    here once instead of being copy-pasted into every script.

    ``navigator_n1_5.py`` defines its own ``_format_message_for_log`` and
    ``_predict`` (differently-styled/shaped implementations, since it targets
    a different model with a different action vocabulary) and so overrides
    this mixin's versions via normal MRO -- it is intentionally not part of
    this mechanical extraction.
    """

    async def _init_browser(self: SupportsBrowserAgentState, playwright) -> None:
        self._browser = await playwright.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height}
        )
        self._page = await context.new_page()
        await asyncio.sleep(1)

    async def _close_browser(self: SupportsBrowserAgentState) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None

    async def _take_screenshot(self: SupportsBrowserAgentState) -> str:
        await self._wait_for_page_ready(fast_mode=True)
        return await aplaywright_screenshot_to_data_url(
            self._page,
            resize_to=(self.viewport_width, self.viewport_height),
        )

    async def _wait_for_page_ready(self: SupportsBrowserAgentState, fast_mode: bool = False) -> None:
        if not await self._page_ready_checker.wait_until_ready(self._page, fast_mode=fast_mode):
            logger.warning(f"Page did not fully stabilize before continuing: {self._page.url}")

    async def _predict(self: SupportsBrowserAgentState) -> ChatCompletion:
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

    def _clip_image_url(self, url: str, max_len: int = 50) -> str:
        # Delegates to the canonical implementation in yutori.navigator.replay, passing
        # this call site's tighter length budget and plain (non-"[clipped]") suffix.
        return _clip_image_url_impl(url, max_len=max_len, prefix_keep=20, plain_suffix="...")

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

    async def _persist_replay(self: SupportsBrowserAgentState) -> None:
        # Replay persistence is best-effort and not part of the agent loop itself.
        if self._replay is None:
            return
        try:
            await self._replay.save_messages(self._messages)
            await self._replay.save_step_payloads(self._step_payloads)
            await self._replay.save_html(self._messages, step_payloads=self._step_payloads)
        except Exception:
            logger.opt(exception=True).warning("Failed to write replay artifacts")
