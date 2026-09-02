"""The Cua computer handler for Navigator n2 — the reference tool implementations.

Start from this file as a structural example when desktop operations cross an
API boundary, whether to local sandbox software or a remote service. Adapt its
provider calls, input units, result types, and process/file handling to your
API. When the adapter runs on an X11 desktop host and can access its display,
shell, and filesystem directly, start from ``direct_x11_adapter.py`` instead.

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
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from yutori.navigator import ShellFileToolsMixin
from yutori.navigator.sandbox_tools import PointerKeyLifecycleMixin
from yutori.navigator.sandbox_tools import (
    append_stream as _append_stream,
)
from yutori.navigator.sandbox_tools import (
    background_bash_log_path as _background_bash_log_path,
)
from yutori.navigator.sandbox_tools import (
    build_bash_result_paths as _build_bash_result_paths,
)
from yutori.navigator.sandbox_tools import (
    build_cwd_tracking_bash_script as _build_cwd_tracking_bash_script,
)
from yutori.navigator.sandbox_tools import (
    clamp_bash_timeout as _clamp_bash_timeout,
)
from yutori.navigator.sandbox_tools import (
    format_background_task_started as _format_background_task_started,
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
from yutori.navigator.sandbox_tools import (
    scroll_notches_from_pixels as _scroll_notches_from_pixels,
)
from yutori.navigator.sandbox_tools import (
    wait_for_file as _wait_for_file,
)


class CuaSandboxComputer(ShellFileToolsMixin, PointerKeyLifecycleMixin):
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
        """Send wheel notches through Cua's public mouse interface.

        Known server limitation: the local Linux image used with the pinned
        ``cua-sandbox==0.1.17`` currently routes this command to a legacy
        handler that ignores ``scroll_x`` and ``scroll_y``. The client-side
        values here are correct, but scrolling remains broken in that image
        until its server dispatch is fixed.
        """
        self._pointer = (x, y)
        notches_x, notches_y = await _scroll_notches_from_pixels(scroll_x, scroll_y, model_action, self.get_dimensions)
        await self._with_modifiers(
            modifier,
            lambda: self.sandbox.mouse.scroll(x, y, scroll_x=notches_x, scroll_y=notches_y),
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
            log_path = _background_bash_log_path("/tmp")
            session = await self.sandbox.terminal.create(
                f"cd {shlex.quote(cwd)} && exec /bin/bash -c {shlex.quote(command)} "
                f"> {shlex.quote(log_path)} 2>&1 < /dev/null"
            )
            process_id = str(session.get("pid") or "unknown")
            return _format_background_task_started(log_path, process_id)

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
        paths = _build_bash_result_paths("/tmp")
        script = _build_cwd_tracking_bash_script(command, cwd=cwd, cwd_path=paths.cwd, status_path=paths.status)
        wrapped = f"(\n{script}) < /dev/null > {shlex.quote(paths.stdout)} 2> {shlex.quote(paths.stderr)}"
        session = await self.sandbox.terminal.create(wrapped)
        process_id = session.get("pid")
        if not isinstance(process_id, int) or process_id <= 0:
            raise RuntimeError("Cua PTY did not return a valid process id.")

        if not await _wait_for_file(lambda: self.sandbox.files.exists(paths.status), timeout_s):
            try:
                await asyncio.wait_for(self.sandbox.terminal.close(process_id), timeout=5)
            except Exception:  # noqa: BLE001 - the disposable sandbox is the final cleanup boundary
                pass
            await self._remove_bash_result_files(*paths.cleanup_paths())
            return f"Command timed out after {timeout_s:g}s"

        try:
            stdout, stderr, status, new_cwd = await asyncio.gather(
                self.sandbox.files.read_text(paths.stdout),
                self.sandbox.files.read_text(paths.stderr),
                self.sandbox.files.read_text(paths.status),
                self.sandbox.files.read_text(paths.cwd),
            )
            self._bash_cwd = new_cwd.strip() or cwd
            body = _append_stream(stdout, stderr)
            return _format_shell_output(body, int(status.strip()))
        finally:
            await self._remove_bash_result_files(*paths.cleanup_paths())

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
