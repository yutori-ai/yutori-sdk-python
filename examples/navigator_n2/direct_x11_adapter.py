"""A direct X11 Linux computer handler for Navigator n2.

Start from this file when the adapter runs on the X11 desktop host and can
access its display, shell, and filesystem directly. That host may be a local
machine or a VM. When those operations cross an API boundary instead, start
from ``cua_adapter.py`` as an API-backed structural example.

``LocalX11Computer`` implements the full handler surface `N2ComputerAgent` calls
directly against the desktop that ``$DISPLAY`` points at: GUI primitives through
pyautogui, whose X11 wheel notches, key events, and drags are the native units
n2's actions map onto, screenshots through mss, and ``bash`` plus the ``read``/
``write``/``edit``/``grep``/``glob`` file tools through local subprocesses in the
exact result formats n2 expects.

Scope and safety:

- **X11 only.** pyautogui emits X11 input events; on a Wayland session synthetic
  input fails or half-works through XWayland. Use an "on Xorg" session, or point
  ``$DISPLAY`` at a virtual server (Xvfb/x11vnc).
- **This is a real machine, not a disposable sandbox.** The agent moves your
  mouse, types on your keyboard, and runs shell commands as your user. Prefer a
  dedicated VM or virtual display, and keep the confirmation callback armed.

Unlike ``cua_adapter.py`` (the same contract across a sandbox API), this adapter
calls the X server and local OS primitives directly.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from yutori.navigator import ShellFileToolsMixin
from yutori.navigator.sandbox_tools import (
    append_stream as _append_stream,
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
    scroll_notches_from_pixels as _scroll_notches_from_pixels,
)
from yutori.navigator.sandbox_tools import (
    wait_for_file as _wait_for_file,
)

# The SDK loop's key vocabulary is already canonical lowercase (punctuation
# arrives as literal characters); only these names spell differently in
# pyautogui. ``cmd`` maps to the Super/Windows key.
_PYAUTOGUI_KEY_MAP = {
    "cmd": "win",
    "page_up": "pageup",
    "page_down": "pagedown",
}

# PyAutoGUI's X11 mapping gives ``<`` its own keysym, so adding Shift (as its
# platform-neutral predicate does) produces the wrong key event on Linux.
_X11_SHIFT_CHARACTERS = frozenset('~!@#$%^&*()_+{}|:">?')

_DRAG_SECONDS = 0.5  # paced so the target registers one continuous drag, not a click


def _map_key(key: str) -> str:
    return _PYAUTOGUI_KEY_MAP.get(key, key)


def _is_x11_shift_character(character: str) -> bool:
    return character.isupper() or character in _X11_SHIFT_CHARACTERS


def _is_directly_typeable(gui: Any, character: str) -> bool:
    # KEYBOARD_KEYS omits uppercase letters even though PyAutoGUI's X11 backend
    # has mappings for them and synthesizes them with Shift.
    return character in gui.KEYBOARD_KEYS or (
        character.isascii() and character.isupper() and character.lower() in gui.KEYBOARD_KEYS
    )


class LocalX11Computer(ShellFileToolsMixin):
    """N2 computer handler with direct access to an X11 display and local shell.

    ``gui`` exists for tests: any object with pyautogui's surface (``click``,
    ``scroll``, ``keyDown``, ...) can stand in. By default pyautogui is imported
    lazily on first GUI action, so this module imports cleanly on hosts without
    an X11 stack.
    """

    def __init__(self, cwd: str | None = None, *, gui: Any = None) -> None:
        self._gui_module = gui
        self._bash_cwd = cwd or str(Path.home())
        self._left_mouse_down = False
        # python-xlib Display objects are not thread-safe, and pyautogui opens one
        # at import. Pin the import and every GUI call to this one thread so the
        # connection is created and used on a single thread for the adapter's life.
        self._gui_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="x11-gui")

    def _gui(self) -> Any:
        if self._gui_module is None:
            import pyautogui

            # The agent legitimately targets screen corners; the fail-safe would
            # abort those actions. Pacing comes from the loop, not per-call pauses.
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0.05
            pyautogui.isShiftCharacter = _is_x11_shift_character
            self._gui_module = pyautogui
        return self._gui_module

    async def _run_gui(self, action: Callable[[], Any]) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._gui_thread, action)

    # -- observation --------------------------------------------------------

    async def screenshot(self) -> str:
        width, height = await self.get_dimensions()

        def grab() -> str:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                # Match the root-screen coordinate space used by PyAutoGUI
                # instead of selecting one physical monitor from an X11 layout.
                frame = sct.grab({"left": 0, "top": 0, "width": width, "height": height})
                image = Image.frombytes("RGB", frame.size, frame.bgra, "raw", "BGRX")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

        return await asyncio.to_thread(grab)

    async def get_dimensions(self) -> tuple[int, int]:
        size = await self._run_gui(lambda: self._gui().size())
        return int(size[0]), int(size[1])

    # -- GUI actions ---------------------------------------------------------

    async def _with_modifiers(
        self,
        modifier: Sequence[str] | None,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        keys = [_map_key(key) for key in modifier or ()]
        for key in keys:
            await self._run_gui(lambda k=key: self._gui().keyDown(k))
        try:
            await action()
        finally:
            for key in reversed(keys):
                await self._run_gui(lambda k=key: self._gui().keyUp(k))

    async def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        modifier: Sequence[str] | None = None,
    ) -> None:
        await self._with_modifiers(modifier, lambda: self._run_gui(lambda: self._gui().click(x, y, button=button)))

    async def double_click(self, x: int, y: int, modifier: Sequence[str] | None = None) -> None:
        await self._with_modifiers(modifier, lambda: self._run_gui(lambda: self._gui().doubleClick(x, y)))

    async def triple_click(self, x: int, y: int, modifier: Sequence[str] | None = None) -> None:
        await self._with_modifiers(modifier, lambda: self._run_gui(lambda: self._gui().tripleClick(x, y)))

    async def move(self, x: int, y: int) -> None:
        await self._run_gui(lambda: self._gui().moveTo(x, y))

    async def drag(self, path: list[dict[str, int]]) -> None:
        if len(path) < 2:
            raise ValueError("drag path must contain at least two points")
        start, end = path[0], path[-1]

        def run() -> None:
            gui = self._gui()
            gui.moveTo(start["x"], start["y"])
            gui.dragTo(end["x"], end["y"], duration=_DRAG_SECONDS, button="left")

        await self._run_gui(run)

    async def scroll(
        self,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
        modifier: Sequence[str] | None = None,
        model_action: dict[str, Any] | None = None,
    ) -> None:
        notches_x, notches_y = await _scroll_notches_from_pixels(scroll_x, scroll_y, model_action, self.get_dimensions)

        def run() -> None:
            gui = self._gui()
            # pyautogui signs: positive scroll() is up, positive hscroll() is right.
            if notches_y:
                gui.scroll(notches_y, x, y)
            if notches_x:
                gui.hscroll(notches_x, x, y)

        await self._with_modifiers(modifier, lambda: self._run_gui(run))

    async def type(self, text: str) -> None:
        def run() -> None:
            gui = self._gui()
            if all(_is_directly_typeable(gui, char) for char in text):
                gui.write(text, interval=0.01)
                return
            # Characters X11 key synthesis cannot produce (non-ASCII) go
            # through the clipboard instead.
            xclip = shutil.which("xclip")
            if xclip is None:
                raise RuntimeError("text contains characters pyautogui cannot type; install xclip for clipboard paste")
            subprocess.run([xclip, "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
            gui.hotkey("ctrl", "v")

        await self._run_gui(run)

    async def keypress(self, keys: Sequence[str] | str) -> None:
        sequence = [keys] if isinstance(keys, str) else list(keys)
        mapped = [_map_key(key) for key in sequence]

        def run() -> None:
            gui = self._gui()
            if len(mapped) == 1:
                gui.press(mapped[0])
                return
            for key in mapped:
                gui.keyDown(key)
            for key in reversed(mapped):
                gui.keyUp(key)

        await self._run_gui(run)

    async def key_down(self, key: str) -> None:
        await self._run_gui(lambda: self._gui().keyDown(_map_key(key)))

    async def key_up(self, key: str) -> None:
        await self._run_gui(lambda: self._gui().keyUp(_map_key(key)))

    async def hold_key(self, key: str, ms: int = 1_000) -> None:
        await self.key_down(key)
        try:
            await asyncio.sleep(ms / 1_000)
        finally:
            await self.key_up(key)

    async def wait(self, ms: int = 1_000) -> None:
        await asyncio.sleep(ms / 1_000)

    async def left_mouse_down(self, x: int | None = None, y: int | None = None) -> None:
        def run() -> None:
            gui = self._gui()
            if x is not None and y is not None:
                gui.mouseDown(x=x, y=y, button="left")
            else:
                gui.mouseDown(button="left")

        await self._run_gui(run)
        self._left_mouse_down = True

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        def run() -> None:
            gui = self._gui()
            if x is not None and y is not None:
                gui.mouseUp(x=x, y=y, button="left")
            else:
                gui.mouseUp(button="left")

        try:
            await self._run_gui(run)
        finally:
            self._left_mouse_down = False

    async def release_held_mouse_button(self) -> None:
        if self._left_mouse_down:
            await self.left_mouse_up()

    # -- shell / file tools --------------------------------------------------

    async def run_shell_command(
        self,
        command: str,
        cwd: str | None = None,
        timeout_seconds: int = 10,
    ) -> str:
        prefix = f"cd {shlex.quote(cwd)} && " if cwd else ""
        return _shell_result(await self.run_sandbox_shell(f"{prefix}{command}", timeout_seconds=timeout_seconds))

    async def run_bash_command(
        self,
        command: str,
        timeout: float = 120.0,
        run_in_background: bool = False,
    ) -> str:
        cwd = self._bash_cwd
        if run_in_background:
            log_path = os.path.join(tempfile.gettempdir(), f"yutori-n2-bash-{uuid.uuid4().hex[:8]}.log")
            with open(log_path, "wb") as log_file:
                process = subprocess.Popen(
                    ["/bin/bash", "-c", command],
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            return _format_background_task_started(log_path, process.pid)

        # The n2 bash contract: the timeout is clamped to [0, 600] and an expiry is a
        # NORMAL result the model can react to, never a raised failure envelope.
        timeout_s = _clamp_bash_timeout(timeout)
        if timeout_s == 0:
            return "Command timed out after 0s"

        # Output goes to files rather than pipes: a command that leaves a
        # descendant alive (``xcalc &``) holds a pipe open past bash's own exit,
        # so a pipe read would hang the full timeout. The status file appearing
        # is the completion signal, independent of surviving descendants.
        token = uuid.uuid4().hex
        result_prefix = os.path.join(tempfile.gettempdir(), f"yutori-n2-bash-{token}")
        stdout_path = f"{result_prefix}.stdout"
        stderr_path = f"{result_prefix}.stderr"
        status_path = f"{result_prefix}.status"
        cwd_path = f"{result_prefix}.cwd"
        status_tmp = f"{status_path}.tmp"
        wrapped = _build_cwd_tracking_bash_script(command, cwd=cwd, cwd_path=cwd_path, status_path=status_path)
        with open(stdout_path, "wb") as stdout_file, open(stderr_path, "wb") as stderr_file:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                wrapped,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )

        async def _status_exists() -> bool:
            return os.path.exists(status_path)

        try:
            if not await _wait_for_file(_status_exists, timeout_s):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                return f"Command timed out after {timeout_s:g}s"
            stdout = Path(stdout_path).read_text(encoding="utf-8", errors="replace")
            stderr = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
            status = Path(status_path).read_text(encoding="utf-8")
            new_cwd = Path(cwd_path).read_text(encoding="utf-8", errors="replace").strip()
            self._bash_cwd = new_cwd or cwd
            return _format_shell_output(_append_stream(stdout, stderr), int(status.strip()))
        finally:
            # Reap bash when it exited; a timed-out group was already killed.
            if process.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
            for path in (stdout_path, stderr_path, status_path, status_tmp, cwd_path, f"{cwd_path}.tmp"):
                with contextlib.suppress(OSError):
                    os.unlink(path)

    async def run_sandbox_shell(self, command: str, *, timeout_seconds: int) -> Any:
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-c",
            command,
            cwd=self._bash_cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            return SimpleNamespace(stdout="", stderr=f"command timed out after {timeout_seconds}s", returncode=124)
        return SimpleNamespace(
            stdout=(stdout or b"").decode("utf-8", "replace"),
            stderr=(stderr or b"").decode("utf-8", "replace"),
            returncode=process.returncode,
        )

    async def file_tool_cwd(self) -> str:
        return self._bash_cwd
