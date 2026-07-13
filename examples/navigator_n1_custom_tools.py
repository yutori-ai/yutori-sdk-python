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
import re
from functools import cached_property
from typing import Any

from _common import (
    BaseAgentConfig,
    BrowserAgentMixin,
    run_example_main,
)
from openai.types.chat import ChatCompletion
from playwright.async_api import Page
from pydantic import Field

from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_MODEL


class Config(BaseAgentConfig):
    task: str = Field(default="Get the titles and links of all the blog posts")
    start_url: str = "https://www.yutori.com"


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

        # Custom tools
        self._extract_content_and_links_tool = ExtractContentAndLinksTool()

    async def run(self, task: str, start_url: str) -> str:
        return await self._run_with_browser_lifecycle(task, start_url, replay_prefix="n1_custom")

    async def _call_llm_with_retries(self) -> ChatCompletion:
        return await self._call_llm_with_tools([self._extract_content_and_links_tool.input_schema])

    # _predict() and _execute() are inherited from BrowserAgentMixin (identical across the
    # n1 examples).

    async def _dispatch_custom_tool(
        self, action_name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str | None] | None:
        if action_name == "extract_content_and_links":
            await self._wait_for_page_ready()
            return False, await self._extract_content_and_links_tool(self._page)
        return None


async def main():
    await run_example_main(
        Config,
        "Example of using the Yutori Navigator API (Navigator n1) to perform a web browsing task",
        api_label="Yutori Navigator n1",
        agent_cls=Agent,
    )


if __name__ == "__main__":
    asyncio.run(main())
