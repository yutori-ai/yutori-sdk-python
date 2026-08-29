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

_FILE_TOOL_SCRIPT = r"""
from pathlib import Path
import base64
import fnmatch
import glob
import json
import os
import re
import sys

arguments = json.loads(base64.b64decode(sys.argv[1]).decode())
cwd = Path(arguments["cwd"])

def resolve(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else cwd / path

def read_text(path):
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8")

def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def mtime_key(path):
    return -path.stat().st_mtime, str(path)

def search_files(root):
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    files = []
    for directory, child_directories, filenames in os.walk(root):
        child_directories[:] = [name for name in child_directories if name not in {".git", ".hg", ".svn"}]
        files.extend(Path(directory, filename) for filename in filenames)
    return files

operation = arguments["operation"]
if operation == "read":
    path = resolve(arguments["file_path"])
    offset, limit = arguments["offset"], arguments["limit"]
    for number, line in enumerate(read_text(path).splitlines()[offset - 1 : offset - 1 + limit], offset):
        print(f"{number:6}\t{line}")
elif operation == "write":
    write_text(resolve(arguments["file_path"]), arguments["content"])
elif operation == "edit":
    path = resolve(arguments["file_path"])
    old, new = arguments["old_string"], arguments["new_string"]
    if not old:
        if path.exists():
            raise SystemExit("File already exists; use a non-empty old_string to edit it")
        write_text(path, new)
        print("created")
    else:
        text = path.read_text(encoding="utf-8")
        matches = text.count(old)
        if not matches:
            raise SystemExit("Requested text was not found")
        if matches > 1 and not arguments["replace_all"]:
            raise SystemExit(f"Found {matches} matches; use replace_all")
        write_text(path, text.replace(old, new, -1 if arguments["replace_all"] else 1))
        print(matches if arguments["replace_all"] else 1)
elif operation == "grep":
    root = resolve(arguments["path"] or arguments["cwd"])
    flags = re.MULTILINE | (re.IGNORECASE if arguments["ignore_case"] else 0)
    if arguments["multiline"]:
        flags |= re.DOTALL
    try:
        regex = re.compile(arguments["pattern"], flags)
    except re.error as error:
        raise SystemExit(f"invalid regex: {error}")
    files, counts, content = [], [], []
    show_numbers = (
        arguments["output_mode"] == "content"
        if arguments["show_line_numbers"] is None
        else arguments["show_line_numbers"]
    )
    before = arguments["context"] if arguments["context"] is not None else arguments["before_context"] or 0
    after = arguments["context"] if arguments["context"] is not None else arguments["after_context"] or 0
    for path in search_files(root):
        file_type, pattern = arguments["file_type"], arguments["glob"]
        if file_type and path.suffix != "." + file_type.lstrip("."):
            continue
        relative_root = root if root.is_dir() else root.parent
        try:
            relative = path.relative_to(relative_root)
        except ValueError:
            relative = path
        if pattern and not (fnmatch.fnmatch(str(relative), pattern) or fnmatch.fnmatch(path.name, pattern)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        lines = text.splitlines() or [text]
        matches = [index for index, line in enumerate(lines) if regex.search(line)]
        if arguments["multiline"] and regex.search(text):
            matches = [0]
        if not matches:
            continue
        files.append(path)
        counts.append(f"{path}:{len(matches)}")
        if arguments["output_mode"] != "content":
            continue
        emitted = set()
        for index in matches:
            for line_index in range(max(0, index - before), min(len(lines), index + after + 1)):
                if line_index not in emitted:
                    emitted.add(line_index)
                    prefix = f"{path}:{line_index + 1}:" if show_numbers else f"{path}:"
                    content.append(prefix + lines[line_index])
    if arguments["output_mode"] == "files_with_matches":
        output = sorted(map(str, files), key=lambda value: mtime_key(Path(value)))
    elif arguments["output_mode"] == "count":
        output = counts
    else:
        output = content
    limit = arguments["head_limit"]
    print("\n".join(output if limit in (None, 0) else output[:limit]))
elif operation == "glob":
    root = resolve(arguments["path"] or arguments["cwd"])
    pattern = arguments["pattern"]
    search_pattern = pattern if Path(pattern).is_absolute() else str(root / pattern)
    matches = [Path(value) for value in glob.glob(search_pattern, recursive=True) if Path(value).exists()]
    matches.sort(key=mtime_key)
    output = [str(path) for path in matches[:100]]
    if len(matches) > 100:
        output.append(f"[... truncated to first 100 of {len(matches)} matches ...]")
    print("\n".join(output))
else:
    raise SystemExit(f"Unknown file operation: {operation}")
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
    content_limit = SHELL_RESULT_MAX_CHARS - len(marker) - 1 if marker else SHELL_RESULT_MAX_CHARS
    if len(output) > content_limit:
        output = output[: content_limit - len(SHELL_RESULT_TRUNCATION_SUFFIX)] + SHELL_RESULT_TRUNCATION_SUFFIX
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
        output = await self._run_file_tool("read", file_path=file_path, offset=offset, limit=limit)
        self._file_snapshots.add(await self._file_key(file_path))
        return output.rstrip()

    async def write_file(self, file_path: str, content: str) -> str:
        await self._run_file_tool("write", file_path=file_path, content=content)
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
        if old_string and file_key not in self._file_snapshots:
            raise RuntimeError(f"Read or write {file_path} before editing it.")
        replacements = await self._run_file_tool(
            "edit",
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
        if old_string == "":
            self._file_snapshots.add(file_key)
            return f"File created successfully at: {file_path}"
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
        output = await self._run_file_tool(
            "grep",
            pattern=pattern,
            path=path,
            glob=glob_pattern,
            file_type=file_type,
            output_mode=output_mode,
            ignore_case=ignore_case,
            show_line_numbers=show_line_numbers,
            before_context=before_context,
            after_context=after_context,
            context=context,
            head_limit=head_limit,
            multiline=multiline,
        )
        return output.rstrip() or "No matches found."

    async def glob_files(self, pattern: str, path: str | None = None) -> str:
        output = await self._run_file_tool("glob", pattern=pattern, path=path)
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

    async def _run_file_tool(self, operation: str, **arguments: Any) -> str:
        payload = {"operation": operation, "cwd": await self._working_directory(), **arguments}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        return await self._run_python(_FILE_TOOL_SCRIPT, encoded)

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
