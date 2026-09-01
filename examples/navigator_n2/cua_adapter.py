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
when building a handler for your own environment. The ``computer_batch`` tool itself
lives in the SDK loop — this module implements what the loop delegates: the GUI
primitives it calls, and the full ``bash``/file-tool output contracts.

Cua here is the sandbox vendor (https://cua.ai, github.com/trycua), whose
``cua-sandbox`` package provides the disposable Docker desktop — not the generic
"computer-use agent" acronym used elsewhere in Yutori's own package names.
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
    clamp_bash_timeout as _clamp_bash_timeout,
)
from yutori.navigator.sandbox_tools import (
    format_shell_output as _format_shell_output,
)
from yutori.navigator.sandbox_tools import (
    format_shell_result as _shell_result,
)
from yutori.navigator.sandbox_tools import (
    result_returncode as _result_returncode,
)
from yutori.navigator.sandbox_tools import (
    result_stdout as _result_stdout,
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
        model_action: dict[str, Any] | None = None,
    ) -> None:
        self._pointer = (x, y)
        notches_x, notches_y = await self._scroll_notches(scroll_x, scroll_y, model_action)
        await self._with_modifiers(
            modifier,
            lambda: self.sandbox.mouse.scroll(x, y, scroll_x=notches_x, scroll_y=notches_y),
        )

    async def _scroll_notches(
        self,
        scroll_x: int,
        scroll_y: int,
        model_action: dict[str, Any] | None,
    ) -> tuple[int, int]:
        """Convert the loop's pixel deltas into Cua wheel notches.

        The loop's ``scroll_x``/``scroll_y`` are pixel deltas (10% of the native
        dimension per model unit, positive = down/right), but Cua's identically
        named parameters are wheel notches with pynput signs (positive = up/right
        — its own default is ``scroll_y=3``, a notch count). Passing the pixels
        through scrolls the wrong way by two orders of magnitude. The model's
        call carries the exact notch count, so prefer it; otherwise recover it
        by inverting the loop's translation.
        """
        action = model_action or {}
        direction, amount = action.get("direction"), action.get("amount")
        if direction in ("up", "down", "left", "right") and type(amount) is int and amount > 0:
            if direction in ("up", "down"):
                return 0, amount if direction == "up" else -amount
            return (amount if direction == "right" else -amount), 0
        width, height = await self.get_dimensions()
        if scroll_y:
            notches = max(1, round(abs(scroll_y) / (0.1 * height)))
            return 0, (-notches if scroll_y > 0 else notches)
        if scroll_x:
            notches = max(1, round(abs(scroll_x) / (0.1 * width)))
            return (notches if scroll_x > 0 else -notches), 0
        return 0, 0

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
        if run_in_background:
            log_path = f"/tmp/yutori-n2-bash-{uuid.uuid4().hex[:8]}.log"
            session = await self.sandbox.terminal.create(
                f"cd {shlex.quote(cwd)} && exec /bin/bash -c {shlex.quote(command)} "
                f"> {shlex.quote(log_path)} 2>&1 < /dev/null"
            )
            process_id = str(session.get("pid") or "unknown")
            return (
                f"Started background task `bash_{log_path[-12:-4]}`.\n"
                f"stdout+stderr is streaming to: {log_path}\n"
                "Use the read tool on that file to retrieve output.\n"
                f"Process id: {process_id}\n"
                f"To cancel: run bash with `kill {process_id}`"
            )

        # The n2 bash contract: the timeout is clamped to [0, 600] and an expiry is a
        # NORMAL result the model can react to, never a raised failure envelope.
        timeout_s = _clamp_bash_timeout(timeout)
        if timeout_s == 0:
            return "Command timed out after 0s"

        # cua-computer-server runs /cmd subprocesses on uvloop. If a shell command
        # leaves any descendant alive (for example ``xcalc &``), uvloop reports that
        # the shell exited but never finishes communicate(), so /cmd waits until its
        # client timeout. Run bash in Cua's public PTY API instead. Redirecting all
        # three standard streams prevents descendants from retaining the PTY, while
        # the atomic status file distinguishes shell completion from output that a
        # deliberately backgrounded descendant may continue to produce.
        token = uuid.uuid4().hex
        result_prefix = f"/tmp/yutori-n2-bash-{token}"
        stdout_path = f"{result_prefix}.stdout"
        stderr_path = f"{result_prefix}.stderr"
        status_path = f"{result_prefix}.status"
        cwd_path = f"{result_prefix}.cwd"
        inner_cwd_tmp = f"{cwd_path}.tmp"
        status_tmp = f"{status_path}.tmp"
        finish = f"__yutori_finish_{token}"
        inner = (
            f"{finish}() {{\n"
            f"  printf '%s\\n' \"$PWD\" > {shlex.quote(inner_cwd_tmp)}\n"
            f"  mv {shlex.quote(inner_cwd_tmp)} {shlex.quote(cwd_path)}\n"
            "}\n"
            f"trap {finish} 0\n"
            f"{command}"
        )
        wrapped = (
            "(\n"
            f"cd {shlex.quote(cwd)}\n"
            "__yutori_cd_rc=$?\n"
            'if [ "$__yutori_cd_rc" -eq 0 ]; then\n'
            f"  /bin/bash -c {shlex.quote(inner)}\n"
            "  __yutori_rc=$?\n"
            "else\n"
            "  __yutori_rc=$__yutori_cd_rc\n"
            "fi\n"
            f"if [ ! -f {shlex.quote(cwd_path)} ]; then\n"
            f"  printf '%s\\n' \"$PWD\" > {shlex.quote(cwd_path)}\n"
            "fi\n"
            f"printf '%s' \"$__yutori_rc\" > {shlex.quote(status_tmp)}\n"
            f"mv {shlex.quote(status_tmp)} {shlex.quote(status_path)}\n"
            'exit "$__yutori_rc"\n'
            f") < /dev/null > {shlex.quote(stdout_path)} 2> {shlex.quote(stderr_path)}"
        )
        session = await self.sandbox.terminal.create(wrapped)
        process_id = session.get("pid")
        if not isinstance(process_id, int) or process_id <= 0:
            raise RuntimeError("Cua PTY did not return a valid process id.")

        async def wait_for_status() -> None:
            delay = 0.01
            while not await self.sandbox.files.exists(status_path):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 0.25)

        try:
            await asyncio.wait_for(wait_for_status(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                await asyncio.wait_for(self.sandbox.terminal.close(process_id), timeout=5)
            except Exception:  # noqa: BLE001 - the disposable sandbox is the final cleanup boundary
                pass
            await self._remove_bash_result_files(
                stdout_path,
                stderr_path,
                status_path,
                status_tmp,
                cwd_path,
                inner_cwd_tmp,
            )
            return f"Command timed out after {timeout_s:g}s"

        try:
            stdout, stderr, status, new_cwd = await asyncio.gather(
                self.sandbox.files.read_text(stdout_path),
                self.sandbox.files.read_text(stderr_path),
                self.sandbox.files.read_text(status_path),
                self.sandbox.files.read_text(cwd_path),
            )
            self._bash_cwd = new_cwd.strip() or cwd
            body = _append_stream(stdout, stderr)
            return _format_shell_output(body, int(status.strip()))
        finally:
            await self._remove_bash_result_files(
                stdout_path,
                stderr_path,
                status_path,
                status_tmp,
                cwd_path,
                inner_cwd_tmp,
            )

    async def _remove_bash_result_files(self, *paths: str) -> None:
        await asyncio.gather(
            *(self.sandbox.files.remove(path) for path in paths),
            return_exceptions=True,
        )

    async def _working_directory(self) -> str:
        if self._bash_cwd is None:
            result = await self.sandbox.shell.run("pwd", timeout=30)
            if _result_returncode(result) != 0:
                raise RuntimeError(_shell_result(result))
            self._bash_cwd = _result_stdout(result).strip()
        if not self._bash_cwd:
            raise RuntimeError("Sandbox shell did not report a working directory.")
        return self._bash_cwd

    async def run_sandbox_shell(self, command: str, *, timeout_seconds: int) -> Any:
        return await self.sandbox.shell.run(command, timeout=timeout_seconds)

    async def file_tool_cwd(self) -> str:
        return await self._working_directory()
