#!/usr/bin/env python
"""
A computer-use agent: Navigator n2 driving a disposable Daytona Linux desktop.

Navigator n2 looks at a full-screen screenshot and answers with tool calls — a
`computer_batch` of GUI actions, or a `bash` command. Cua's `ComputerAgent`
runs the loop: it sends the task and screenshot, executes each call through
the `DaytonaComputer` adapter below, returns the result to the model (a fresh
screenshot for the batch, the output for bash), and asks again until the model
answers with text.

Everything Daytona-specific lives in `DaytonaComputer`. Swap it for your own
adapter to drive a different desktop.

Usage:
    pip install cua-agent daytona yutori       # Python 3.12 or 3.13
    yutori auth login                          # or export YUTORI_API_KEY=...
    export DAYTONA_API_KEY=...                 # https://app.daytona.io

    python examples/navigator_n2_daytona.py \\
        "Write 'hello from n2' to /tmp/demo.txt, then open a terminal and cat the file"

Walkthrough: https://docs.yutori.com/reference/n2-daytona
"""

import asyncio
import sys

from cua_agent import ComputerAgent
from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams

from yutori.auth import require_api_key
from yutori.navigator import TOOL_SET_COMPUTER_USE_LATEST

# Any snapshot carrying Daytona's computer-use bundle.
SNAPSHOT = "daytonaio/sandbox:0.6.0"

# One `keyboard.type` call longer than this is silently cut off by the sandbox.
TYPE_CHUNK_MAX_CHARS = 500


class DaytonaComputer:
    """Adapts a Daytona sandbox to the handler protocol the loop calls."""

    def __init__(self, sandbox) -> None:
        self._sandbox = sandbox
        self._cu = sandbox.computer_use

    async def get_environment(self) -> str:
        return "linux"

    async def get_dimensions(self) -> tuple[int, int]:
        display = (await self._cu.display.get_info()).displays[0]
        return display.width, display.height

    async def screenshot(self, text: str | None = None) -> str:
        return (await self._cu.screenshot.take_full_screen(show_cursor=True)).screenshot

    async def click(self, x: int, y: int, button: str = "left") -> None:
        await self._cu.mouse.click(x, y, button)

    async def double_click(self, x: int, y: int) -> None:
        await self._cu.mouse.click(x, y, "left", double=True)

    async def move(self, x: int, y: int) -> None:
        await self._cu.mouse.move(x, y)

    async def drag(self, path: list[dict[str, int]]) -> None:
        await self._cu.mouse.drag(path[0]["x"], path[0]["y"], path[-1]["x"], path[-1]["y"])

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        await self._cu.mouse.scroll(x, y, "down" if scroll_y > 0 else "up", max(1, round(abs(scroll_y) / 100)))

    async def type(self, text: str) -> None:
        async def flush(pending: str) -> None:
            for start in range(0, len(pending), TYPE_CHUNK_MAX_CHARS):
                await self._cu.keyboard.type(pending[start : start + TYPE_CHUNK_MAX_CHARS])

        buffer = ""
        for char in text:
            if char in ("\n", "\t"):
                await flush(buffer)
                buffer = ""
                await self._cu.keyboard.press("enter" if char == "\n" else "tab")
            else:
                buffer += char
        await flush(buffer)

    async def keypress(self, keys: list[str] | str) -> None:
        if isinstance(keys, list):
            key_sequence = ["+".join(keys)] if len(keys) > 1 else keys
        else:
            key_sequence = keys.split()
        for key in key_sequence:
            if "+" in key:
                await self._cu.keyboard.hotkey(key)
            else:
                await self._cu.keyboard.press(key)

    async def wait(self, ms: int = 1000) -> None:
        await asyncio.sleep(ms / 1000)

    async def get_current_url(self) -> str:
        return ""

    async def left_mouse_down(self, x: int | None = None, y: int | None = None) -> None:
        raise NotImplementedError("held clicks are not supported")

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        raise NotImplementedError("held clicks are not supported")

    async def run_bash_command(self, command: str, timeout: float = 120.0, run_in_background: bool = False) -> str:
        result = await self._sandbox.process.exec(command, timeout=int(timeout))
        output = result.result or ""
        if result.exit_code:
            output = f"{output}\n[exit code {result.exit_code}]"
        return output[:8000]


class RunGuard:
    def __init__(self, max_steps: int = 30) -> None:
        self.max_steps = max_steps
        self.steps = 0
        self.limit_reached = False

    async def on_run_continue(self, _kwargs, _old, _new) -> bool:
        if self.steps >= self.max_steps:
            self.limit_reached = True
            return False
        self.steps += 1
        return True

    async def on_computer_call_end(self, _item, outputs) -> None:
        for output in outputs:
            value = output.get("output")
            if isinstance(value, str) and value.startswith("[ERROR]"):
                if not value.startswith("[ERROR] bash"):
                    raise RuntimeError(value)


async def main(task: str) -> None:
    guard = RunGuard()
    # Before the sandbox exists: a missing key should not cost a desktop.
    api_key = require_api_key()

    async with AsyncDaytona() as daytona:
        sandbox = await daytona.create(CreateSandboxFromSnapshotParams(snapshot=SNAPSHOT))
        try:
            await sandbox.computer_use.start()
            agent = ComputerAgent(
                model="yutori/n2",
                tools=[DaytonaComputer(sandbox)],
                api_key=api_key,
                tool_set=TOOL_SET_COMPUTER_USE_LATEST,
                callbacks=[guard],
            )

            async for response in agent.run(task, stream=False):
                for item in response.get("output") or []:
                    if item.get("type") == "message":
                        for part in item.get("content") or []:
                            if isinstance(part, dict) and part.get("text"):
                                print(part["text"])
                    elif item.get("type") == "function_call":
                        print(f"ACTION {item.get('name')}: {item.get('arguments')}")
        finally:
            await sandbox.delete()

    if guard.limit_reached:
        raise RuntimeError(f"Stopped after {guard.max_steps} steps")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
