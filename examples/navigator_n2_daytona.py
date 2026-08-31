#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "daytona==0.207.0",
#   "yutori>=0.9.5",
# ]
# ///
"""
A computer-use agent: Navigator n2 driving a disposable Daytona Linux desktop.

Navigator n2 looks at a full-screen screenshot and answers with tool calls — a
`computer_batch` of GUI actions, or a `bash` command. `N2ComputerAgent`, the
SDK's n2 agent loop, executes each call through a small adapter, sends the
result back (a fresh screenshot for the batch, the output for bash), and asks
again until the model answers with text.

Daytona is third-party infrastructure; this Yutori-maintained example contains
the whole integration — the `DaytonaComputer` adapter plus the sandbox
lifecycle in `main`. Swap those pieces for your own environment to drive a
different desktop.

The adapter deliberately implements the smallest contract-valid surface — GUI
plus `bash` over Daytona's REST primitives — so its choices are not the tool
contract. When adapting it, take the full surface (file tools, modifiers, held
actions) and the result formats n2 is trained on from api.md's "Navigator n2"
section, with `examples/navigator_n2/cua_adapter.py` as the full reference
implementation.

Usage:
    yutori auth login                         # or export YUTORI_API_KEY=...
    export DAYTONA_API_KEY=...                # https://app.daytona.io

    uv run examples/navigator_n2_daytona.py \\
        "Find the OS version and free disk space of this machine, and save a summary to a file on the desktop"

Add --record to save a screen recording of the run to n2-daytona-run.mp4.
Long runs compact automatically (the SDK default); each compaction prints a notice.

Walkthrough: https://docs.yutori.com/reference/n2-daytona
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import uuid

from yutori import AsyncYutoriClient
from yutori.navigator import TOOL_SET_COMPUTER_USE_BASH_BATCH, N2ComputerAgent, format_stop_and_summarize
from yutori.navigator.n2_compaction import response_message

# Any snapshot carrying Daytona's computer-use bundle. This one is a bare XFCE
# desktop at 1024x768; build your own to give the model a browser or an editor.
SNAPSHOT = "daytonaio/sandbox:0.6.0"

# One `keyboard.type` call longer than this is silently cut off by the sandbox.
TYPE_CHUNK_MAX_CHARS = 500

# Where --record saves the screen recording after the run.
RECORDING_PATH = "n2-daytona-run.mp4"

# The n2 `bash` tool caps one result at this many characters.
BASH_RESULT_MAX_CHARS = 30_000
MAX_STEPS = 30


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stable Navigator n2 on third-party Daytona infrastructure.")
    parser.add_argument("task", help="Task for stable Navigator n2")
    parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=MAX_STEPS,
        help=f"Maximum model turns (default: {MAX_STEPS})",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"Record the sandbox screen and save the video to {RECORDING_PATH} when the run ends.",
    )
    return parser.parse_args(argv)


def _truncate(text: str, max_chars: int = BASH_RESULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... output truncated, {len(text) - max_chars} more chars ...]"


# `bash` promises a working directory that persists across calls, but every
# `exec` is a fresh process. Each command reports where it ended up on this
# sentinel line and the next one starts there.
CWD_SENTINEL = "__YUTORI_N2_CWD__"


class CompactionNotice:
    """Print when the SDK's default compactor rewrites the conversation."""

    async def on_compaction(self, info: dict) -> None:
        print(f"Compacted context: {info['items_before']} -> {info['items_after']} items")


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

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int, model_action: dict | None = None) -> None:
        # Daytona wants wheel notches, and scrolls vertically only (so does n2).
        # Declaring `model_action=` makes the loop pass the model's own call, whose
        # `direction`/`amount` are already notches (see api.md, "Navigator n2"); the
        # pixel arithmetic — one notch of `amount` is a tenth of the screen — is the
        # fallback for callers that don't pass it.
        if model_action and model_action.get("direction"):
            direction, amount = str(model_action["direction"]), int(model_action.get("amount") or 3)
            if direction not in ("up", "down"):
                raise NotImplementedError("horizontal scrolling is not supported")
        elif scroll_y:
            direction = "down" if scroll_y > 0 else "up"
            amount = max(1, round(abs(scroll_y) / (self._height * 0.1)))
        else:
            if scroll_x:
                raise NotImplementedError("horizontal scrolling is not supported")
            return
        await self._cu.mouse.scroll(x, y, direction, amount)

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
            return (
                f"Started background task `bash_{log_path[-12:-4]}`.\n"
                f"stdout+stderr is streaming to: {log_path}\n"
                f"Process id: {pid}\n"
                f"To cancel: run bash with `kill {pid}`"
            )

        # The n2 bash contract: the timeout is clamped to [0, 600] and an expiry is
        # a normal result the model can react to, never a raised failure envelope.
        # (Daytona raises on expiry and discards the partial output.)
        timeout_s = max(0.0, min(float(120.0 if timeout is None else timeout), 600.0))
        if timeout_s == 0:
            return "Command timed out after 0s"
        # Run the command, then report the directory it finished in, keeping
        # the command's own exit code.
        wrapped = f'{command}\n__rc=$?\nprintf "\\n{CWD_SENTINEL}%s" "$PWD"\nexit $__rc'
        try:
            result = await self._sandbox.process.exec(wrapped, cwd=self._cwd, timeout=max(1, int(timeout_s)))
        except Exception as error:  # noqa: BLE001 - classify sandbox timeouts below
            # Match the class name (DaytonaProcessExecutionTimeoutError) and the
            # message ("command execution timeout" — which says "timeout", not
            # "timed out"), so a generically-named error is still classified.
            error_text = f"{type(error).__name__}: {error}".lower()
            if "timeout" in error_text or "timed out" in error_text:
                return f"Command timed out after {timeout_s:g}s"
            raise
        output, marker, cwd = result.result.rpartition(f"\n{CWD_SENTINEL}")
        if marker:
            self._cwd = cwd or self._cwd
        else:
            output = result.result  # killed before the sentinel could print (e.g. by a signal)
        # The tool owns its result text; this is the format n2 expects.
        output = _truncate(output)
        if result.exit_code:
            return f"Exit code {result.exit_code}\n{output}" if output else f"Exit code {result.exit_code}"
        return output or "(Bash completed with no output)"


