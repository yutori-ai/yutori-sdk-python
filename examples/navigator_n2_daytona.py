#!/usr/bin/env python
"""
A computer-use agent: Navigator n2 driving a disposable Daytona Linux desktop.

Navigator n2 looks at a full-screen screenshot and answers with tool calls — a
`computer_batch` of GUI actions, or a `bash` command. `N2ComputerAgent`, the
SDK's n2 agent loop, executes each call through a small adapter, sends the
result back (a fresh screenshot for the batch, the output for bash), and asks
again until the model answers with text.

Everything Daytona-specific lives in `DaytonaComputer`. Swap it for your own
adapter to drive a different desktop.

Usage:
    pip install 'yutori>=0.9.2' daytona      # Python 3.10+
    yutori auth login                         # or export YUTORI_API_KEY=...
    export DAYTONA_API_KEY=...                # https://app.daytona.io

    python examples/navigator_n2_daytona.py \\
        "Write 'hello from n2' to /tmp/demo.txt, then open a terminal and cat the file"

Watch the run: `await sandbox.get_preview_link(6080)` returns a noVNC URL.
Walkthrough: https://docs.yutori.com/reference/n2-daytona
"""

from __future__ import annotations

import asyncio
import shlex
import sys
import uuid

from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams

from yutori import AsyncYutoriClient
from yutori.navigator import TOOL_SET_COMPUTER_USE_LATEST, N2ComputerAgent

# Any snapshot carrying Daytona's computer-use bundle. This one is a bare XFCE
# desktop at 1024x768; build your own to give the model a browser or an editor.
SNAPSHOT = "daytonaio/sandbox:0.6.0"

# One `keyboard.type` call longer than this is silently cut off by the sandbox.
TYPE_CHUNK_MAX_CHARS = 500

# The n2 `bash` tool caps one result at this many characters.
BASH_RESULT_MAX_CHARS = 8_000
TRUNCATED_MARKER = "\n[result truncated]"

# `bash` promises a working directory that persists across calls, but every
# `exec` is a fresh process. Each command reports where it ended up on this
# sentinel line and the next one starts there.
CWD_SENTINEL = "__YUTORI_N2_CWD__"


