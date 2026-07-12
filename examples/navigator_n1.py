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
    BrowserAgentMixin,
    llm_retry,
    run_example_main,
)
from loguru import logger
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, Field

from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_MODEL
from yutori.navigator.loop import update_trimmed_history
from yutori.navigator.replay import sanitize_step_payload  # Optional replay helpers.


class Config(BaseModel):
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
    # payload management
    max_request_bytes: int = 9_500_000
    keep_recent_screenshots: int = 6
    # optional local replay artifacts
    replay_dir: str | None = None
    replay_id: str | None = None


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
        max_request_bytes: int = 9_500_000,
        keep_recent_screenshots: int = 6,
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
        }
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=self._request_messages,
                temperature=self.temperature,
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