async def main(task: str, max_steps: int = MAX_STEPS, record: bool = False) -> None:
    # Validate the Yutori credential before entering or allocating third-party infrastructure.
    async with AsyncYutoriClient() as client:
        await client.get_usage()

        from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams

        async with AsyncDaytona() as daytona:
            sandbox = await daytona.create(CreateSandboxFromSnapshotParams(snapshot=SNAPSHOT, ephemeral=True))
            recording = None
            try:
                computer = DaytonaComputer(sandbox)
                await computer.start()
                if record:
                    recording = await sandbox.computer_use.recording.start(label="yutori-n2")
                agent = N2ComputerAgent(
                    computer=computer,
                    completions=client.chat.completions,
                    model="n2",
                    # This compact Yutori-maintained adapter intentionally implements GUI and
                    # bash only, so it pins the compatible immutable batch-plus-bash contract.
                    tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
                    max_steps=max_steps,
                    callbacks=[CompactionNotice()],
                )

                async for step in agent.run(task):
                    for item in step.get("output") or []:
                        if item.get("type") == "message":
                            for part in item.get("content") or []:
                                if isinstance(part, dict) and part.get("text"):
                                    print(part["text"])
                        elif item.get("type") == "function_call":
                            print(f"ACTION {item.get('name')}: {item.get('arguments')}")
                        elif item.get("type") == "function_call_output":
                            output = item.get("output")
                            if isinstance(output, str):
                                print(output)
                            elif isinstance(output, dict) and output.get("result") is not None:
                                print(f"RESULT {json.dumps(output['result'], sort_keys=True)}")
            finally:
                try:
                    if recording is not None:
                        stopped = await sandbox.computer_use.recording.stop(recording.id)
                        await sandbox.computer_use.recording.download(recording.id, RECORDING_PATH)
                        seconds = stopped.duration_seconds or 0
                        print(f"Saved screen recording ({seconds:.0f}s) to {RECORDING_PATH}")
                finally:
                    await sandbox.delete(wait=True)

        if agent.stopped_by == "max_steps":
            # One summarize-only completion — sent by this harness itself via the
            # actor's exact next request, so a tool-call reply is never executed.
            # The sandbox is already deleted; only the Yutori client is needed.
            nudge = {"role": "user", "content": [{"type": "text", "text": format_stop_and_summarize(task)}]}
            response = await client.chat.completions.create(**agent.completion_request([nudge]))
            _, message = response_message(response)
            summary = message.get("content")
            if isinstance(summary, str) and summary.strip():
                print(f"Step cap reached; the model's summary of progress so far:\n{summary}")
            raise RuntimeError(f"Stopped after {max_steps} steps (summary above)")


if __name__ == "__main__":
    cli_args = parse_args()
    asyncio.run(main(cli_args.task, max_steps=cli_args.max_steps, record=cli_args.record))
