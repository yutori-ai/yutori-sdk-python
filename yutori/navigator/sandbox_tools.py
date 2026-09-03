"""Reference n2 file-tool and shell-result implementations for sandbox adapters.

The n2 loop serves every tool in its pinned set: an adapter that pins a set
with the file tools (``read``/``write``/``edit``/``grep``/``glob``) must
implement all of them, with the exact result strings the model relies on. This module
ships that implementation for any sandbox that can run a shell command:
``FILE_TOOL_SCRIPT`` executes inside the sandbox (python3 stdlib only), and
:class:`ShellFileToolsMixin` provides the handler methods over two small
hooks the adapter supplies. The shell-result helpers render ``bash`` results
the same way (``Exit code N`` headers, truncation caps).

Ownership note: the n2 loop implements ``computer_batch`` itself but only
routes shell/file handler text (a trim and a 256K backstop aside) -- these
output contracts are the adapter's to honor. When building (or pointing a
coding agent at) a custom adapter, the expected formats live in
``FILE_TOOL_SCRIPT`` below (``cat -n`` numbering, the sha256 read-before-edit
gate, truncation markers), ``format_shell_output``, and
``render_image_result``; ``examples/navigator_n2/cua_adapter.py`` and
``examples/navigator_n2/direct_x11_adapter.py`` add the ``bash`` timeout and
background-run forms and show the full wiring (across a sandbox API and
through direct X11 access, respectively).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import shlex
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from PIL import Image

BASH_RESULT_MAX_CHARS = 30_000

FILE_TOOL_SCRIPT = r"""
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
        record_fingerprint(path, data)
        print("__YUTORI_IMAGE__")
        print(base64.b64encode(data).decode())
        raise SystemExit(0)
    if suffix == ".pdf":
        record_fingerprint(path, data)
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
    # n2 edit tool exactly -- replace-mode decoding (invalid bytes become U+FFFD on
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


# The shell-result contract ``run_sandbox_shell`` returns: an object exposing
# ``stdout``, ``stderr``, and ``returncode``. It is duck-typed because every sandbox
# SDK spells its own result type differently, so read it only through these three
# accessors -- they are the one place the tolerated shapes (attribute absent, or
# present but ``None``) are decided.
def result_stdout(result: Any) -> str:
    """Read a shell result's ``stdout``, treating a missing or ``None`` field as empty."""
    return str(getattr(result, "stdout", "") or "")


def result_stderr(result: Any) -> str:
    """Read a shell result's ``stderr``, treating a missing or ``None`` field as empty."""
    return str(getattr(result, "stderr", "") or "")


def result_returncode(result: Any) -> int:
    """Read a shell result's ``returncode``, treating a missing or ``None`` field as 0 (success)."""
    return int(getattr(result, "returncode", 0) or 0)


def append_stream(base: str, addition: str) -> str:
    """Append a second output stream to the first, inserting a newline only where one is missing."""
    if not addition:
        return base
    return f"{base}{'' if not base or base.endswith(chr(10)) else chr(10)}{addition}"


def join_output_streams(result: Any) -> str:
    """Join Cua's separate stdout and stderr streams without losing either."""
    return append_stream(result_stdout(result), result_stderr(result))


def truncate_tool_output(text: str, max_chars: int = BASH_RESULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... output truncated, {len(text) - max_chars} more chars ...]"


def format_shell_output(output: str, exit_code: int) -> str:
    """Render a bash result the way n2 expects: output, with an exit-code header on failure."""
    output = truncate_tool_output(output)
    if exit_code:
        return f"Exit code {exit_code}\n{output}" if output else f"Exit code {exit_code}"
    return output or "(Bash completed with no output)"


def format_shell_result(result: Any) -> str:
    return format_shell_output(join_output_streams(result), result_returncode(result))


