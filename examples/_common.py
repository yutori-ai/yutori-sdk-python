"""Shared helpers for runnable example scripts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any, Protocol

from loguru import logger
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from openai.types.chat import ChatCompletion
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from yutori import AsyncYutoriClient
from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_MODEL, aplaywright_screenshot_to_data_url, denormalize_coordinates
from yutori.navigator.loop import update_trimmed_history
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.replay import TrajectoryRecorder, make_run_id, sanitize_step_payload
from yutori.navigator.replay import _clip_image_url as _clip_image_url_impl

RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class BaseAgentConfig(BaseModel):
    """Shared task/model/agent/browser/replay fields for the n1 example scripts' ``Config``.

    ``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py`` previously each defined a
    byte-for-byte identical ``Config`` with exactly these nine fields (differing only in their
    ``task``/``start_url`` defaults, which subclasses override). ``navigator_n1.py`` subclasses
    this and adds two payload-trim fields. ``navigator_n1_5.py`` interleaves several more
    model-specific fields among a differently-ordered version of this same set, so it keeps its
    own standalone ``Config`` instead of subclassing.
    """

    # task
    task: str = Field(default="List the team member names")
    start_url: str = "https://www.yutori.com"
    # model
    base_url: str = DEFAULT_BASE_URL
    model: str = NAVIGATOR_N1_MODEL
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


async def _navigate_and_settle(page: Any, nav_coro: Any, *, sleep: float) -> None:
    """Await a Playwright navigation call, wait for load, then settle for ``sleep`` seconds.

    Shared by the ``goto``/``back``/``refresh`` branches of :func:`execute_n1_primitive_action`,
    which each await a different navigation call, wait for ``"domcontentloaded"``, then sleep
    for their own fixed duration -- otherwise identical.
    """
    await nav_coro
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(sleep)


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
    if action_name in ("left_click", "double_click", "right_click", "triple_click"):
        # All four share the same coordinate resolution and post-click pause;
        # only the actual mouse call differs.
        abs_x, abs_y = _click_coordinates(arguments, viewport_width, viewport_height)
        if action_name == "double_click":
            await page.mouse.dblclick(abs_x, abs_y)
        elif action_name == "right_click":
            await page.mouse.click(abs_x, abs_y, button="right")
        elif action_name == "triple_click":
            await page.mouse.click(abs_x, abs_y, click_count=3)
        else:
            await page.mouse.click(abs_x, abs_y)
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
        await _navigate_and_settle(page, page.goto(url), sleep=1)

    elif action_name in ("back", "go_back"):
        await _navigate_and_settle(page, page.go_back(), sleep=0.5)

    elif action_name == "refresh":
        await _navigate_and_settle(page, page.reload(), sleep=1)

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


async def run_example_main(
    config_cls: Callable[[], Any],
    description: str,
    *,
    api_label: str,
    agent_cls: Callable[..., SupportsAgentRun],
    include_payload_trim: bool = False,
) -> None:
    """Configure logging, parse CLI args, and run ``agent_cls`` -- the shared ``main()`` body.

    ``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py`` each
    defined this same configure/build-parser/parse/validate/run sequence in their own
    ``main()`` (``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py`` were
    byte-for-byte identical; ``navigator_n1.py`` differs only by passing
    ``include_payload_trim=True``). ``navigator_n1_5.py`` builds its parser directly with
    extra tool-set/json-schema/timezone/location arguments, so it keeps its own ``main()``.
    """
    configure_example_logging()

    default_config = config_cls()
    parser = build_agent_arg_parser(
        description,
        default_config,
        api_label=api_label,
        include_payload_trim=include_payload_trim,
    )
    args = parser.parse_args()
    config = config_cls.model_validate(vars(args))

    await run_example_agent(agent_cls, config)


class SupportsBrowserAgentState(Protocol):
    """Instance attributes :class:`BrowserAgentMixin` methods expect from ``self``.

    Each example ``Agent.__init__`` sets these before the browser lifecycle
    or replay-persistence methods below are called.
    """

    base_url: str
    headless: bool
    viewport_width: int
    viewport_height: int
    replay_dir: str | None
    replay_id: str | None
    max_steps: int
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

    async def _call_llm_with_retries(self) -> ChatCompletion: ...

    async def _dispatch_custom_tool(
        self, action_name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str | None] | None: ...


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

    ``_init_agent_state`` and ``_start_run`` cover the ``__init__``/``run()``
    bookkeeping that was also duplicated verbatim across all four scripts
    (browser handles, the ``PageReadyChecker``, and per-run message/replay
    setup); every ``Agent.__init__``/``run()`` still has its own model- and
    tool-specific setup around these calls. ``_init_common_agent_config``
    additionally covers the shared constructor-kwarg assignment (plus the
    ``_init_agent_state`` call) that ``navigator_n1.py``, ``navigator_n1_custom_tools.py``,
    and ``navigator_n1_memo.py`` all build from the same nine kwargs;
    ``navigator_n1_5.py`` interleaves several more model-specific kwargs among
    those same nine and keeps its own inline assignment. This mixin's own
    ``__init__`` covers that same nine-kwarg signature directly, so
    ``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py`` -- which had
    no other constructor-kwarg divergence -- now call ``super().__init__(**kwargs)``
    instead of redefining the full signature themselves.

    ``_run_with_browser_lifecycle`` covers the entire ``run()`` body -- opening
    the client/browser, navigating, running :meth:`_run_agent_loop`, and
    cleanup -- shared verbatim by ``navigator_n1.py``, ``navigator_n1_custom_tools.py``,
    and ``navigator_n1_memo.py``; ``navigator_n1_5.py`` keeps its own ``run()``.

    ``_execute`` covers the argument-parsing / n1-primitive-fallback / page-ready-wait /
    catch-all-error structure that was also duplicated verbatim across those same three
    scripts' ``_execute()`` methods; each only differed in which custom tools (if any) it
    checked first. Scripts with custom tools now override :meth:`_dispatch_custom_tool`
    instead of ``_execute`` itself; ``navigator_n1.py`` has no custom tools and uses the
    default. ``navigator_n1_5.py`` keeps its own differently-shaped ``_execute``.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = NAVIGATOR_N1_MODEL,
        temperature: float = 0.3,
        max_steps: int = 100,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        headless: bool = False,
        replay_dir: str | None = None,
        replay_id: str | None = None,
    ) -> None:
        """Shared constructor for example ``Agent`` subclasses that add no kwargs of their own.

        ``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py`` had byte-for-byte
        identical ``__init__`` bodies built from exactly this nine-kwarg signature -- only
        their trailing custom-tool setup line differed. Both now call ``super().__init__(**kwargs)``
        and add just that one line. ``navigator_n1.py`` interleaves two more payload-trim kwargs
        into this same signature and keeps its own ``__init__``; ``navigator_n1_5.py`` interleaves
        several more model-specific kwargs and also keeps its own.
        """
        self._init_common_agent_config(
            base_url=base_url,
            model=model,
            temperature=temperature,
            max_steps=max_steps,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            headless=headless,
            replay_dir=replay_dir,
            replay_id=replay_id,
        )

    def _init_common_agent_config(
        self: SupportsBrowserAgentState,
        *,
        base_url: str,
        model: str,
        temperature: float,
        max_steps: int,
        viewport_width: int,
        viewport_height: int,
        headless: bool,
        replay_dir: str | None,
        replay_id: str | None,
    ) -> None:
        """Assign the constructor kwargs shared by every example ``Agent.__init__`` and init agent state.

        Called both by :meth:`__init__` above (for subclasses with no extra kwargs) and directly
        by ``navigator_n1.py``'s ``Agent.__init__`` (which sets the same nine kwargs plus its own
        payload-trim fields set separately around this call). ``navigator_n1_5.py`` interleaves
        several more model-specific kwargs among these same nine and keeps its own inline assignment.
        """
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_steps = max_steps
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.headless = headless
        self.replay_dir = replay_dir
        self.replay_id = replay_id

        self._init_agent_state()

    def _init_agent_state(self: SupportsBrowserAgentState) -> None:
        """Initialize the browser/replay/message bookkeeping shared by every example ``Agent.__init__``.

        Each script's ``__init__`` set this same block of instance state --
        browser/page handles, the ``PageReadyChecker``, and replay/message
        bookkeeping -- verbatim after assigning its own constructor kwargs.
        ``navigator_n1.py`` and ``navigator_n1_5.py`` additionally set
        ``self._request_messages = None`` for payload trimming right after
        calling this; that attribute isn't part of the shared state since
        ``navigator_n1_custom_tools.py``/``navigator_n1_memo.py`` never read it.
        """
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
        """Reset per-run message/replay state and log the run header -- the shared prologue of every example ``run()``.

        Resets message/step bookkeeping for a fresh run and starts a replay
        recorder when ``self.replay_dir`` is set. ``replay_prefix`` is the
        ``make_run_id`` prefix each script previously hardcoded inline
        (``"n1"``, ``"n1_custom"``, ``"navigator_memo"``, ``"navigator_1_5"``).
        ``navigator_n1_5.py`` calls this after reformatting ``task`` with
        timezone/location context, so it logs and labels the replay with the
        reformatted task, matching its existing behavior. Callers that reset
        additional per-run state (``navigator_n1.py`` and ``navigator_n1_5.py``
        also clear ``self._request_messages``) do so themselves.
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

    async def _run_agent_loop(self: SupportsBrowserAgentState) -> str:
        """Run the predict -> execute step loop shared by every example ``Agent.run()``.

        ``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``
        each carried a byte-for-byte identical version of this loop; only ``navigator_n1_5.py``
        diverges (it stop-and-summarizes on interruption/exception/step-limit and appends a URL
        suffix to each tool result), so it keeps its own ``run()`` loop instead of using this.

        Every caller's ``_execute(tool_call)`` must return ``(should_exit, result)``:
        ``should_exit=True`` ends the run immediately with ``result`` as the final response
        (used by ``navigator_n1_memo.py``'s ``list_records`` tool); the other two scripts'
        ``_execute`` always return ``should_exit=False``.
        """
        final_response = ""

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
                should_exit, result = await self._execute(tool_call)
                if should_exit:
                    await self._persist_replay()
                    return result
                content = [{"type": "text", "text": result}] if result else []
                self._messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})
            await self._persist_replay()

        if self._step_count >= self.max_steps:
            logger.warning(f"Reached maximum steps ({self.max_steps})")

        return final_response

    async def _run_with_browser_lifecycle(
        self: SupportsBrowserAgentState,
        task: str,
        start_url: str,
        *,
        replay_prefix: str,
    ) -> str:
        """Run a full task: start the run, open the client/browser, and loop until done.

        ``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``
        each carried a byte-for-byte identical ``run()`` body (open ``AsyncYutoriClient`` +
        Playwright, launch the browser, navigate to ``start_url``, run
        :meth:`_run_agent_loop`, and clean up in ``finally``), differing only in the
        ``replay_prefix`` passed to :meth:`_start_run`. ``navigator_n1.py`` additionally
        resets ``self._request_messages`` for payload trimming -- callers that need that
        do so themselves before calling this. ``navigator_n1_5.py`` diverges right after
        page-ready (stop-and-summarize handling, an extra ``except Exception`` branch,
        URL-suffixed tool results) and keeps its own ``run()``.
        """
        self._start_run(task, start_url, replay_prefix=replay_prefix)

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

                final_response = await self._run_agent_loop()

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                await self._persist_replay()
                await self._close_browser()

        return final_response

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

    async def _call_llm(
        self: SupportsBrowserAgentState,
        messages: list,
        *,
        extra_fields: dict[str, Any] | None = None,
    ) -> ChatCompletion:
        """Call the model with ``messages`` and record a sanitized request/response pair for replay.

        Shared by :meth:`_call_llm_with_tools` (fixed ``tools`` list, untrimmed
        ``self._messages``) and the trimmed-history ``_call_llm_with_retries`` overrides in
        ``navigator_n1.py`` and ``navigator_n1_5.py`` (which pass ``self._request_messages``
        plus their own model-specific ``extra_fields``, e.g. ``tool_set``/``disable_tools``/
        ``json_schema``). All three previously built this same request-payload-dict /
        timeout-guarded-create / sanitized-step-payload body independently; ``extra_fields``
        is merged into the payload dict, which then doubles as the ``create()`` kwargs, so the
        logged request and the actual call can no longer drift apart.
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

        Shared by ``navigator_n1.py``'s and ``navigator_n1_5.py``'s ``_call_llm_with_retries``,
        which each independently built this same "call :func:`update_trimmed_history`, then log
        the removed-screenshot count" preamble before calling :meth:`_call_llm` with their own
        (absent vs. ``tool_set``/``disable_tools``/``json_schema``) ``extra_fields``. Callers are
        responsible for their own ``self.max_request_bytes``/``self.keep_recent_screenshots``/
        ``self._request_messages`` attributes -- not every ``BrowserAgentMixin`` subclass uses
        payload trimming, so those aren't part of :class:`SupportsBrowserAgentState`.
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

    @llm_retry
    async def _call_llm_with_tools(self: SupportsBrowserAgentState, tools: list[dict]) -> ChatCompletion:
        """Call the model with a fixed ``tools`` list -- the shared body of ``_call_llm_with_retries``.

        ``navigator_n1_custom_tools.py`` and ``navigator_n1_memo.py`` each defined a
        ``_call_llm_with_retries`` that was byte-for-byte identical to this one, differing
        only in the (fixed, non-trimmed) ``tools`` list passed to the chat completions call.
        Each now delegates here with its own tool-schema list. ``navigator_n1.py`` and
        ``navigator_n1_5.py`` additionally trim message history and pass model-specific
        fields (``tool_set``/``disable_tools``/``json_schema``), so they keep their own
        ``_call_llm_with_retries`` instead of using this helper.
        """
        return await self._call_llm(self._messages, extra_fields={"tools": tools})

    async def _dispatch_custom_tool(
        self, action_name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str | None] | None:
        """Handle an example-specific custom tool call, or decline it.

        Overridden by ``navigator_n1_custom_tools.py`` (``extract_content_and_links``)
        and ``navigator_n1_memo.py`` (``add_question``/``add_options``/``list_records``)
        to handle their own tools before :meth:`_execute` falls back to
        :func:`execute_n1_primitive_action`. The default (used by ``navigator_n1.py``,
        which defines no custom tools) always declines by returning ``None``.

        Return ``None`` if ``action_name`` isn't recognized, so ``_execute`` falls
        through to the n1 primitive-action dispatch. Return ``(should_exit, result)``
        directly to handle it and skip the primitive-action path entirely.
        """
        return None

    async def _execute(self: SupportsBrowserAgentState, tool_call: Any) -> tuple[bool, str | None]:
        """Parse a tool call's arguments and execute it -- the shared body of every example ``_execute()``.

        ``navigator_n1.py``, ``navigator_n1_custom_tools.py``, and ``navigator_n1_memo.py``
        each defined this same argument-parsing / custom-tool-dispatch / n1-primitive-fallback
        / page-ready-wait / catch-all-error structure verbatim, differing only in which custom
        tools (if any) :meth:`_dispatch_custom_tool` recognizes. ``navigator_n1_5.py`` targets a
        different model and action vocabulary and keeps its own differently-shaped ``_execute``.
        """
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return False, f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            custom_result = await self._dispatch_custom_tool(action_name, arguments)
            if custom_result is not None:
                return custom_result

            if not await execute_n1_primitive_action(
                self._page, action_name, arguments, self.viewport_width, self.viewport_height
            ):
                logger.warning(f"Unknown action: {action_name}")
                return False, f"[ERROR] Unknown action: {action_name}"

            # Wait for any navigation or dynamic content
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            await self._wait_for_page_ready()

        except Exception as e:
            logger.error(f"Error executing {action_name}: {e}")
            return False, f"[ERROR] Error executing {action_name}: {e}"

        return False, None

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
