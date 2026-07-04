"""Shared helpers for runnable example scripts."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Protocol

from loguru import logger
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from yutori.navigator import aplaywright_screenshot_to_data_url
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.replay import TrajectoryRecorder

RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

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
    _step_payloads: list[dict]


class BrowserAgentMixin:
    """Playwright lifecycle, screenshot, and replay-persistence helpers shared by the example agents.

    Every ``examples/navigator_*.py`` script defines its own ``Agent`` class
    because the action-execution logic differs meaningfully per Navigator
    version (that divergence is the point of having separate examples).
    These methods, however, are identical boilerplate across all of
    them -- launching/closing the browser, taking a screenshot, waiting for
    page readiness, clipping image URLs for log output, formatting messages
    for log output, and persisting optional replay artifacts -- so they live
    here once instead of being copy-pasted into every script.

    ``navigator_n1_5.py`` defines its own ``_format_message_for_log`` (a
    differently-styled but behaviorally-equivalent implementation) and so
    overrides this mixin's version via normal MRO -- it is intentionally not
    part of this mechanical extraction.
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
