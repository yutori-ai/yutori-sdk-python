#!/usr/bin/env python
"""
A web browsing agent using Yutori's Navigator API (Navigator n1.5) with custom tools
that let the model memorize information (into a file) as it navigates.

We ask the model to take a quiz and record every question, description, and all the
options along the way. Built on the complete Navigator n1.5 agent in `navigator_n1_5.py`.

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
    uv run python examples/navigator_n1_5_memo.py \
        --task "Take the quiz and record every question, description, and all the options along the way" \
        --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from functools import cached_property
from typing import Any

from _common import BaseAgentConfig, run_example_main
from loguru import logger
from navigator_n1_5 import Agent as NavigatorAgent
from pydantic import Field


class Config(BaseAgentConfig):
    task: str = Field(
        default="Take the quiz and record every question, description, and all the options along the way",
    )
    start_url: str = "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"


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

    @staticmethod
    def _find_record(records: list[dict], index: int) -> dict | None:
        for record in records:
            if record["index"] == index:
                return record
        return None

    async def add_question(self, index: int, question: str, description: str | None = None) -> str:
        records = await self.read_jsonl(self.file_path)
        record = self._find_record(records, index)
        if record is not None:
            record["question"] = question
            record["description"] = description
            logger.warning(f"Updated question {index} with new question: {question} and description: {description}")
        else:
            records.append({"index": index, "question": question, "description": description})
        await self.write_jsonl(self.file_path, records)
        return f"Successfully added question {index}"

    async def add_options(self, question_index: int, options: list[str]) -> str:
        records = await self.read_jsonl(self.file_path)
        record = self._find_record(records, question_index)
        if record is None:
            raise ValueError(f"Question index {question_index} not found")
        record.setdefault("options", []).extend(options)
        await self.write_jsonl(self.file_path, records)
        return f"Successfully added options to question {question_index}"

    async def list_records(self) -> str:
        with open(self.file_path, "r") as f:
            content = f.read()
        return f"Memo file path: {self.file_path}\nRecords:\n{content}"


class Agent(NavigatorAgent):
    replay_prefix = "n1_5_memo"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Custom memo tool suite
        self._memo_tool_suite = MemoToolSuite()
        self.custom_tools = self._memo_tool_suite.input_schemas

    async def _dispatch_custom_tool(self, action_name: str, arguments: dict[str, Any]) -> str | None:
        if action_name == "add_question":
            return await self._memo_tool_suite.add_question(**arguments)

        if action_name == "add_options":
            return await self._memo_tool_suite.add_options(**arguments)

        if action_name == "list_records":
            # The model reads the records back and then answers with its summary.
            return await self._memo_tool_suite.list_records()

        return None


async def main():
    await run_example_main(
        Config,
        "Example of using the Yutori Navigator API (Navigator n1.5) with custom memo tools",
        api_label="Yutori Navigator n1.5",
        agent_cls=Agent,
    )


if __name__ == "__main__":
    asyncio.run(main())
