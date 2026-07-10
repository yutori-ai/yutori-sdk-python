#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API (Navigator n1, OpenAI API
compatible) with custom tools.

This script takes a user query, launches a local Playwright browser session,
calls the Navigator API to get actions, executes them, and iterates until the
task is complete.

In addition, we implement a custom tool to extract content and links from the page.

Replay logging in this example is optional. Here, "replay" means saving the
agent trajectory to local files so you can inspect screenshots, actions, and
raw request/response payloads in `visualization.html` after the run.

Usage:
    yutori auth login  # or export YUTORI_API_KEY=...
    uv sync --extra examples
    uv run python examples/navigator_n1_custom_tools.py \
        --task "Get the titles and links of all the blog posts" \
        --start-url "https://www.yutori.com"
"""

import asyncio
import json
import re
from functools import cached_property

from _common import (
    BrowserAgentMixin,
    build_agent_arg_parser,
    configure_example_logging,
    execute_n1_primitive_action,
    llm_retry,
    run_example_agent,
)
from loguru import logger
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel, Field

from yutori import AsyncYutoriClient
from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_MODEL
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.replay import TrajectoryRecorder, make_run_id, sanitize_step_payload  # Optional replay helpers.


class Config(BaseModel):
    # task
    task: str = Field(default="Get the titles and links of all the blog posts")
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


class ExtractContentAndLinksTool:
    def __init__(self):
        super().__init__()

        self._link_pattern = re.compile(r'- link "([^"]*)"')
        self._url_pattern = re.compile(r"- /url: (.+)")
        self._title_cleaner_pattern = re.compile(r"\s+\d+$")

    @cached_property
    def input_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "extract_content_and_links",
                "description": (
                    "Extracts page content and hyperlinks relevant to the user task. "
                    "This operation is strictly read-only and never interacts with or alters the page"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    async def __call__(self, page: Page, **kwargs) -> str:
        url_to_title: dict[str, str] = {}

        snapshot = await page.locator("body").aria_snapshot()
        lines = snapshot.split("\n")

        for i, line in enumerate(lines):
            # Match link pattern: - link "TITLE": or - link "TITLE"
            if link_match := self._link_pattern.search(line):
                title = link_match.group(1)

                # Look for /url in subsequent indented lines
                url = None
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    # Check if we've moved out of this link's children (less or equal indentation)
                    if next_line.strip() and not next_line.startswith(" " * (len(line) - len(line.lstrip()) + 2)):
                        break
                    # Check for /url pattern
                    url_match = self._url_pattern.search(next_line)
                    if url_match:
                        url = url_match.group(1).strip()
                        break
                    j += 1

                if not url:
                    continue

                title = self._title_cleaner_pattern.sub("", title).strip()
                if url in url_to_title:
                    # Deduplicate by URL, keeping the longest title (without trailing numbers)
                    existing = url_to_title[url]
                    if len(title) > len(existing):
                        url_to_title[url] = title
                else:
                    url_to_title[url] = title

        result = f"Current URL: {page.url}"
        if url_to_title:
            result += "\nLinks on the entire page:\n"
            result += "\n".join([f"- [{title}]({url})" for url, title in url_to_title.items()])
        return result


class Agent(BrowserAgentMixin):
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = NAVIGATOR_N1_MODEL,
        temperature: float = 0.3,
        max_steps: int = 100,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        headless: bool = False,
        replay_dir: str | None = None,
        replay_id: str | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_steps = max_steps
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.headless = headless
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
        # Stored only so the replay viewer can show raw request/response JSON per step.
        self._step_payloads: list[dict] = []
        self._step_count = 0

        # Custom tools
        self._extract_content_and_links_tool = ExtractContentAndLinksTool()

    async def run(self, task: str, start_url: str) -> str:
        logger.info(f"Task: {task}")
        logger.info(f"Starting URL: {start_url}")

        self._messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]
        self._message_index = 0
        self._step_count = 0
        self._step_payloads = []
        self._replay = None

        final_response = ""
        # Replay output is opt-in; the loop still works without any of this.
        if self.replay_dir:
            replay_id = self.replay_id or make_run_id(prefix="n1_custom", label=task)
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
                        content = [{"type": "text", "text": result}] if result else []
                        self._messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})
                    await self._persist_replay()

                if self._step_count >= self.max_steps:
                    logger.warning(f"Reached maximum steps ({self.max_steps})")

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                await self._persist_replay()
                await self._close_browser()

        return final_response

    @llm_retry
    async def _call_llm_with_retries(self) -> ChatCompletion:
        # This copy is only for replay output; the request itself just uses the same fields directly.
        request_payload = {
            "model": self.model,
            "messages": self._messages,
            "temperature": self.temperature,
            "tools": [self._extract_content_and_links_tool.input_schema],
        }
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                temperature=self.temperature,
                tools=[self._extract_content_and_links_tool.input_schema],  # add custom tools here
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

    # _predict() is inherited from BrowserAgentMixin (identical across the n1 examples).

    async def _execute(self, tool_call: ChatCompletionMessageToolCall) -> str | None:
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            if action_name == "extract_content_and_links":
                await self._wait_for_page_ready()
                return await self._extract_content_and_links_tool(self._page)

            if not await execute_n1_primitive_action(
                self._page, action_name, arguments, self.viewport_width, self.viewport_height
            ):
                logger.warning(f"Unknown action: {action_name}")
                return f"[ERROR] Unknown action: {action_name}"

            # Wait for any navigation or dynamic content
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            await self._wait_for_page_ready()

        except Exception as e:
            logger.error(f"Error executing {action_name}: {e}")
            return f"[ERROR] Error executing {action_name}: {e}"


async def main():
    configure_example_logging()

    default_config = Config()
    parser = build_agent_arg_parser(
        "Example of using the Yutori Navigator API (Navigator n1) to perform a web browsing task",
        default_config,
        api_label="Yutori Navigator n1",
    )
    args = parser.parse_args()
    config = Config.model_validate(vars(args))

    await run_example_agent(Agent, config)


if __name__ == "__main__":
    asyncio.run(main())