def format_background_task_started(log_path: str, process_id: Any) -> str:
    """Render the n2 ``run_bash_command(run_in_background=True)`` acknowledgment.

    ``log_path`` is expected to end in ``.log``; the task id shown to the model is
    the 8 characters before that suffix (the random hex the caller generated it from).
    """
    return (
        f"Started background task `bash_{log_path[-12:-4]}`.\n"
        f"stdout+stderr is streaming to: {log_path}\n"
        "Use the read tool on that file to retrieve output.\n"
        f"Process id: {process_id}\n"
        f"To cancel: run bash with `kill {process_id}`"
    )


async def wait_for_file(exists: Callable[[], Awaitable[bool]], timeout_seconds: float) -> bool:
    """Poll ``exists`` with capped exponential backoff until it returns true or
    ``timeout_seconds`` elapses.

    This is the n2 ``bash`` contract's atomic-status-file wait: the adapter's
    generated script writes a status file only once the command has fully
    exited, so polling for that file's existence is the completion signal,
    independent of any backgrounded descendant still running. Delay starts at
    10ms and doubles up to a 250ms cap. Returns ``False`` on timeout rather
    than raising, so the caller can turn it into a normal (non-exceptional)
    "Command timed out" result.
    """

    async def poll() -> None:
        delay = 0.01
        while not await exists():
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.25)

    try:
        await asyncio.wait_for(poll(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    return True


BASH_TIMEOUT_DEFAULT_SECONDS = 120.0
BASH_TIMEOUT_MAX_SECONDS = 600.0


def clamp_bash_timeout(timeout: float | None) -> float:
    """Clamp a `bash` timeout into the n2 contract's [0, 600] range, defaulting a missing value to 120s."""
    return max(0.0, min(float(BASH_TIMEOUT_DEFAULT_SECONDS if timeout is None else timeout), BASH_TIMEOUT_MAX_SECONDS))


def clamp_bash_timeout_or_expired(timeout: float | None) -> tuple[float, str | None]:
    """Clamp a `bash` timeout and flag an immediate expiry.

    The n2 `bash` contract: the timeout is clamped to [0, 600], and a clamped value of 0 is
    itself an expiry -- a NORMAL result the model can react to, never a raised failure
    envelope. Returns ``(timeout_seconds, message)``: when ``message`` is not ``None`` the
    caller should return it immediately without running the command; otherwise proceed using
    ``timeout_seconds``.
    """
    timeout_s = clamp_bash_timeout(timeout)
    if timeout_s == 0:
        return timeout_s, "Command timed out after 0s"
    return timeout_s, None


def build_cwd_tracking_bash_script(command: str, *, cwd: str, cwd_path: str, status_path: str) -> str:
    """Build the n2 `bash` wrapper script: `cd` into ``cwd``, run ``command``, and record its
    resulting working directory and exit status for the caller to read back afterward.

    The exit status is only meaningful once ``status_path`` exists, so both it and the recorded
    cwd are written to a ``.tmp`` sibling and moved into place, never written in place: a status
    poller must never observe a partially written file. The cwd capture runs in an inner shell
    with its own ``trap ... 0`` so it fires even if ``command`` exits early, backgrounds a
    descendant, or is killed by a signal -- the outer ``if`` only backstops the case where the cd
    itself failed and the inner shell never ran.

    Returns the script body only; the caller supplies its own output redirection, either embedded
    in the string (wrapping it in ``(...)`` with a trailing `` < /dev/null > out 2> err`` to hand
    one string to a remote shell) or via subprocess stdout/stderr handles.
    """
    status_tmp = f"{status_path}.tmp"
    cwd_tmp = f"{cwd_path}.tmp"
    finish = f"__yutori_finish_{uuid.uuid4().hex}"
    inner = (
        f"{finish}() {{\n"
        f"  printf '%s\\n' \"$PWD\" > {shlex.quote(cwd_tmp)}\n"
        f"  mv {shlex.quote(cwd_tmp)} {shlex.quote(cwd_path)}\n"
        "}\n"
        f"trap {finish} 0\n"
        f"{command}"
    )
    return (
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
    )


class BashResultPaths(NamedTuple):
    """File paths for one `run_bash_command` invocation's result-file handoff.

    Built from a random token under ``base_dir`` so concurrent bash calls never
    collide; ``status_tmp``/``cwd_tmp`` are the write-then-`mv` staging paths
    `build_cwd_tracking_bash_script` targets before its atomic rename.
    """

    stdout: str
    stderr: str
    status: str
    cwd: str
    status_tmp: str
    cwd_tmp: str

    def cleanup_paths(self) -> tuple[str, str, str, str, str, str]:
        """All six paths, in the order callers remove them once a result is read."""
        return (self.stdout, self.stderr, self.status, self.status_tmp, self.cwd, self.cwd_tmp)


def build_bash_result_paths(base_dir: str) -> BashResultPaths:
    """Allocate a fresh, collision-free set of `run_bash_command` result-file paths under ``base_dir``."""
    prefix = os.path.join(base_dir, f"yutori-n2-bash-{uuid.uuid4().hex}")
    status_path = f"{prefix}.status"
    cwd_path = f"{prefix}.cwd"
    return BashResultPaths(
        stdout=f"{prefix}.stdout",
        stderr=f"{prefix}.stderr",
        status=status_path,
        cwd=cwd_path,
        status_tmp=f"{status_path}.tmp",
        cwd_tmp=f"{cwd_path}.tmp",
    )


def background_bash_log_path(base_dir: str) -> str:
    """Allocate a fresh `run_bash_command(run_in_background=True)` log path under ``base_dir``."""
    return os.path.join(base_dir, f"yutori-n2-bash-{uuid.uuid4().hex[:8]}.log")


async def scroll_notches_from_pixels(
    scroll_x: int,
    scroll_y: int,
    model_action: "dict[str, Any] | None",
    get_dimensions: Callable[[], Awaitable[tuple[int, int]]],
) -> tuple[int, int]:
    """Convert the loop's pixel scroll deltas into wheel notches.

    The loop hands adapters pixel deltas (``round(amount * 0.1 * dimension)``,
    positive = down/right), but wheel APIs (Cua's mouse, pyautogui, ...) take
    notches with the opposite sign convention (positive = up/right). The
    model's own tool call carries the exact notch count via ``direction``/
    ``amount`` in ``model_action``, so prefer that; otherwise recover the
    notch count by inverting the loop's pixel translation, calling
    ``get_dimensions`` only in that fallback path.
    """
    action = model_action or {}
    direction, amount = action.get("direction"), action.get("amount")
    if direction in ("up", "down", "left", "right") and type(amount) is int and amount > 0:
        if direction in ("up", "down"):
            return 0, amount if direction == "up" else -amount
        return (amount if direction == "right" else -amount), 0
    width, height = await get_dimensions()
    if scroll_y:
        notches = max(1, round(abs(scroll_y) / (0.1 * height)))
        return 0, (-notches if scroll_y > 0 else notches)
    if scroll_x:
        notches = max(1, round(abs(scroll_x) / (0.1 * width)))
        return (notches if scroll_x > 0 else -notches), 0
    return 0, 0


IMAGE_VIEW_MAX_EDGE = 1568


def render_image_result(file_path: str, data: bytes) -> "dict[str, str] | str":
    """Return an image read as visible image content: a note with the source
    dimensions plus the image itself, downscaled to a 1568-px max edge and
    WEBP-encoded -- the same bounds the loop applies to screenshots."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        width, height = image.size
        scale = min(1.0, IMAGE_VIEW_MAX_EDGE / max(width, height)) if max(width, height) else 1.0
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


def python_file_tool_command(script: str, *arguments: str) -> str:
    return "python3 -c " + shlex.quote(script) + "".join(f" {shlex.quote(argument)}" for argument in arguments)


class PointerKeyLifecycleMixin:
    """Shared ``hold_key``/``wait``/``release_held_mouse_button`` n2 tool handlers.

    Mix into a computer adapter that already implements ``key_down``, ``key_up``,
    and ``left_mouse_up``, and tracks a ``_left_mouse_down`` bool set by its own
    ``left_mouse_down``/``left_mouse_up``.
    """

    async def key_down(self, key: str) -> None:
        raise NotImplementedError

    async def key_up(self, key: str) -> None:
        raise NotImplementedError

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        raise NotImplementedError

    _left_mouse_down: bool = False

    async def hold_key(self, key: str, ms: int = 1_000) -> None:
        await self.key_down(key)
        try:
            await asyncio.sleep(ms / 1_000)
        finally:
            await self.key_up(key)

    async def wait(self, ms: int = 1_000) -> None:
        await asyncio.sleep(ms / 1_000)

    async def release_held_mouse_button(self) -> None:
        if self._left_mouse_down:
            await self.left_mouse_up()


class ShellFileToolsMixin:
    """The five n2 file-tool handlers over any sandbox shell.

    Mix into a computer adapter and implement two hooks:

    - ``async def run_sandbox_shell(self, command: str, *, timeout_seconds: int)``
      -- run one shell command in the sandbox and return an object with
      ``stdout``, ``stderr``, and ``returncode`` attributes.
    - ``async def file_tool_cwd(self) -> str`` -- the working directory that
      relative paths resolve against (usually the bash tool's tracked cwd).

    The sandbox needs ``python3`` (stdlib only) on PATH.
    """

    async def run_sandbox_shell(self, command: str, *, timeout_seconds: int) -> Any:
        raise NotImplementedError

    async def file_tool_cwd(self) -> str:
        raise NotImplementedError

    async def read_file(self, file_path: str, offset: int = 1, limit: int = 2_000) -> "str | dict[str, str]":
        if offset < 1:
            raise ValueError("read.offset must be a positive 1-based line number")
        output = await self._run_file_tool("read", file_path=file_path, offset=offset, limit=limit)
        if output.startswith("__YUTORI_IMAGE__"):
            _, _, encoded = output.partition("\n")
            return render_image_result(file_path, base64.b64decode(encoded.strip()))
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

    async def _run_file_tool(self, operation: str, **arguments: Any) -> str:
        payload = {"operation": operation, "cwd": await self.file_tool_cwd(), **arguments}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        # Search tools get longer budgets (120s grep / 60s glob); plain file I/O stays short.
        timeout = {"grep": 120, "glob": 60}.get(operation, 30)
        result = await self.run_sandbox_shell(
            python_file_tool_command(FILE_TOOL_SCRIPT, encoded), timeout_seconds=timeout
        )
        returncode = result_returncode(result)
        if returncode != 0:
            # n2 expects unexpected failures as a plain ``ERROR: ...``
            # tool result the model can react to, never a raised failure envelope.
            detail = result_stderr(result).strip().splitlines()
            reason = detail[-1] if detail else f"{operation} failed with exit code {returncode}"
            return f"ERROR: {reason}"
        return result_stdout(result)


__all__ = [
    "BASH_RESULT_MAX_CHARS",
    "BASH_TIMEOUT_DEFAULT_SECONDS",
    "BASH_TIMEOUT_MAX_SECONDS",
    "BashResultPaths",
    "FILE_TOOL_SCRIPT",
    "IMAGE_VIEW_MAX_EDGE",
    "ShellFileToolsMixin",
    "append_stream",
    "background_bash_log_path",
    "build_bash_result_paths",
    "build_cwd_tracking_bash_script",
    "clamp_bash_timeout",
    "clamp_bash_timeout_or_expired",
    "format_background_task_started",
    "format_shell_output",
    "format_shell_result",
    "join_output_streams",
    "python_file_tool_command",
    "render_image_result",
    "result_returncode",
    "result_stderr",
    "result_stdout",
    "scroll_notches_from_pixels",
    "truncate_tool_output",
    "wait_for_file",
]
