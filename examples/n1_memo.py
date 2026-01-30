#!/usr/bin/env python
"""
A web browsing agent using Yutori's n1 API (OpenAI API compatible) with custom tools

This script demonstrates how to use custom tools to let the model memorize information (into files) as it navigates.

We ask the model to take a quiz and record every question, description, and all the options along the way.

We implement three custom tools:
- `add_question`: to add a new question and description
- `add_options`: to add new options to an existing question
- `list_records`: to list all the questions and options in JSONL format

Usage:
    export YUTORI_API_KEY=...
    python examples/n1_memo.py \
        --task "Take the quiz and record every question, description, and all the options along the way" \
        --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
"""

import argparse
import asyncio
import base64
import io
import json
import os
import sys
from datetime import datetime
from functools import cached_property

from loguru import logger
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from PIL import Image
from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from yutori import AsyncYutoriClient

RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class Config(BaseModel):
    # task
    task: str = Field(
        default="Take the quiz and record every question, description, and all the options along the way",
    )
    start_url: str = "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
    # model
    api_key: str = Field(default_factory=lambda: os.getenv("YUTORI_API_KEY"))
    base_url: str = "https://api.yutori.com/v1"
    model: str = "n1-latest"
    temperature: float = 0.3
    # agent
    max_steps: int = 100
    # browser
    viewport_width: int = 1280
    viewport_height: int = 800
    headless: bool = False


class MemoToolSuite:
    def __init__(self, file_path: str | None = None):
        super().__init__()
        if not file_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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


class Agent:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.yutori.com/v1",
        model: str = "n1-latest",
        temperature: float = 0.3,
        max_steps: int = 100,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        headless: bool = False,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_steps = max_steps
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.headless = headless

        self._client: AsyncYutoriClient | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._messages: list = []
        self._step_count = 0

        # Custom memo tool suite
        self._memo_tool_suite = MemoToolSuite()

    async def run(self, task: str, start_url: str) -> str:
        logger.info(f"Task: {task}")
        logger.info(f"Starting URL: {start_url}")

        self._messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]
        self._message_index = 0
        self._step_count = 0

        final_response = ""

        async with (
            AsyncYutoriClient(api_key=self.api_key, base_url=self.base_url) as client,
            async_playwright() as playwright,
        ):
            try:
                self._client = client
                await self._init_browser(playwright)
                await self._page.goto(start_url)
                await self._page.wait_for_load_state("domcontentloaded")

                while self._step_count < self.max_steps:
                    self._step_count += 1
                    logger.debug(f"Step {self._step_count}, URL: {self._page.url}")

                    response = await self._predict()

                    # Log raw model prediction
                    logger.info(f"Response: {response}")

                    # Store the assistant's response
                    self._messages.append(response.model_dump(exclude_none=True))
                    self._message_index = len(self._messages)

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
                            logger.info("Task completed (`list_records` tool called)")
                            return result
                        content = [{"type": "text", "text": result}] if result else []
                        self._messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})

                if self._step_count >= self.max_steps:
                    logger.warning(f"Reached maximum steps ({self.max_steps})")

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                await self._close_browser()

        return final_response

    async def _init_browser(self, playwright) -> None:
        self._browser = await playwright.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height}
        )
        self._page = await context.new_page()
        await asyncio.sleep(1)

    async def _close_browser(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None

    async def _take_screenshot(self) -> str:
        screenshot_bytes = await self._page.screenshot(type="jpeg", quality=75)
        img = Image.open(io.BytesIO(screenshot_bytes))
        webp_buffer = io.BytesIO()
        img.save(webp_buffer, format="WEBP", quality=90)
        webp_bytes = webp_buffer.getvalue()
        return base64.b64encode(webp_bytes).decode("utf-8")

    def _convert_coordinates(self, rel_x: int, rel_y: int) -> tuple[int, int]:
        abs_x = int(rel_x / 1000 * self.viewport_width)
        abs_y = int(rel_y / 1000 * self.viewport_height)
        return abs_x, abs_y

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

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _call_llm_with_retries(self) -> ChatCompletion:
        return await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                temperature=self.temperature,
                tools=self._memo_tool_suite.input_schemas,  # add custom tools here
            ),
            timeout=120.0,  # 2 minutes
        )

    async def _predict(self) -> ChatCompletion:
        screenshot_b64 = await self._take_screenshot()
        current_url = self._page.url

        last_content = self._messages[-1]["content"]
        if len(last_content) == 0:
            last_content.append({"type": "text", "text": f"Current URL: {current_url}"})
        last_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/webp;base64,{screenshot_b64}"},
            }
        )

        for i in range(self._message_index, len(self._messages)):
            logger.info(f"Message: {self._format_message_for_log(self._messages[i])}")

        response = await self._call_llm_with_retries()
        return response.choices[0].message

    async def _execute(self, tool_call: ChatCompletionMessageToolCall) -> tuple[bool, str | None]:
        # Returns (should_exit, result)
        action_name = tool_call.function.name

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse arguments: {tool_call.function.arguments}")
            return False, f"[ERROR] Failed to parse arguments: {tool_call.function.arguments}"

        try:
            if action_name == "left_click":
                coords = arguments.get("coordinates", [0, 0])
                abs_x, abs_y = self._convert_coordinates(coords[0], coords[1])
                await self._page.mouse.click(abs_x, abs_y)
                await asyncio.sleep(0.5)

            elif action_name == "double_click":
                coords = arguments.get("coordinates", [0, 0])
                abs_x, abs_y = self._convert_coordinates(coords[0], coords[1])
                await self._page.mouse.dblclick(abs_x, abs_y)
                await asyncio.sleep(0.5)

            elif action_name == "right_click":
                coords = arguments.get("coordinates", [0, 0])
                abs_x, abs_y = self._convert_coordinates(coords[0], coords[1])
                await self._page.mouse.click(abs_x, abs_y, button="right")
                await asyncio.sleep(0.5)

            elif action_name == "triple_click":
                coords = arguments.get("coordinates", [0, 0])
                abs_x, abs_y = self._convert_coordinates(coords[0], coords[1])
                await self._page.mouse.click(abs_x, abs_y, click_count=3)
                await asyncio.sleep(0.5)

            elif action_name == "type":
                text = arguments.get("text", "")
                press_enter = arguments.get("press_enter_after", False)
                clear_first = arguments.get("clear_before_typing", False)

                if clear_first:
                    await self._page.keyboard.press("Control+a" if sys.platform != "darwin" else "Meta+a")
                    await self._page.keyboard.press("Backspace")

                await self._page.keyboard.type(text, delay=50)

                if press_enter:
                    await self._page.keyboard.press("Enter")
                await asyncio.sleep(0.5)

            elif action_name in ("key", "key_press"):
                key = arguments.get("key") or arguments.get("key_comb", "")
                await self._page.keyboard.press(key)
                await asyncio.sleep(0.3)

            elif action_name == "scroll":
                coords = arguments.get("coordinates") or arguments.get("coordinate", [500, 500])
                direction = arguments.get("direction", "down")
                amount = arguments.get("amount", 3)

                abs_x, abs_y = self._convert_coordinates(coords[0], coords[1])
                scroll_delta = amount * (self.viewport_height * 0.1)

                delta_y = scroll_delta if direction == "down" else (-scroll_delta if direction == "up" else 0)
                delta_x = scroll_delta if direction == "right" else (-scroll_delta if direction == "left" else 0)

                await self._page.mouse.move(abs_x, abs_y)
                await self._page.mouse.wheel(delta_x, delta_y)
                await asyncio.sleep(0.5)

            elif action_name == "hover":
                coords = arguments.get("coordinates", [0, 0])
                abs_x, abs_y = self._convert_coordinates(coords[0], coords[1])
                await self._page.mouse.move(abs_x, abs_y)
                await asyncio.sleep(0.3)

            elif action_name == "drag":
                start_coords = arguments.get("start_coordinates") or arguments.get("startCoordinates", [0, 0])
                end_coords = arguments.get("coordinates") or arguments.get("endCoordinates", [0, 0])

                start_x, start_y = self._convert_coordinates(start_coords[0], start_coords[1])
                end_x, end_y = self._convert_coordinates(end_coords[0], end_coords[1])

                await self._page.mouse.move(start_x, start_y)
                await self._page.mouse.down()
                await self._page.mouse.move(end_x, end_y, steps=10)
                await self._page.mouse.up()
                await asyncio.sleep(0.5)

            elif action_name in ("goto", "goto_url"):
                url = arguments.get("url", "")
                await self._page.goto(url)
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

            elif action_name in ("back", "go_back"):
                await self._page.go_back()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.5)

            elif action_name == "refresh":
                await self._page.reload()
                await self._page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

            elif action_name == "wait":
                await asyncio.sleep(2)

            elif action_name == "add_question":
                result = await self._memo_tool_suite.add_question(**arguments)
                return False, result

            elif action_name == "add_options":
                result = await self._memo_tool_suite.add_options(**arguments)
                return False, result

            elif action_name == "list_records":
                result = await self._memo_tool_suite.list_records()
                return True, result

            else:
                logger.warning(f"Unknown action: {action_name}")
                return False, f"[ERROR] Unknown action: {action_name}"

            # Wait for any navigation or dynamic content
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error executing {action_name}: {e}")
            return False, f"[ERROR] Error executing {action_name}: {e}"

        return False, None


