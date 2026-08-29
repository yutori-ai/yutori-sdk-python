"""Run stable Navigator n2 in a disposable public Cua sandbox."""

from __future__ import annotations

import argparse
import asyncio
import base64
import shlex
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import PurePosixPath
from typing import Any

from yutori.auth import require_api_key
from yutori.navigator import NAVIGATOR_N2_MODEL, N2ComputerAgent

try:
    from .shared import RunGuard, add_common_arguments, build_confirmation_callback, run_agent, selected_tool_set
except ImportError:
    from shared import RunGuard, add_common_arguments, build_confirmation_callback, run_agent, selected_tool_set

SHELL_RESULT_MAX_CHARS = 8_000
SHELL_RESULT_TRUNCATION_SUFFIX = "\n[result truncated]"


def _result_output(result: Any) -> str:
    """Join Cua's separate stdout and stderr streams without losing either."""
    output = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    if stderr:
        output = f"{output}{'' if not output or output.endswith(chr(10)) else chr(10)}{stderr}"
    return output


def _format_shell_output(output: str, exit_code: int) -> str:
    marker = f"[exit code {exit_code}]" if exit_code else ""
    if len(output) > SHELL_RESULT_MAX_CHARS:
        output = output[: SHELL_RESULT_MAX_CHARS - len(SHELL_RESULT_TRUNCATION_SUFFIX)] + SHELL_RESULT_TRUNCATION_SUFFIX
    if marker:
        return f"{output}{'' if not output or output.endswith(chr(10)) else chr(10)}{marker}"
    return output or "Command exited with code 0 and produced no output."


def _shell_result(result: Any) -> str:
    return _format_shell_output(
        _result_output(result),
        int(getattr(result, "returncode", 0) or 0),
    )


def _python_command(script: str, *arguments: str) -> str:
    return "python3 -c " + shlex.quote(script) + "".join(f" {shlex.quote(argument)}" for argument in arguments)


