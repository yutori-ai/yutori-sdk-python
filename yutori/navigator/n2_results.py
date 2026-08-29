"""Text renderers for Navigator n2 tool results, matching the evaluation harness.

The n2 checkpoints were trained and evaluated against one specific rendering of
every tool result: a ``computer_batch`` reports one ``[i:name]`` line per member,
``bash`` returns its combined output with an ``Exit code N`` header on failure,
``read`` returns ``cat -n`` lines, and so on. These helpers reproduce those
strings exactly so an adapter can feed the model the observation stream it was
measured under. They are pure functions: adapters call them with raw output and
put the returned text on the wire.
"""

from __future__ import annotations

import re
from typing import Any, Optional

BASH_MAX_OUTPUT_CHARS = 30_000
READ_MAX_OUTPUT_CHARS = 256 * 1024
READ_DEFAULT_LIMIT = 2_000
EDIT_SNIPPET_CONTEXT_LINES = 4

BATCH_SCREENSHOT_MEMBER_TEXT = "screenshot queued (delivered after the batch)"
BATCH_EMPTY_TEXT = "(empty batch)"
BASH_NO_OUTPUT_TEXT = "(Bash completed with no output)"
READ_EMPTY_FILE_TEXT = "[file exists but is empty]"
ACTION_EXECUTED_TEXT = "Action executed."

# The clauses of the evaluation system prompt that describe trained behaviour —
# ask without tools, end with the terminal markers — without the benchmark's
# machine-specific lines (its Ubuntu pin and sudo password).
N2_TASK_GUIDELINES = (
    "# Task Guidelines\n"
    "If you find the task needs additional information to be properly completed or you need to ask clarifying "
    "questions to the user, ask a question in your output without calling any tools in order to prompt the user "
    "to provide feedback. Do this only if user input is necessary.\n"
    "If the task is genuinely impossible — missing apps or features, insufficient permissions, contradictory "
    "requirements — end with `[INFEASIBLE]`; don't claim success you didn't achieve.\n"
    "If you're done with the task, stop calling tools and give a short summary of what you did and found, "
    "and end with `[DONE]`."
)


_TRUNCATION_MARKER = re.compile(r"\[\.\.\. output truncated, \d+ more chars \.\.\.\]\s*$")


def truncate_output(text: str, max_chars: int) -> str:
    """Cap ``text`` with the harness's ``[... output truncated, N more chars ...]`` marker.

    Text that already ends with the marker (an adapter capped it at the source)
    is returned unchanged rather than cut a second time.
    """
    if len(text) <= max_chars or _TRUNCATION_MARKER.search(text):
        return text
    return f"{text[:max_chars]}\n\n[... output truncated, {len(text) - max_chars} more chars ...]"


def render_tool_output(result: Any, *, max_chars: int) -> str:
    """Turn an adapter's shell/file return value into the text the model sees.

    Adapters return either the finished text, or — for ``bash`` — a dict
    ``{"output", "exit_code", "timed_out", "timeout"}`` that is rendered with
    :func:`format_bash_result`. Either way the text is capped at ``max_chars``.
    """
    if isinstance(result, dict) and "output" in result:
        return format_bash_result(
            str(result.get("output") or ""),
            result.get("exit_code"),
            timed_out=bool(result.get("timed_out")),
            timeout_seconds=result.get("timeout"),
            max_chars=max_chars,
        )
    return truncate_output("" if result is None else str(result), max_chars)


def format_action_error(error: BaseException) -> str:
    """The text a failed batch member contributes: ``ERROR: <Type>: <message>``."""
    return f"ERROR: {type(error).__name__}: {error}"


def format_batch_result(
    member_names: list[str],
    outcomes: list[Optional[str]],
    *,
    error_index: Optional[int] = None,
    error_text: Optional[str] = None,
) -> str:
    """Render a ``computer_batch`` result the way the evaluation harness does.

    ``outcomes[i]`` is the text a completed member produced (``""`` for a GUI
    action, the queued-screenshot note for a ``screenshot`` member). When
    ``error_index`` is set, that member failed with ``error_text`` and every later
    member was skipped; the result ends with the harness's halt line.
    """
    if not member_names:
        return BATCH_EMPTY_TEXT
    lines: list[str] = []
    for index, name in enumerate(member_names):
        if error_index is not None and index >= error_index:
            break
        text = outcomes[index] if index < len(outcomes) and outcomes[index] is not None else ""
        lines.append(f"[{index}:{name}] {text}")
    if error_index is not None:
        name = member_names[error_index] if error_index < len(member_names) else "?"
        remaining = len(member_names) - error_index - 1
        lines.append(
            f"batch stopped at actions[{error_index}] ({error_index}:{name}): {error_text or 'ERROR'} "
            f"({error_index} completed, {remaining} skipped)"
        )
    return "\n".join(lines).strip()


