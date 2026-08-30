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
import base64
import io
import json
import shlex
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from PIL import Image

BASH_RESULT_MAX_CHARS = 30_000


_FILE_TOOL_SCRIPT = r"""
from pathlib import Path
import base64
import fnmatch
import glob
import hashlib
import json
import os
import re
import struct
import sys

arguments = json.loads(base64.b64decode(sys.argv[1]).decode())
cwd = Path(arguments["cwd"])
STATE = Path("/tmp/.yutori-n2-read-fingerprints.json")
READ_MAX = 256 * 1024
GREP_MAX = 20000
MAX_COLUMNS = 500
VCS_EXCLUDES = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

def done(text):
    print(text)
    raise SystemExit(0)

def truncate(text, limit):
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... output truncated, {len(text) - limit} more chars ...]"

def resolve(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else cwd / path

def detect_encoding(head):
    if not head:
        return "utf-8"
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"

def decode_text(data):
    return data.decode(detect_encoding(data[:4096]), "replace").replace("\r\n", "\n")

def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def load_fingerprints():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}

def record_fingerprint(path, data):
    fingerprints = load_fingerprints()
    fingerprints[str(path)] = hashlib.sha256(data).hexdigest()
    STATE.write_text(json.dumps(fingerprints))

def check_read_before_edit(path, display, data):
    seen = load_fingerprints().get(str(path))
    if seen is None:
        return f"ERROR: you must read {display} before editing it (read it, then edit)."
    if seen != hashlib.sha256(data).hexdigest():
        return f"ERROR: {display} changed since you last read it - read it again before editing."
    return None

def edit_snippet(text, anchor, extra_lines):
    lines = text.split("\n")
    line_no = text[: max(anchor, 0)].count("\n") + 1
    lo = max(1, line_no - 4)
    hi = min(len(lines), line_no + 4 + extra_lines)
    return "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(lo, hi + 1))

def mtime_key(path):
    return -path.stat().st_mtime, str(path)

def search_files(root):
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    files = []
    for directory, child_directories, filenames in os.walk(root):
        child_directories[:] = [name for name in child_directories if name not in VCS_EXCLUDES]
        files.extend(Path(directory, filename) for filename in filenames)
    return files

operation = arguments["operation"]
if operation == "read":
    display = arguments["file_path"]
    path = resolve(display)
    if path.is_dir():
        done(f"ERROR: path is a directory, not a file: {display}")
    if not path.is_file():
        done(f"ERROR: file does not exist: {display}")
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        print("__YUTORI_IMAGE__")
        print(base64.b64encode(data).decode())
        raise SystemExit(0)
    if suffix == ".pdf":
        done(f"[pdf file: {path.name} - {len(data)} bytes; binary content not shown]")
    if suffix == ".ipynb":
        try:
            nb = json.loads(data.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            done(f"ERROR: {display} is not valid JSON/.ipynb: {exc}")
        parts = []
        for idx, cell in enumerate(nb.get("cells", [])):
            src = cell.get("source", "")
            if isinstance(src, list):
                src = "".join(src)
            parts.append(f"# -- Cell {idx} [{cell.get('cell_type', 'code')}] --\n{src}")
        record_fingerprint(path, data)
        done(truncate("\n\n".join(parts) if parts else "[notebook has no cells]", READ_MAX))
    record_fingerprint(path, data)
    if not data:
        done("[file exists but is empty]")
    offset, limit = arguments["offset"], arguments["limit"]
    lines = decode_text(data).split("\n")
    start = max(0, offset - 1) if offset else 0
    window = lines[start : start + max(0, limit)]
    done(truncate("\n".join(f"{start + i + 1:>6}\t{line}" for i, line in enumerate(window)), READ_MAX))
elif operation == "write":
    display = arguments["file_path"]
    path = resolve(display)
    existed = path.exists()
    write_text(path, arguments["content"])
    record_fingerprint(path, arguments["content"].encode("utf-8"))
    if existed:
        done(f"The file {display} has been updated successfully.")
    done(f"File created successfully at: {display}")
elif operation == "edit":
    display = arguments["file_path"]
    path = resolve(display)
    old, new = arguments["old_string"], arguments["new_string"]
    if old == new:
        done("ERROR: old_string and new_string are identical.")
    if old == "":
        if path.exists():
            done(
                f"ERROR: cannot create {display}: it already exists "
                "(use a non-empty old_string to edit, or write to overwrite)."
            )
        write_text(path, new)
        record_fingerprint(path, new.encode("utf-8"))
        done(f"File created successfully at: {display}")
    if not path.is_file():
        done(f"ERROR: file does not exist: {display}")
    # NOTE: the decode/match/anchor semantics below deliberately reproduce the served
    # n2 edit tool exactly — replace-mode decoding (invalid bytes become U+FFFD on
    # write-back), matching against the raw text (read output is CRLF-normalized for
    # display, the edit match is not), and anchoring the snippet on the first
    # occurrence of new_string. Reproducing them byte-for-byte is the point of this
    # reference implementation; "improving" them here would make results diverge
    # from what n2 sees elsewhere.
    data = path.read_bytes()
    stale = check_read_before_edit(path, display, data)
    if stale is not None:
        done(stale)
    text = data.decode("utf-8", "replace")
    count = text.count(old)
    if count == 0:
        done("ERROR: old_string not found in file (it must match exactly, including whitespace).")
    if count > 1 and not arguments["replace_all"]:
        done(f"ERROR: old_string is not unique ({count} occurrences). Add context or pass replace_all=true.")
    new_text = text.replace(old, new) if arguments["replace_all"] else text.replace(old, new, 1)
    write_text(path, new_text)
    record_fingerprint(path, new_text.encode("utf-8"))
    anchor = new_text.find(new) if new else text.find(old)
    done(f"The file {display} has been updated successfully:\n{edit_snippet(new_text, anchor, new.count(chr(10)))}")
elif operation == "grep":
    root = resolve(arguments["path"] or arguments["cwd"])
    flags = re.MULTILINE | (re.IGNORECASE if arguments["ignore_case"] else 0)
    if arguments["multiline"]:
        flags |= re.DOTALL
    try:
        regex = re.compile(arguments["pattern"], flags)
    except re.error as error:
        done(f"ERROR: invalid regex: {error}")
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
        if arguments["multiline"]:
            hits = [(0, text[:MAX_COLUMNS])] if regex.search(text) else []
            lines = text.splitlines() or [text]
        else:
            lines = text.splitlines()
            hits = [(index, line[:MAX_COLUMNS]) for index, line in enumerate(lines) if regex.search(line)]
        if not hits:
            continue
        files.append(path)
        counts.append(f"{path}:{len(hits)}")
        if arguments["output_mode"] != "content":
            continue
        emitted = set()
        for index, hit_line in hits:
            if arguments["multiline"]:
                prefix = f"{path}:{index + 1}:" if show_numbers else f"{path}:"
                content.append(prefix + hit_line)
                continue
            for line_index in range(max(0, index - before), min(len(lines), index + after + 1)):
                if line_index not in emitted:
                    emitted.add(line_index)
                    prefix = f"{path}:{line_index + 1}:" if show_numbers else f"{path}:"
                    content.append(prefix + lines[line_index][:MAX_COLUMNS])
    if arguments["output_mode"] == "files_with_matches":
        output = sorted(map(str, files), key=lambda value: mtime_key(Path(value)))
    elif arguments["output_mode"] == "count":
        output = counts
    else:
        output = content
    limit = arguments["head_limit"]
    done(truncate("\n".join(output if limit in (None, 0) else output[:limit]), GREP_MAX))
elif operation == "glob":
    root = resolve(arguments["path"] or arguments["cwd"])
    pattern = arguments["pattern"]
    search_pattern = pattern if Path(pattern).is_absolute() else str(root / pattern)
    matches = [Path(value) for value in glob.glob(search_pattern, recursive=True) if Path(value).exists()]
    matches.sort(key=mtime_key)
    output = [str(path) for path in matches[:100]]
    if len(matches) > 100:
        output.append(f"[... truncated to first 100 of {len(matches)} matches ...]")
    done("\n".join(output))
else:
    raise SystemExit(f"Unknown file operation: {operation}")
"""


