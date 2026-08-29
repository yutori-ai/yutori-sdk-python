"""Run stable Navigator n2 in a disposable public Cua sandbox."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
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

_GREP_SCRIPT = r"""
from pathlib import Path
import base64
import fnmatch
import json
import os
import re
import sys

arguments = json.loads(base64.b64decode(sys.argv[2]).decode())
root = Path(arguments["path"]).expanduser()
if not root.is_absolute():
    root = Path(sys.argv[1]) / root
flags = re.MULTILINE | (re.IGNORECASE if arguments["ignore_case"] else 0)
if arguments["multiline"]:
    flags |= re.DOTALL
try:
    regex = re.compile(arguments["pattern"], flags)
except re.error as error:
    raise SystemExit(f"invalid regex: {error}")

def files_to_search():
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    files = []
    for directory, child_directories, filenames in os.walk(root):
        child_directories[:] = [name for name in child_directories if name not in {".git", ".hg", ".svn"}]
        files.extend(Path(directory, filename) for filename in filenames)
    return files

def include(file_path):
    file_type = arguments["file_type"]
    if file_type and file_path.suffix != "." + file_type.lstrip("."):
        return False
    glob_pattern = arguments["glob"]
    if not glob_pattern:
        return True
    relative_root = root if root.is_dir() else root.parent
    try:
        relative = file_path.relative_to(relative_root)
    except ValueError:
        relative = file_path
    return fnmatch.fnmatch(str(relative), glob_pattern) or fnmatch.fnmatch(file_path.name, glob_pattern)

files_with_matches = []
counts = []
content = []
show_numbers = (
    arguments["output_mode"] == "content"
    if arguments["show_line_numbers"] is None
    else arguments["show_line_numbers"]
)
before = arguments["context"] if arguments["context"] is not None else arguments["before_context"] or 0
after = arguments["context"] if arguments["context"] is not None else arguments["after_context"] or 0
for file_path in files_to_search():
    if not include(file_path):
        continue
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    lines = text.splitlines() or [text]
    matches = [index for index, line in enumerate(lines) if regex.search(line)]
    if arguments["multiline"] and regex.search(text):
        matches = [0]
    if not matches:
        continue
    files_with_matches.append(file_path)
    counts.append(f"{file_path}:{len(matches)}")
    if arguments["output_mode"] != "content":
        continue
    emitted = set()
    for index in matches:
        for line_index in range(max(0, index - before), min(len(lines), index + after + 1)):
            if line_index in emitted:
                continue
            emitted.add(line_index)
            prefix = f"{file_path}:{line_index + 1}:" if show_numbers else f"{file_path}:"
            content.append(prefix + lines[line_index])

if arguments["output_mode"] == "files_with_matches":
    output = [str(path) for path in sorted(files_with_matches, key=lambda path: (-path.stat().st_mtime, str(path)))]
elif arguments["output_mode"] == "count":
    output = counts
else:
    output = content
limit = arguments["head_limit"]
if limit not in (None, 0):
    output = output[:limit]
print("\n".join(output))
"""

_GLOB_SCRIPT = r"""
from pathlib import Path
import base64
import glob
import json
import sys

arguments = json.loads(base64.b64decode(sys.argv[2]).decode())
root = Path(arguments["path"]).expanduser()
if not root.is_absolute():
    root = Path(sys.argv[1]) / root
pattern = arguments["pattern"]
search_pattern = pattern if Path(pattern).is_absolute() else str(root / pattern)
matches = [Path(match) for match in glob.glob(search_pattern, recursive=True) if Path(match).exists()]
matches.sort(key=lambda path: (-path.stat().st_mtime, str(path)))
output = [str(path) for path in matches[:100]]
if len(matches) > 100:
    output.append(f"[... truncated to first 100 of {len(matches)} matches ...]")
print("\n".join(output))
"""


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

    async def read_file(self, file_path: str, offset: int = 1, limit: int = 2_000) -> str:
        if offset < 1:
            raise ValueError("read.offset must be a positive 1-based line number")
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
            "for number, line in enumerate(text.splitlines()[offset - 1:offset - 1 + limit], offset):\n"
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
        cwd = await self._working_directory()
        if old_string == "":
            script = (
                "from pathlib import Path\n"
                "import base64, sys\n"
                "path = Path(sys.argv[1]).expanduser()\n"
                "if not path.is_absolute(): path = Path(sys.argv[2]) / path\n"
                "if path.exists(): raise SystemExit('File already exists; use a non-empty old_string to edit it')\n"
                "path.parent.mkdir(parents=True, exist_ok=True)\n"
                "path.write_text(base64.b64decode(sys.argv[3]).decode(), encoding='utf-8')\n"
            )
            await self._run_python(script, file_path, cwd, base64.b64encode(new_string.encode()).decode())
            self._file_snapshots.add(file_key)
            return f"File created successfully at: {file_path}"
        if file_key not in self._file_snapshots:
            raise RuntimeError(f"Read or write {file_path} before editing it.")
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
            "if not matches: raise SystemExit('Requested text was not found')\n"
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

    async def grep_files(
        self,
        pattern: str,
        path: str | None = None,
        glob_pattern: str | None = None,
        file_type: str | None = None,
        output_mode: str = "files_with_matches",
        ignore_case: bool = False,
        show_line_numbers: bool | None = None,
        before_context: int | None = None,
        after_context: int | None = None,
        context: int | None = None,
        head_limit: int | None = 250,
        multiline: bool = False,
    ) -> str:
        cwd = await self._working_directory()
        arguments = base64.b64encode(
            json.dumps(
                {
                    "pattern": pattern,
                    "path": path or cwd,
                    "glob": glob_pattern,
                    "file_type": file_type,
                    "output_mode": output_mode,
                    "ignore_case": ignore_case,
                    "show_line_numbers": show_line_numbers,
                    "before_context": before_context,
                    "after_context": after_context,
                    "context": context,
                    "head_limit": head_limit,
                    "multiline": multiline,
                }
            ).encode()
        ).decode()
        script = _GREP_SCRIPT
        output = await self._run_python(script, cwd, arguments)
        return output.rstrip() or "No matches found."

    async def glob_files(self, pattern: str, path: str | None = None) -> str:
        cwd = await self._working_directory()
        arguments = base64.b64encode(json.dumps({"pattern": pattern, "path": path or cwd}).encode()).decode()
        output = await self._run_python(_GLOB_SCRIPT, cwd, arguments)
        return output.rstrip() or "No files found."

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
