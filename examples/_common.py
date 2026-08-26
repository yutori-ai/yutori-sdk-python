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
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_5_MODEL, aplaywright_screenshot_to_data_url
from yutori.navigator.loop import update_trimmed_history
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.replay import TrajectoryRecorder, make_run_id, sanitize_step_payload
from yutori.navigator.replay import _clip_image_url as _clip_image_url_impl

RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class BaseAgentConfig(BaseModel):
    """Task/model/agent/browser/replay fields shared by the example scripts' ``Config``.

    ``navigator_n1_5_custom_tools.py`` and ``navigator_n1_5_memo.py`` subclass this and
    override only the ``task``/``start_url`` defaults. ``navigator_n1_5.py`` adds
    model-specific fields (tool set, structured output, user context, payload trimming)
    and keeps its own standalone ``Config``.
    """

    # task
    task: str = Field(default="List the team member names")
    start_url: str = "https://www.yutori.com"
    # model
    base_url: str = DEFAULT_BASE_URL
    model: str = NAVIGATOR_N1_5_MODEL
    temperature: float = 0.3
    # agent
    max_steps: int = 100
    # browser
    viewport_width: int = 1280
    viewport_height: int = 800
    headless: bool = False
    # optional local replay artifacts
    replay_dir: str | None = None
    replay_id: str | None = None


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
) -> argparse.ArgumentParser:
    """Build the task/model/agent/browser/replay parser shared by the example scripts' ``main()``.

    The custom-tool scripts use it as is through :func:`run_example_main`. ``navigator_n1_5.py``
    builds its parser directly instead, since it also takes tool-set/json-schema/timezone/
    location and payload-trim arguments.
    """
    parser = argparse.ArgumentParser(description=description)
    add_task_arguments(parser, default_config)
    add_model_arguments(parser, default_config, api_label=api_label)
    add_agent_arguments(parser, default_config)
    add_browser_arguments(parser, default_config)
    add_replay_arguments(parser, default_config)
    return parser


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


async def run_example_main(
    config_cls: Callable[[], Any],
    description: str,
    *,
    api_label: str,
    agent_cls: Callable[..., SupportsAgentRun],
) -> None:
    """Configure logging, parse CLI args, and run ``agent_cls`` -- the shared ``main()`` body.

    Used by the custom-tool scripts, whose CLI is exactly the base argument set.
    ``navigator_n1_5.py`` takes extra tool-set/json-schema/timezone/location and payload-trim
    arguments, so it keeps its own ``main()``.
    """
    configure_example_logging()

    default_config = config_cls()
    parser = build_agent_arg_parser(description, default_config, api_label=api_label)
    args = parser.parse_args()
    config = config_cls.model_validate(vars(args))

    await run_example_agent(agent_cls, config)


class SupportsBrowserAgentState(Protocol):
    """Instance attributes :class:`BrowserAgentMixin` methods expect from ``self``.

    Each example ``Agent.__init__`` sets these before the browser lifecycle
    or replay-persistence methods below are called.
    """

    headless: bool
    viewport_width: int
    viewport_height: int
    replay_dir: str | None
    replay_id: str | None
    model: str
    temperature: float
    _client: Any
    _browser: Any
    _page: Any
    _page_ready_checker: PageReadyChecker
    _replay: TrajectoryRecorder | None
    _messages: list
    _message_index: int
    _step_count: int
    _step_payloads: list[dict]