class DaytonaComputer:
    """Adapts a Daytona sandbox to the handler protocol `N2ComputerAgent` calls.

    Coordinates arrive already scaled to the screen: the loop maps n2's
    normalized 1000x1000 space onto the dimensions of the screenshot it sent.
    """

    def __init__(self, sandbox) -> None:
        self._sandbox = sandbox
        self._cu = sandbox.computer_use
        self._height = 0
        self._cwd: str | None = None

    async def start(self) -> None:
        await self._cu.start()
        display = (await self._cu.display.get_info()).displays[0]
        self._height = display.height

    # -- observation -------------------------------------------------------

    async def screenshot(self) -> str:
        return (await self._cu.screenshot.take_full_screen(show_cursor=True)).screenshot

    # -- mouse -------------------------------------------------------------

    async def click(self, x: int, y: int, button: str = "left") -> None:
        await self._cu.mouse.click(x, y, button)

    async def double_click(self, x: int, y: int) -> None:
        await self._cu.mouse.click(x, y, "left", double=True)

    async def move(self, x: int, y: int) -> None:
        await self._cu.mouse.move(x, y)

    async def drag(self, path: list[dict[str, int]]) -> None:
        await self._cu.mouse.drag(path[0]["x"], path[0]["y"], path[-1]["x"], path[-1]["y"])

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        # The loop hands over a pixel distance — one notch of the model's `amount`
        # is a tenth of the screen. Daytona wants wheel notches, and scrolls
        # vertically only (so does n2).
        if scroll_y == 0:
            if scroll_x:
                raise NotImplementedError("horizontal scrolling is not supported")
            return
        notches = max(1, round(abs(scroll_y) / (self._height * 0.1)))
        await self._cu.mouse.scroll(x, y, "down" if scroll_y > 0 else "up", notches)

    # -- keyboard ----------------------------------------------------------

    async def type(self, text: str) -> None:
        # `keyboard.type` fails on control characters, so newlines and tabs go
        # out as key presses and the text between them is sent in chunks.
        buffer = ""
        for char in text:
            if char in ("\n", "\t"):
                await self._type_chunked(buffer)
                buffer = ""
                await self._cu.keyboard.press("enter" if char == "\n" else "tab")
            else:
                buffer += char
        await self._type_chunked(buffer)

    async def _type_chunked(self, text: str) -> None:
        for start in range(0, len(text), TYPE_CHUNK_MAX_CHARS):
            await self._cu.keyboard.type(text[start : start + TYPE_CHUNK_MAX_CHARS])

    async def keypress(self, keys: list[str]) -> None:
        # One call per chord: `ctrl+c` arrives as ["ctrl", "c"], `enter` as ["enter"].
        # Modifiers go in their own argument so a key that is itself "+" survives.
        await self._cu.keyboard.press(keys[-1], modifiers=keys[:-1] or None)

    async def wait(self, ms: int = 1000) -> None:
        await asyncio.sleep(ms / 1000)

    # -- shell -------------------------------------------------------------

    async def run_bash_command(self, command: str, timeout: float = 120.0, run_in_background: bool = False) -> str:
        if run_in_background:
            log_path = f"/tmp/yutori-n2-{uuid.uuid4().hex[:8]}.log"
            launched = await self._sandbox.process.exec(
                f"nohup sh -c {shlex.quote(command)} > {log_path} 2>&1 & echo $!",
                cwd=self._cwd,
                timeout=30,
            )
            pid = launched.result.strip()
            return f"Started background task (pid {pid}).\nOutput file: {log_path}\nCancel with: kill {pid}"

        # Run the command, then report the directory it finished in, keeping
        # the command's own exit code.
        wrapped = f'{command}\n__rc=$?\nprintf "\\n{CWD_SENTINEL}%s" "$PWD"\nexit $__rc'
        result = await self._sandbox.process.exec(wrapped, cwd=self._cwd, timeout=int(timeout))
        output, marker, cwd = result.result.rpartition(f"\n{CWD_SENTINEL}")
        if marker:
            self._cwd = cwd or self._cwd
        else:
            output = result.result  # killed by the timeout before it could report
        # Keep the whole result within the cap, markers included, so the loop's own
        # truncation never cuts off the exit code of a long failing command.
        exit_marker = f"\n[exit code {result.exit_code}]" if result.exit_code else ""
        budget = BASH_RESULT_MAX_CHARS - len(exit_marker)
        if len(output) > budget:
            output = output[: budget - len(TRUNCATED_MARKER)] + TRUNCATED_MARKER
        if not output:
            return exit_marker.lstrip("\n")
        return output + exit_marker


class StepLimit:
    """Stop the run after `max_steps` model turns; the loop calls this before each one."""

    def __init__(self, max_steps: int = 30) -> None:
        self.max_steps = max_steps
        self.steps = 0
        self.reached = False

    async def on_run_continue(self, _kwargs, _old_items, _new_items) -> bool:
        if self.steps >= self.max_steps:
            self.reached = True
            return False
        self.steps += 1
        return True


async def main(task: str) -> None:
    limit = StepLimit()
    # The client resolves credentials up front: a missing key must not cost a desktop.
    async with AsyncYutoriClient() as client, AsyncDaytona() as daytona:
        sandbox = await daytona.create(CreateSandboxFromSnapshotParams(snapshot=SNAPSHOT))
        try:
            computer = DaytonaComputer(sandbox)
            await computer.start()
            agent = N2ComputerAgent(
                computer=computer,
                completions=client.chat.completions,
                model="n2",  # SDK 0.9.2 still defaults to the deprecated "n2-preview" alias
                tool_set=TOOL_SET_COMPUTER_USE_LATEST,
                callbacks=[limit],
            )

            async for step in agent.run(task):
                for item in step.get("output") or []:
                    if item.get("type") == "message":
                        for part in item.get("content") or []:
                            if isinstance(part, dict) and part.get("text"):
                                print(part["text"])
                    elif item.get("type") == "function_call":
                        print(f"ACTION {item.get('name')}: {item.get('arguments')}")
        finally:
            await sandbox.delete()

    if limit.reached:
        raise RuntimeError(f"Stopped after {limit.max_steps} steps")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
