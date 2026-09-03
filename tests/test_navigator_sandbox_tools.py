"""Hook-level tests for the SDK's shared sandbox file-tool mixin."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from yutori.navigator import FILE_TOOL_SCRIPT, ShellFileToolsMixin, format_shell_output
from yutori.navigator.sandbox_tools import (
    clamp_bash_timeout_or_expired,
    format_shell_result,
    result_returncode,
    result_stderr,
    result_stdout,
)


class RecordingAdapter(ShellFileToolsMixin):
    def __init__(self, result: SimpleNamespace) -> None:
        self.result = result
        self.calls: list[tuple[str, int]] = []

    async def run_sandbox_shell(self, command: str, *, timeout_seconds: int) -> SimpleNamespace:
        self.calls.append((command, timeout_seconds))
        return self.result

    async def file_tool_cwd(self) -> str:
        return "/workspace"


async def test_mixin_builds_the_in_vm_command_with_cwd_and_budgets() -> None:
    adapter = RecordingAdapter(SimpleNamespace(stdout="     1\thello\n", stderr="", returncode=0))

    assert await adapter.read_file("notes.txt") == "     1\thello"

    command, timeout = adapter.calls[0]
    assert command.startswith("python3 -c ")
    assert timeout == 30  # plain file I/O budget; grep/glob get 120/60
    encoded = command.rsplit(" ", 1)[1].strip("'")
    payload = json.loads(base64.b64decode(encoded))
    assert payload["operation"] == "read" and payload["cwd"] == "/workspace"

    await adapter.grep_files("needle")
    assert adapter.calls[1][1] == 120


async def test_mixin_wraps_unexpected_failures_as_plain_error_results() -> None:
    adapter = RecordingAdapter(SimpleNamespace(stdout="", stderr="boom: python3 exploded", returncode=3))

    result = await adapter.write_file("a.txt", "content")

    assert result == "ERROR: boom: python3 exploded"  # never a raised failure envelope


async def test_mixin_empty_search_results_use_the_contract_strings() -> None:
    adapter = RecordingAdapter(SimpleNamespace(stdout="", stderr="", returncode=0))

    assert await adapter.grep_files("nothing") == "No matches found."
    assert await adapter.glob_files("*.nope") == "No files found."


def test_shared_helpers_render_the_bash_contract() -> None:
    assert format_shell_output("out", 0) == "out"
    assert format_shell_output("", 0) == "(Bash completed with no output)"
    assert format_shell_output("boom", 7) == "Exit code 7\nboom"
    assert "def done(text):" in FILE_TOOL_SCRIPT  # in-VM script is exported intact


def test_clamp_bash_timeout_or_expired_flags_a_zero_clamp() -> None:
    assert clamp_bash_timeout_or_expired(30) == (30.0, None)
    assert clamp_bash_timeout_or_expired(None) == (120.0, None)  # missing -> the 120s default
    assert clamp_bash_timeout_or_expired(9999) == (600.0, None)  # clamped to the 600s max
    assert clamp_bash_timeout_or_expired(0) == (0.0, "Command timed out after 0s")
    assert clamp_bash_timeout_or_expired(-5) == (0.0, "Command timed out after 0s")  # clamped to 0


def test_result_accessors_tolerate_missing_and_none_fields() -> None:
    full = SimpleNamespace(stdout="out", stderr="err", returncode=2)
    assert (result_stdout(full), result_stderr(full), result_returncode(full)) == ("out", "err", 2)

    # A sandbox result may omit a field entirely, or set it to None; both read as "unset".
    for sparse in (SimpleNamespace(), SimpleNamespace(stdout=None, stderr=None, returncode=None)):
        assert result_stdout(sparse) == ""
        assert result_stderr(sparse) == ""
        assert result_returncode(sparse) == 0
        assert format_shell_result(sparse) == "(Bash completed with no output)"