class BrowserAgentMixin:
    """Playwright lifecycle, screenshot, model-call, and replay-persistence helpers for the example agents.

    ``navigator_n1_5.py``'s ``Agent`` owns the run loop and the Navigator n1.5 action
    execution; this mixin holds the boilerplate around it -- launching/closing the
    browser, taking a screenshot, waiting for page readiness, calling the model and
    recording the request/response pair, trimming request history, clipping image
    URLs for log output, and persisting optional replay artifacts.

    Scripts that add custom tools subclass ``navigator_n1_5.Agent`` and override
    :meth:`_dispatch_custom_tool`, which the n1.5 ``_execute`` consults before its
    built-in browser actions.
    """

    def _init_agent_state(self: SupportsBrowserAgentState) -> None:
        """Initialize the browser/replay/message bookkeeping every example ``Agent.__init__`` needs."""
        self._client = None
        self._browser = None
        self._page = None
        self._page_ready_checker = PageReadyChecker(
            timeout=30,
            initial_wait=2.0,
            wait_after_ready=1.0,
            replace_native_select_dropdown=True,
            disable_new_tabs=True,
            disable_printing=True,
        )
        # Replay bookkeeping is optional and only used when writing local artifacts.
        self._replay = None
        self._messages = []
        # Stored only so the replay viewer can show raw request/response JSON per step.
        self._step_payloads = []
        self._step_count = 0

    def _start_run(
        self: SupportsBrowserAgentState,
        task: str,
        start_url: str,
        *,
        replay_prefix: str,
    ) -> None:
        """Reset per-run message/replay state and log the run header -- the prologue of every ``run()``.

        Starts a replay recorder when ``self.replay_dir`` is set; ``replay_prefix`` labels the
        replay run id (``"navigator_1_5"``, ``"n1_5_custom"``, ``"n1_5_memo"``). Callers that
        reset additional per-run state (``navigator_n1_5.py`` also clears
        ``self._request_messages``) do so themselves.
        """
        logger.info(f"Task: {task}")
        logger.info(f"Starting URL: {start_url}")

        self._messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]
        self._message_index = 0
        self._step_count = 0
        self._step_payloads = []
        self._replay = None

        # Replay output is opt-in; the loop still works without any of this.
        if self.replay_dir:
            replay_id = self.replay_id or make_run_id(prefix=replay_prefix, label=task)
            self._replay = TrajectoryRecorder(self.replay_dir, replay_id)
            logger.info(f"Replay artifacts: {self._replay.item_dir}")

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

    async def _call_llm(
        self: SupportsBrowserAgentState,
        messages: list,
        *,
        extra_fields: dict[str, Any] | None = None,
    ) -> ChatCompletion:
        """Call the model with ``messages`` and record a sanitized request/response pair for replay.

        ``extra_fields`` (``tool_set``/``disable_tools``/``json_schema``, and ``tools`` for
        custom tools) is merged into the payload dict, which then doubles as the ``create()``
        kwargs, so the logged request and the actual call cannot drift apart.
        """
        request_payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            **(extra_fields or {}),
        }
        response = await asyncio.wait_for(
            self._client.chat.completions.create(**request_payload),
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

    def _trim_request_messages(self) -> list:
        """Trim ``self._request_messages`` against ``self._messages`` and log any removed screenshots.

        Callers own the ``self.max_request_bytes``/``self.keep_recent_screenshots``/
        ``self._request_messages`` attributes -- payload trimming is opt-in, so those aren't
        part of :class:`SupportsBrowserAgentState`.
        """
        self._request_messages, size_bytes, removed = update_trimmed_history(
            self._messages,
            self._request_messages,
            max_bytes=self.max_request_bytes,
            keep_recent=self.keep_recent_screenshots,
        )
        if removed:
            logger.info(f"Trimmed {removed} old screenshot(s); payload ~{size_bytes / (1024 * 1024):.2f} MB")
        return self._request_messages

    async def _dispatch_custom_tool(self, action_name: str, arguments: dict[str, Any]) -> str | None:
        """Handle an example-specific custom tool call, or decline it.

        Overridden by ``navigator_n1_5_custom_tools.py`` (``extract_content_and_links``) and
        ``navigator_n1_5_memo.py`` (``add_question``/``add_options``/``list_records``). Return
        the tool result text to handle the call, or ``None`` so ``_execute`` falls through to
        the built-in Navigator n1.5 browser actions. The default always declines.
        """
        return None

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