class CuaSandboxComputer:
    """N2 computer-handler adapter built only on the public cua==0.1.6 API."""

    def __init__(self, sandbox: Any) -> None:
        self.sandbox = sandbox
        self._bash_cwd: str | None = None
        self._file_snapshots: set[str] = set()
        self._pointer: tuple[int, int] | None = None
        self._left_mouse_down = False

    async def screenshot(self) -> str:
        return await self.sandbox.screenshot_base64(format="jpeg", quality=80)

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

    async def hold_key(self, key: str, ms: int = 1_000) -> None:
        await self.sandbox.keyboard.key_down(key)
        try:
            await asyncio.sleep(ms / 1_000)
        finally:
            await self.sandbox.keyboard.key_up(key)

    async def wait(self, ms: int = 1_000) -> None:
        await asyncio.sleep(ms / 1_000)

    async def left_mouse_down(self, x: int | None = None, y: int | None = None) -> None:
        if (x is None) != (y is None):
            raise ValueError("mouse_down coordinates must include both x and y")
        point = (x, y) if x is not None and y is not None else self._pointer
        if point is None:
            raise ValueError("mouse_down requires coordinates before the pointer has moved")
        self._pointer = point
        await self.sandbox.mouse.mouse_down(*point)
        self._left_mouse_down = True

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        if (x is None) != (y is None):
            raise ValueError("mouse_up coordinates must include both x and y")
        point = (x, y) if x is not None and y is not None else self._pointer
        if point is None:
            raise ValueError("mouse_up requires coordinates before the pointer has moved")
        self._pointer = point
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
                f"Started background task (pid {process_id}).\n"
                f"Output file: {log_path}\n"
                f"Cancel with: kill -- -{process_id}"
            )

        sentinel = f"__YUTORI_N2_BASH_CWD_{uuid.uuid4().hex}__"
        wrapped = f"{prefix}{command}\n__yutori_rc=$?\nprintf '\\n{sentinel}%s' \"$PWD\"\nexit $__yutori_rc"
        result = await self.sandbox.shell.run(wrapped, timeout=max(1, int(timeout) if timeout else 600))
        stdout = str(getattr(result, "stdout", "") or "")
        body, marker, new_cwd = stdout.rpartition(f"\n{sentinel}")
        if marker:
            self._bash_cwd = new_cwd.strip() or cwd
            stderr = str(getattr(result, "stderr", "") or "")
            if stderr:
                body = f"{body}{'' if not body or body.endswith(chr(10)) else chr(10)}{stderr}"
            return _format_shell_output(body, int(getattr(result, "returncode", 0) or 0))
        return _shell_result(result)

    async def read_file(self, file_path: str, offset: int = 0, limit: int = 2_000) -> str:
        cwd = await self._working_directory()
        script = (
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[1]).expanduser()\n"
            "if not path.is_absolute(): path = Path(sys.argv[2]) / path\n"
            "data = path.read_bytes()\n"
            "text = data.decode('utf-8-sig') if data.startswith(b'\\xef\\xbb\\xbf') else "
            "(data.decode('utf-16') if data.startswith((b'\\xff\\xfe', b'\\xfe\\xff')) else data.decode('utf-8'))\n"
            "offset, limit = int(sys.argv[3]), int(sys.argv[4])\n"
            "for number, line in enumerate(text.splitlines()[offset:offset + limit], offset + 1):\n"
            "    print(f'{number:6}\\t{line}')\n"
        )
        output = await self._run_python(script, file_path, cwd, str(offset), str(limit))
        self._file_snapshots.add(await self._file_key(file_path))
        return output.rstrip()

    async def write_file(self, file_path: str, content: str) -> str:
        cwd = await self._working_directory()
        encoded = base64.b64encode(content.encode()).decode()
        script = (
            "from pathlib import Path\n"
            "import base64, sys\n"
            "path = Path(sys.argv[1]).expanduser()\n"
            "if not path.is_absolute(): path = Path(sys.argv[2]) / path\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text(base64.b64decode(sys.argv[3]).decode(), encoding='utf-8')\n"
        )
        await self._run_python(script, file_path, cwd, encoded)
        self._file_snapshots.add(await self._file_key(file_path))
        return f"Wrote {len(content)} characters to {file_path}."

    async def edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        file_key = await self._file_key(file_path)
        if file_key not in self._file_snapshots:
            raise RuntimeError(f"Read or write {file_path} before editing it.")
        cwd = await self._working_directory()
        script = (
            "from pathlib import Path\n"
            "import base64, sys\n"
            "path = Path(sys.argv[1]).expanduser()\n"
            "if not path.is_absolute(): path = Path(sys.argv[2]) / path\n"
            "old = base64.b64decode(sys.argv[3]).decode()\n"
            "new = base64.b64decode(sys.argv[4]).decode()\n"
            "replace_all = sys.argv[5] == '1'\n"
            "text = path.read_text(encoding='utf-8')\n"
            "matches = text.count(old)\n"
            "if not old or not matches: raise SystemExit('Requested text was not found')\n"
            "if matches > 1 and not replace_all: raise SystemExit(f'Found {matches} matches; use replace_all')\n"
            "path.write_text(text.replace(old, new, -1 if replace_all else 1), encoding='utf-8')\n"
            "print(matches if replace_all else 1)\n"
        )
        replacements = await self._run_python(
            script,
            file_path,
            cwd,
            base64.b64encode(old_string.encode()).decode(),
            base64.b64encode(new_string.encode()).decode(),
            "1" if replace_all else "0",
        )
        return f"Edited {file_path}: replaced {replacements.strip()} occurrence(s)."

    async def _working_directory(self) -> str:
        if self._bash_cwd is None:
            result = await self.sandbox.shell.run("pwd", timeout=30)
            if int(getattr(result, "returncode", 0) or 0) != 0:
                raise RuntimeError(_shell_result(result))
            self._bash_cwd = str(getattr(result, "stdout", "") or "").strip()
        if not self._bash_cwd:
            raise RuntimeError("Sandbox shell did not report a working directory.")
        return self._bash_cwd

    async def _file_key(self, file_path: str) -> str:
        path = PurePosixPath(file_path)
        return str(path if path.is_absolute() else PurePosixPath(await self._working_directory()) / path)

    async def _run_python(self, script: str, *arguments: str) -> str:
        result = await self.sandbox.shell.run(_python_command(script, *arguments), timeout=30)
        if int(getattr(result, "returncode", 0) or 0) != 0:
            raise RuntimeError(_shell_result(result))
        return str(getattr(result, "stdout", "") or "")


async def main(args: argparse.Namespace) -> None:
    from cua import Image, Sandbox

    guard = RunGuard(args.max_steps)
    async with Sandbox.ephemeral(Image.linux()) as sandbox:
        computer = CuaSandboxComputer(sandbox)
        async with N2ComputerAgent(
            computer=computer,
            api_key=require_api_key(),
            model=NAVIGATOR_N2_MODEL,
            tool_set=selected_tool_set(args.tool_set),
            callbacks=[guard],
            action_confirmation_callback=build_confirmation_callback(args.auto_approve, always_confirm_shell=False),
            supports_click_modifiers=True,
        ) as agent:
            await run_agent(agent, args.task, guard)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        print("Interrupted.")