def _append_stream(base: str, addition: str) -> str:
    """Append a second output stream to the first, inserting a newline only where one is missing."""
    if not addition:
        return base
    return f"{base}{'' if not base or base.endswith(chr(10)) else chr(10)}{addition}"


def _result_output(result: Any) -> str:
    """Join Cua's separate stdout and stderr streams without losing either."""
    return _append_stream(str(getattr(result, "stdout", "") or ""), str(getattr(result, "stderr", "") or ""))


def _truncate(text: str, max_chars: int = BASH_RESULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... output truncated, {len(text) - max_chars} more chars ...]"


def _format_shell_output(output: str, exit_code: int) -> str:
    """Render a bash result the way n2 expects: output, with an exit-code header on failure."""
    output = _truncate(output)
    if exit_code:
        return f"Exit code {exit_code}\n{output}" if output else f"Exit code {exit_code}"
    return output or "(Bash completed with no output)"


def _shell_result(result: Any) -> str:
    return _format_shell_output(
        _result_output(result),
        int(getattr(result, "returncode", 0) or 0),
    )


_IMAGE_VIEW_MAX_EDGE = 1568


def _render_image_result(file_path: str, data: bytes) -> "dict[str, str] | str":
    """Return an image read as visible image content: a note with the source
    dimensions plus the image itself, downscaled to a 1568-px max edge and
    WEBP-encoded — the same bounds the loop applies to screenshots."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        width, height = image.size
        scale = min(1.0, _IMAGE_VIEW_MAX_EDGE / max(width, height)) if max(width, height) else 1.0
        shown = image.resize((max(1, int(width * scale)), max(1, int(height * scale)))) if scale < 1.0 else image
        if shown.mode not in ("RGB", "RGBA", "L"):
            shown = shown.convert("RGB")
        buffer = io.BytesIO()
        shown.save(buffer, format="WEBP", quality=90)
    except Exception as error:  # noqa: BLE001 - not a decodable image
        return f"ERROR: {file_path} is not a readable image: {error}"
    note = f"Loaded image {file_path} ({width}x{height})" + (
        f", shown downscaled to {shown.size[0]}x{shown.size[1]}" if shown.size != (width, height) else ""
    )
    return {"text": note, "image_url": "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode()}


def _python_command(script: str, *arguments: str) -> str:
    return "python3 -c " + shlex.quote(script) + "".join(f" {shlex.quote(argument)}" for argument in arguments)


class CuaSandboxComputer:
    """N2 computer-handler adapter built only on the public cua==0.1.6 API."""

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

    async def read_file(self, file_path: str, offset: int = 1, limit: int = 2_000) -> "str | dict[str, str]":
        if offset < 1:
            raise ValueError("read.offset must be a positive 1-based line number")
        output = await self._run_file_tool("read", file_path=file_path, offset=offset, limit=limit)
        if output.startswith("__YUTORI_IMAGE__"):
            _, _, encoded = output.partition("\n")
            return _render_image_result(file_path, base64.b64decode(encoded.strip()))
        return output.rstrip("\n")

    async def write_file(self, file_path: str, content: str) -> str:
        return (await self._run_file_tool("write", file_path=file_path, content=content)).rstrip("\n")

    async def edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        return (
            await self._run_file_tool(
                "edit",
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
        ).rstrip("\n")

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
        return output.rstrip("\n") or "No matches found."

    async def glob_files(self, pattern: str, path: str | None = None) -> str:
        output = await self._run_file_tool("glob", pattern=pattern, path=path)
        return output.rstrip("\n") or "No files found."

    async def _working_directory(self) -> str:
        if self._bash_cwd is None:
            result = await self.sandbox.shell.run("pwd", timeout=30)
            if int(getattr(result, "returncode", 0) or 0) != 0:
                raise RuntimeError(_shell_result(result))
            self._bash_cwd = str(getattr(result, "stdout", "") or "").strip()
        if not self._bash_cwd:
            raise RuntimeError("Sandbox shell did not report a working directory.")
        return self._bash_cwd

    async def _run_file_tool(self, operation: str, **arguments: Any) -> str:
        payload = {"operation": operation, "cwd": await self._working_directory(), **arguments}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        # Search tools get longer budgets (120s grep / 60s glob); plain file I/O stays short.
        timeout = {"grep": 120, "glob": 60}.get(operation, 30)
        result = await self.sandbox.shell.run(_python_command(_FILE_TOOL_SCRIPT, encoded), timeout=timeout)
        if int(getattr(result, "returncode", 0) or 0) != 0:
            # n2 expects unexpected failures as a plain ``ERROR: ...``
            # tool result the model can react to, never a raised failure envelope.
            detail = str(getattr(result, "stderr", "") or "").strip().splitlines()
            reason = detail[-1] if detail else f"{operation} failed with exit code {getattr(result, 'returncode', '?')}"
            return f"ERROR: {reason}"
        return str(getattr(result, "stdout", "") or "")