async def main():
    logger.remove()
    logger.level("DEBUG", color="<fg #808080>")
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{file}</cyan>:<cyan>{line:>3}</cyan> | "
            "<level>{message}</level>{exception}"
        ),
        colorize=True,
    )

    default_config = Config()
    parser = argparse.ArgumentParser(description="Example of using Yutori n1 API to perform a web browsing task")
    parser.add_argument("--task", default=default_config.task, help="The task to perform")
    parser.add_argument("--start-url", default=default_config.start_url, help="Starting URL")
    parser.add_argument(
        "--api-key",
        default=default_config.api_key,
        help="Yutori API key, or set YUTORI_API_KEY in environment variables",
    )
    parser.add_argument("--base-url", default=default_config.base_url, help="Yutori n1 base URL")
    parser.add_argument("--model", default=default_config.model, help="Yutori n1 model")
    parser.add_argument("--temperature", type=float, default=default_config.temperature, help="Yutori n1 temperature")
    parser.add_argument("--max-steps", type=int, default=default_config.max_steps, help="Maximum number of steps")
    parser.add_argument("--viewport-width", type=int, default=default_config.viewport_width, help="Viewport width")
    parser.add_argument("--viewport-height", type=int, default=default_config.viewport_height, help="Viewport height")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    args = parser.parse_args()
    config = Config.model_validate(vars(args))

    agent = Agent(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        max_steps=config.max_steps,
        viewport_width=config.viewport_width,
        viewport_height=config.viewport_height,
        headless=config.headless,
    )

    result = await agent.run(config.task, config.start_url)
    logger.info(f"Final result: {result or '(No final response from model)'}")


if __name__ == "__main__":
    asyncio.run(main())
