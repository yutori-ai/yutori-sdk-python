"""The Cua computer handler for Navigator n2 — the reference tool implementations.

``CuaSandboxComputer`` implements the full handler surface `N2ComputerAgent` calls
(GUI primitives, ``run_bash_command`` with a persistent working directory, and the
``read``/``write``/``edit``/``grep``/``glob`` file tools) on the public ``cua`` sandbox
API, rendering every result in the exact format n2 expects:
``Exit code N`` headers, ``(Bash completed with no output)``, ``Command timed out after
Xs`` as a normal result, ``cat -n`` line numbering with the ``[... output truncated,
N more chars ...]`` caps, image reads returned as visible image content, the
sha256-fingerprint read-before-edit gate, and every expected tool error as a plain
``ERROR: ...`` result rather than a raised failure envelope. Copy or adapt this module
when building a handler for your own environment.
"""

from __future__ import annotations

import asyncio
import shlex
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from yutori.navigator import ShellFileToolsMixin
from yutori.navigator.sandbox_tools import (
    append_stream as _append_stream,
)
from yutori.navigator.sandbox_tools import (
    format_shell_output as _format_shell_output,
)
from yutori.navigator.sandbox_tools import (
    format_shell_result as _shell_result,
)


class CuaSandboxComputer(ShellFileToolsMixin):
    """N2 computer-handler adapter built only on the public cua-sandbox API."""

    def __init__(self, sandbox: Any) -> None:
        self.sandbox = sandbox
        self._bash_cwd: str | None = None
        self._pointer: tuple[int, int] | None = None
        self._left_mouse_down = False

    async def screenshot(self) -> str:
        screenshot = await self.sandbox.screenshot_base64(format="jpeg", quality=80)
        return screenshot if screenshot.startswith("data:") else f"data:image/jpeg;base64,{screenshot}"

    async def get_dimensions(self) -> tuple[int, int]:
        return await self.sandbox.get_dimensions()

    async def _with_modifiers(
        self,
        modifier: Sequence[str] | None,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        keys = list(modifier or ())
        for key in keys:
            await self.sandbox.keyboard.key_down(key)
        try:
            await action()
        finally:
            for key in reversed(keys):
                await self.sandbox.keyboard.key_up(key)

    async def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        modifier: Sequence[str] | None = None,
    ) -> None:
        self._pointer = (x, y)
        await self._with_modifiers(modifier, lambda: self.sandbox.mouse.click(x, y, button=button))

    async def double_click(self, x: int, y: int, modifier: Sequence[str] | None = None) -> None:
        self._pointer = (x, y)
        await self._with_modifiers(modifier, lambda: self.sandbox.mouse.double_click(x, y))

    async def triple_click(self, x: int, y: int, modifier: Sequence[str] | None = None) -> None:
        self._pointer = (x, y)

        async def click_three_times() -> None:
            for _ in range(3):
                await self.sandbox.mouse.click(x, y)

        await self._with_modifiers(modifier, click_three_times)

    async def move(self, x: int, y: int) -> None:
        self._pointer = (x, y)
        await self.sandbox.mouse.move(x, y)

    async def scroll(
        self,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
        modifier: Sequence[str] | None = None,
    ) -> None:
        self._pointer = (x, y)
        await self._with_modifiers(
            modifier,
            lambda: self.sandbox.mouse.scroll(x, y, scroll_x=scroll_x, scroll_y=scroll_y),
        )

    async def drag(self, path: list[dict[str, int]]) -> None:
        if len(path) < 2:
            raise ValueError("drag path must contain at least two points")
        start, end = path[0], path[-1]
        self._pointer = (end["x"], end["y"])
        await self.sandbox.mouse.drag(start["x"], start["y"], end["x"], end["y"])

    async def type(self, text: str) -> None:
        await self.sandbox.keyboard.type(text)

    async def keypress(self, keys: Sequence[str] | str) -> None:
        await self.sandbox.keyboard.keypress(keys)

    async def key_down(self, key: str) -> None:
        await self.sandbox.keyboard.key_down(key)

    async def key_up(self, key: str) -> None:
        await self.sandbox.keyboard.key_up(key)

    async def hold_key(self, key: str, ms: int = 1_000) -> None:
        await self.key_down(key)
        try:
            await asyncio.sleep(ms / 1_000)
        finally:
            await self.key_up(key)

    async def wait(self, ms: int = 1_000) -> None:
        await asyncio.sleep(ms / 1_000)

    def _resolve_and_set_pointer(self, x: int | None, y: int | None, *, action: str) -> tuple[int, int]:
        if (x is None) != (y is None):
            raise ValueError(f"{action} coordinates must include both x and y")
        point = (x, y) if x is not None and y is not None else self._pointer
        if point is None:
            raise ValueError(f"{action} requires coordinates before the pointer has moved")
        self._pointer = point
        return point

    async def left_mouse_down(self, x: int | None = None, y: int | None = None) -> None:
        point = self._resolve_and_set_pointer(x, y, action="mouse_down")
        await self.sandbox.mouse.mouse_down(*point)
        self._left_mouse_down = True

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        point = self._resolve_and_set_pointer(x, y, action="mouse_up")
        try:
            await self.sandbox.mouse.mouse_up(*point)
        finally:
            self._left_mouse_down = False

    async def release_held_mouse_button(self) -> None:
        if self._left_mouse_down:
            await self.left_mouse_up()

    async def run_shell_command(
        self,
        command: str,
        cwd: str | None = None,
        timeout_seconds: int = 10,
    ) -> str:
        prefix = f"cd {shlex.quote(cwd)} && " if cwd else ""
        return _shell_result(await self.sandbox.shell.run(f"{prefix}{command}", timeout=timeout_seconds))

    async def run_bash_command(
        self,
        command: str,
        timeout: float = 120.0,
        run_in_background: bool = False,
    ) -> str:
        cwd = await self._working_directory()
        prefix = f"cd {shlex.quote(cwd)} && "
        if run_in_background:
            log_path = f"/tmp/yutori-n2-bash-{uuid.uuid4().hex[:8]}.log"
            result = await self.sandbox.shell.run(
                f"{prefix}nohup sh -c {shlex.quote(command)} > {log_path} 2>&1 < /dev/null & echo $!",
                timeout=30,
            )
            process_id = str(getattr(result, "stdout", "") or "").strip() or "unknown"
            return (
                f"Started background task `bash_{log_path[-12:-4]}`.\n"
                f"stdout+stderr is streaming to: {log_path}\n"
                "Use the read tool on that file to retrieve output.\n"
                f"Process id: {process_id}\n"
                f"To cancel: run bash with `kill {process_id}`"
            )

        sentinel = f"__YUTORI_N2_BASH_CWD_{uuid.uuid4().hex}__"
        wrapped = f"{prefix}{command}\n__yutori_rc=$?\nprintf '\\n{sentinel}%s' \"$PWD\"\nexit $__yutori_rc"
        # The n2 bash contract: the timeout is clamped to [0, 600] and an expiry is a
        # NORMAL result the model can react to, never a raised failure envelope. (The
        # sandbox API discards partial output on expiry, so the result is the bare line.)
        timeout_s = max(0.0, min(float(120.0 if timeout is None else timeout), 600.0))
        if timeout_s == 0:
            return "Command timed out after 0s"
        try:
            result = await self.sandbox.shell.run(wrapped, timeout=timeout_s)
        except Exception as error:  # noqa: BLE001 - classify sandbox timeouts below
            if "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower():
                return f"Command timed out after {timeout_s:g}s"
            raise
        stdout = str(getattr(result, "stdout", "") or "")
        body, marker, new_cwd = stdout.rpartition(f"\n{sentinel}")
        if marker:
            self._bash_cwd = new_cwd.strip() or cwd
            body = _append_stream(body, str(getattr(result, "stderr", "") or ""))
            return _format_shell_output(body, int(getattr(result, "returncode", 0) or 0))
        return _shell_result(result)

    async def _working_directory(self) -> str:
        if self._bash_cwd is None:
            result = await self.sandbox.shell.run("pwd", timeout=30)
            if int(getattr(result, "returncode", 0) or 0) != 0:
                raise RuntimeError(_shell_result(result))
            self._bash_cwd = str(getattr(result, "stdout", "") or "").strip()
        if not self._bash_cwd:
            raise RuntimeError("Sandbox shell did not report a working directory.")
        return self._bash_cwd

    async def run_sandbox_shell(self, command: str, *, timeout_seconds: int) -> Any:
        return await self.sandbox.shell.run(command, timeout=timeout_seconds)

    async def file_tool_cwd(self) -> str:
        return await self._working_directory()
