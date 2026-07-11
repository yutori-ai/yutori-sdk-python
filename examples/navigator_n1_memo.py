#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API (Navigator n1, OpenAI API
compatible) with custom tools.

This script demonstrates how to use custom tools to let the model memorize information (into files) as it navigates.

We ask the model to take a quiz and record every question, description, and all the options along the way.

Replay logging in this example is optional. Here, "replay" means saving the
agent trajectory to local files so you can inspect screenshots, actions, and
raw request/response payloads in `visualization.html` after the run.

We implement three custom tools:
- `add_question`: to add a new question and description
- `add_options`: to add new options to an existing question
- `list_records`: to list all the questions and options in JSONL format

Usage:
    yutori auth login  # or export YUTORI_API_KEY=...
    uv sync --extra examples
    uv run python examples/navigator_n1_memo.py \
        --task "Take the quiz and record every question, description, and all the options along the way" \
        --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from functools import cached_property

from _common import (
    BrowserAgentMixin,
    execute_n1_primitive_action,
    llm_retry,
    run_example_main,
)
from loguru import logger
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from pydantic import BaseModel, Field

from yutori.config import DEFAULT_BASE_URL
from yutori.navigator import NAVIGATOR_N1_MODEL
from yutori.navigator.replay import sanitize_step_payload  # Optional replay helpers.


class Config(BaseModel):
    # task
    task: str = Field(
        default="Take the quiz and record every question, description, and all the options along the way",
    )
    start_url: str = "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
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


class MemoToolSuite:
    def __init__(self, file_path: str | None = None):
        super().__init__()
        if not file_path:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            file_path = os.path.join(os.path.dirname(__file__), f"memo_{timestamp}.jsonl")
        self.file_path = file_path

        logger.info(f"Memo file path: {self.file_path}")

    @cached_property
    def input_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "add_question",
                    "description": (
                        "Add a new question and description to the memo. Call this whenever you see a new question."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer", "description": "The index of the question."},
                            "question": {"type": "string", "description": "The question text exactly as shown."},
                            "description": {"type": "string", "description": "The description of the question."},
                        },
                        "required": ["index", "question"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_options",
                    "description": "Add new options to an existing question. Call this whenever you see new options.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question_index": {"type": "integer", "description": "The index of the question."},
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "The options to add.",
                            },
                        },
                        "required": ["question_index", "options"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_records",
                    "description": (
                        "List all the recorded questions and options. Call this after completing the quiz."
                    ),
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
        ]

    @staticmethod
    async def read_jsonl(file_path: str) -> list[dict]:
        try:
            with open(file_path, "r") as f:
                content = f.read()
            return [json.loads(line) for line in content.splitlines()]
        except FileNotFoundError:
            return []

    @staticmethod
    async def write_jsonl(file_path: str, records: list[dict]) -> None:
        lines = [json.dumps(record) for record in records]
        with open(file_path, "w") as f:
            f.write("\n".join(lines))

    async def add_question(self, index: int, question: str, description: str | None = None) -> str:
        records = await self.read_jsonl(self.file_path)
        for record in records:
            if record["index"] == index:
                record["question"] = question
                record["description"] = description
                logger.warning(f"Updated question {index} with new question: {question} and description: {description}")
                break
        else:
            records.append({"index": index, "question": question, "description": description})
        await self.write_jsonl(self.file_path, records)
        return f"Successfully added question {index}"

    async def add_options(self, question_index: int, options: list[str]) -> str:
        records = await self.read_jsonl(self.file_path)
        for record in records:
            if record["index"] == question_index:
                record.setdefault("options", []).extend(options)
                break
        else:
            raise ValueError(f"Question index {question_index} not found")
        await self.write_jsonl(self.file_path, records)
        return f"Successfully added options to question {question_index}"

    async def list_records(self) -> str:
        with open(self.file_path, "r") as f:
            content = f.read()
        return f"Memo file path: {self.file_path}\nRecords:\n{content}"


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

        self._init_agent_state()

        # Custom memo tool suite
        self._memo_tool_suite = MemoToolSuite()

    async def run(self, task: str, start_url: str) -> str:
        return await self._run_with_browser_lifecycle(task, start_url, replay_prefix="navigator_memo")

    @llm_retry
    async def _call_llm_with_retries(self) -> ChatCompletion:
        # This copy is only for replay output; the request itself just uses the same fields directly.
        request_payload = {
            "model": self.model,
            "messages": self._messages,
            "temperature": self.temperature,
            "tools": self._memo_tool_suite.input_schemas,
        }
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                temperature=self.temperature,
                tools=self._memo_tool_suite.input_schemas,  # add custom tools here
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

    async def _execute(self, tool_call: ChatCompletionMessageToolCall) -> tuple[bool, str | None]:
        # Returns (should_exit, result)
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return False, f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            if action_name == "add_question":
                result = await self._memo_tool_suite.add_question(**arguments)
                return False, result

            elif action_name == "add_options":
                result = await self._memo_tool_suite.add_options(**arguments)
                return False, result

            elif action_name == "list_records":
                result = await self._memo_tool_suite.list_records()
                logger.info("Task completed (`list_records` tool called)")
                return True, result

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


async def main():
    await run_example_main(
        Config,
        "Example of using the Yutori Navigator API (Navigator n1) to perform a web browsing task",
        api_label="Yutori Navigator n1",
        agent_cls=Agent,
    )


if __name__ == "__main__":
    asyncio.run(main())