def format_bash_result(
    output: str,
    exit_code: Optional[int],
    *,
    timed_out: bool = False,
    timeout_seconds: Optional[float] = None,
    max_chars: int = BASH_MAX_OUTPUT_CHARS,
) -> str:
    """Render a foreground ``bash`` result: output, or an exit-code / timeout header plus output."""
    output = truncate_output(output, max_chars)
    if timed_out:
        header = f"Command timed out after {timeout_seconds:g}s" if timeout_seconds is not None else "Command timed out"
        return f"{header}\n{output}" if output else header
    if exit_code is None:
        return f"ERROR: command exit code unavailable\n{output}" if output else "ERROR: command exit code unavailable"
    if exit_code != 0:
        return f"Exit code {exit_code}\n{output}" if output else f"Exit code {exit_code}"
    return output or BASH_NO_OUTPUT_TEXT


def format_background_bash_result(task_id: str, output_path: str, pid: Optional[int]) -> str:
    """Render the result of ``bash`` with ``run_in_background``."""
    lines = [
        f"Started background task `{task_id}`.",
        f"stdout+stderr is streaming to: {output_path}",
        "Use the read tool on that file to retrieve output.",
    ]
    if pid is not None:
        lines.append(f"Process id: {pid}")
        lines.append(f"To cancel: run bash with `kill {pid}`")
    return "\n".join(lines)


def format_cat_n(text: str, *, offset: int = 1, limit: int = READ_DEFAULT_LIMIT) -> str:
    """``cat -n`` rendering of a 1-based line window, as the ``read`` tool returns it."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = max(offset, 1) - 1
    window = lines[start : start + limit]
    return "\n".join(f"{start + index + 1:>6}\t{line}" for index, line in enumerate(window))


def format_read_result(text: str, *, offset: int = 1, limit: int = READ_DEFAULT_LIMIT) -> str:
    """Render a text file for the ``read`` tool (empty-file note, ``cat -n`` window, size cap)."""
    if text == "":
        return READ_EMPTY_FILE_TEXT
    return truncate_output(format_cat_n(text, offset=offset, limit=limit), READ_MAX_OUTPUT_CHARS)


def format_write_result(file_path: str, *, created: bool) -> str:
    """Render the ``write`` tool's success message."""
    if created:
        return f"File created successfully at: {file_path}"
    return f"The file {file_path} has been updated successfully."


class N2EditError(ValueError):
    """An ``edit`` that the harness rejects; ``str(error)`` is the exact model-visible message."""


def apply_edit(
    content: str,
    file_path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> tuple[str, str]:
    """Apply the ``edit`` tool's exact-string replacement to ``content``.

    Returns ``(new_content, result_text)``; raises :class:`N2EditError` with the
    harness's message when the edit is rejected. The read-before-edit gate is the
    adapter's responsibility (it needs per-run state), as is creating a file when
    ``old_string`` is empty and the file does not exist.
    """
    if old_string == new_string:
        raise N2EditError("ERROR: old_string and new_string are identical.")
    if old_string == "":
        raise N2EditError(
            f"ERROR: cannot create {file_path}: it already exists (use a non-empty old_string to edit, "
            "or write to overwrite)."
        )
    count = content.count(old_string)
    if count == 0:
        raise N2EditError("ERROR: old_string not found in file (it must match exactly, including whitespace).")
    if count > 1 and not replace_all:
        raise N2EditError(
            f"ERROR: old_string is not unique ({count} occurrences). Add context or pass replace_all=true."
        )
    new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
    first_line = content[: content.index(old_string)].count("\n") + 1
    start = max(1, first_line - EDIT_SNIPPET_CONTEXT_LINES)
    end = first_line + EDIT_SNIPPET_CONTEXT_LINES + new_string.count("\n")
    snippet = format_cat_n(new_content, offset=start, limit=end - start + 1)
    return new_content, f"The file {file_path} has been updated successfully:\n{snippet}"


def parse_terminal_marker(text: str) -> Optional[str]:
    """Return ``"done"`` / ``"infeasible"`` when a final answer carries the trained marker."""
    if "[INFEASIBLE]" in text:
        return "infeasible"
    if "[DONE]" in text:
        return "done"
    return None
