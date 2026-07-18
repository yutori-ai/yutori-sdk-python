#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API with the Navigator n1 model
(OpenAI API compatible).

This script takes a user query, launches a local Playwright browser session,
calls the Navigator API to get actions, executes them, and iterates until the
task is complete.

Replay logging in this example is optional. Here, "replay" means saving the
agent trajectory to local files so you can inspect screenshots, actions, and
raw request/response payloads in `visualization.html` after the run.

Features:
- Payload trimming: keeps the agent's owned message history bounded while still
  ending in a standard chat completions call.

Usage:
    yutori auth login  # or export YUTORI_API_KEY=...
    uv sync --extra examples
    uv run python examples/navigator_n1.py --task "List the team member names" --start-url "https://www.yutori.com"
"""

import asyncio

from _common import (
    BaseAgentConfig,
    BrowserAgentMixin,
    llm_retry,
    run_example_main,
)
from openai.types.chat import ChatCompletion

from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_MODEL
from yutori.navigator.payload import DEFAULT_KEEP_RECENT_SCREENSHOTS, DEFAULT_MAX_REQUEST_BYTES


class Config(BaseAgentConfig):
    # payload management
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    keep_recent_screenshots: int = DEFAULT_KEEP_RECENT_SCREENSHOTS


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
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        keep_recent_screenshots: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
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
        self.max_request_bytes = max_request_bytes
        self.keep_recent_screenshots = keep_recent_screenshots
        self._request_messages: list | None = None

    async def run(self, task: str, start_url: str) -> str:
        self._request_messages = None
        return await self._run_with_browser_lifecycle(task, start_url, replay_prefix="n1")

    @llm_retry
    async def _call_llm_with_retries(self) -> ChatCompletion:
        return await self._call_llm(self._trim_request_messages())

    # _predict() and _execute() are inherited from BrowserAgentMixin (identical across the
    # n1 examples); this script has no custom tools, so it also uses the default
    # _dispatch_custom_tool() (always declines, falling through to the n1 primitive actions).


async def main():
    await run_example_main(
        Config,
        "Example of using the Yutori Navigator API (Navigator n1) to perform a web browsing task",
        api_label="Yutori Navigator n1",
        agent_cls=Agent,
        include_payload_trim=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
