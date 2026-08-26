#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API (Navigator n1.5) with a custom tool.

Custom tools ride alongside the built-in browser actions through the `tools`
parameter, and the model calls them like any other action. This script adds a
read-only tool that extracts the page's content and links, on top of the
complete Navigator n1.5 agent in `navigator_n1_5.py`.

Replay logging in this example is optional. Here, "replay" means saving the
agent trajectory to local files so you can inspect screenshots, actions, and
raw request/response payloads in `visualization.html` after the run.

Usage:
    yutori auth login  # or export YUTORI_API_KEY=...
    uv sync --extra examples
    uv run python examples/navigator_n1_5_custom_tools.py \
        --task "Get the titles and links of all the blog posts" \
        --start-url "https://www.yutori.com"
"""

import asyncio
import re
from functools import cached_property
from typing import Any

from _common import BaseAgentConfig, run_example_main
from navigator_n1_5 import Agent as NavigatorAgent
from playwright.async_api import Page
from pydantic import Field


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

        # The n1.5 loop already appends the current URL to every non-empty tool result.
        if not url_to_title:
            return "No links found on the page."
        links = "\n".join(f"- [{title}]({url})" for url, title in url_to_title.items())
        return f"Links on the entire page:\n{links}"


class Agent(NavigatorAgent):
    replay_prefix = "n1_5_custom"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Custom tools
        self._extract_content_and_links_tool = ExtractContentAndLinksTool()
        self.custom_tools = [self._extract_content_and_links_tool.input_schema]

    async def _dispatch_custom_tool(self, action_name: str, arguments: dict[str, Any]) -> str | None:
        if action_name == "extract_content_and_links":
            await self._wait_for_page_ready()
            return await self._extract_content_and_links_tool(self._page)
        return None


async def main():
    await run_example_main(
        Config,
        "Example of using the Yutori Navigator API (Navigator n1.5) with a custom tool",
        api_label="Yutori Navigator n1.5",
        agent_cls=Agent,
    )


if __name__ == "__main__":
    asyncio.run(main())
