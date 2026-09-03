"""Tests for the SDK-owned Navigator n2 computer-use loop."""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import time
from typing import Any

import pytest
from PIL import Image

from yutori.navigator import (
    TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
    TOOL_SET_COMPUTER_USE_BROWSER_BATCH,
    TOOL_SET_COMPUTER_USE_FILES,
    TOOL_SET_COMPUTER_USE_FILES_BATCH,
    TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
    TOOL_SET_COMPUTER_USE_LATEST,
    N2ActionValidationError,
    N2ComputerAgent,
    convert_n2_items_to_completion_messages,
    execute_n2_computer_call,
    parse_n2_key_expression,
    parse_n2_tool_calls,
    prepare_n2_image_data_url,
    retain_n2_image_window,
    translate_n2_action,
    translate_n2_bash,
    translate_n2_batch,
    translate_n2_read,
    translate_n2_shell_command,
)
from yutori.navigator.macos.types import CancellationLatch, N2Observation
from yutori.navigator.n2 import _CallbackDispatcher
from yutori.navigator.n2_payload import (
    fit_n2_request_images_to_budget,
    image_dimensions,
)

from .conftest import FakeCompletions


def _png_data_url(width: int = 200, height: int = 100) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _png_b64(width: int = 200, height: int = 100) -> str:
    return _png_data_url(width, height).split(",", 1)[1]


def _observation(native_width: int = 200, native_height: int = 100) -> N2Observation:
    encoded = base64.b64decode(_png_b64(20, 10))
    return N2Observation(1, native_width, native_height, 20, 10, "image/png", encoded)


# ---------------------------------------------------------------------------
# Action translation
# ---------------------------------------------------------------------------


def test_click_maps_normalized_coordinates_to_native_pixels():
    actions = translate_n2_action("left_click", {"coordinates": [500, 500]}, 2000, 1000)
    assert actions == [{"type": "click", "x": 1000, "y": 500, "button": "left"}]
    # The 1000 edge clamps to the last pixel index.
    actions = translate_n2_action("left_click", {"coordinates": [1000, 1000]}, 2000, 1000)
    assert actions == [{"type": "click", "x": 1999, "y": 999, "button": "left"}]


@pytest.mark.parametrize("bad", [[-1, 0], [0, 1001], [1.5, 2], [True, 2], [1], "x", None])
def test_coordinates_are_strictly_validated(bad):
    with pytest.raises(N2ActionValidationError):
        translate_n2_action("left_click", {"coordinates": bad}, 1000, 1000)


def test_triple_click_translates_to_an_explicit_primitive():
    actions = translate_n2_action("triple_click", {"coordinates": [0, 0]}, 100, 100)
    assert [action["type"] for action in actions] == ["triple_click"]


def test_held_modifier_is_rejected_without_the_latest_capability():
    with pytest.raises(N2ActionValidationError, match="key_press for keyboard shortcuts"):
        translate_n2_action("left_click", {"coordinates": [1, 1], "modifier": "ctrl"}, 100, 100)
    # modifier_keys folds into modifier before the same rejection.
    with pytest.raises(N2ActionValidationError, match="key_press for keyboard shortcuts"):
        translate_n2_action("left_click", {"coordinates": [1, 1], "modifier_keys": ["ctrl"]}, 100, 100)
    with pytest.raises(N2ActionValidationError, match="must be one or more"):
        translate_n2_action(
            "left_click",
            {"coordinates": [1, 1], "modifier": "+"},
            100,
            100,
            allow_click_modifiers=True,
        )


@pytest.mark.parametrize(
    ("action", "expected_types"),
    [
        ("left_click", ["click"]),
        ("right_click", ["click"]),
        ("middle_click", ["click"]),
        ("double_click", ["double_click"]),
        ("triple_click", ["triple_click"]),
    ],
)
def test_click_family_accepts_canonical_modifiers_with_the_latest_capability(action, expected_types):
    actions = translate_n2_action(
        action,
        {"coordinates": [500, 500], "modifier": "command+shift"},
        100,
        100,
        allow_click_modifiers=True,
    )
    assert [translated["type"] for translated in actions] == expected_types
    assert all(translated["modifier"] == ["cmd", "shift"] for translated in actions)

    alias_actions = translate_n2_action(
        action,
        {"coordinates": [500, 500], "modifier_keys": ["command", "shift"]},
        100,
        100,
        allow_click_modifiers=True,
    )
    assert alias_actions == actions


def test_null_or_empty_modifier_is_an_ordinary_click():
    actions = translate_n2_action("left_click", {"coordinates": [1, 1], "modifier": None}, 100, 100)
    assert actions[0]["type"] == "click"


def test_scroll_converts_amount_to_pixels_and_validates_direction():
    actions = translate_n2_action("scroll", {"coordinates": [500, 500], "direction": "down", "amount": 3}, 1000, 800)
    assert actions == [{"type": "scroll", "x": 500, "y": 400, "scroll_x": 0, "scroll_y": 240}]
    # The served schema names all four directions; left/right ride the scroll_x slot.
    actions = translate_n2_action("scroll", {"coordinates": [500, 500], "direction": "right", "amount": 3}, 1000, 800)
    assert actions == [{"type": "scroll", "x": 500, "y": 400, "scroll_x": 300, "scroll_y": 0}]
    actions = translate_n2_action("scroll", {"coordinates": [500, 500], "direction": "left", "amount": 2}, 1000, 800)
    assert actions == [{"type": "scroll", "x": 500, "y": 400, "scroll_x": -200, "scroll_y": 0}]
    with pytest.raises(N2ActionValidationError, match="direction"):
        translate_n2_action("scroll", {"coordinates": [1, 1], "direction": "sideways", "amount": 1}, 100, 100)
    with pytest.raises(N2ActionValidationError, match="amount"):
        translate_n2_action("scroll", {"coordinates": [1, 1], "direction": "up", "amount": 51}, 100, 100)
    modified = translate_n2_action(
        "scroll",
        {"coordinates": [1, 1], "direction": "up", "amount": 1, "modifier": "shift"},
        100,
        100,
        allow_click_modifiers=True,
    )
    assert modified[0]["modifier"] == ["shift"]


def test_click_and_scroll_modifier_capabilities_can_be_configured_independently():
    click = translate_n2_action(
        "left_click",
        {"coordinates": [1, 1], "modifier": "ctrl"},
        100,
        100,
        allow_click_modifiers=True,
        allow_scroll_modifiers=False,
    )
    assert click[0]["modifier"] == ["ctrl"]

    with pytest.raises(N2ActionValidationError, match="not supported by this computer handler"):
        translate_n2_action(
            "scroll",
            {"coordinates": [1, 1], "direction": "down", "amount": 1, "modifier": "shift"},
            100,
            100,
            allow_click_modifiers=True,
            allow_scroll_modifiers=False,
        )


def test_key_press_parses_chords_sequences_and_aliases():
    assert parse_n2_key_expression("ctrl+a enter") == [["ctrl", "a"], ["enter"]]
    assert parse_n2_key_expression("Command+Period") == [["cmd", "."]]
    assert parse_n2_key_expression("ArrowUp Insert") == [["up"], ["insert"]]
    # Names outside the vocabulary pass through lowercased; the handler decides.
    assert parse_n2_key_expression("notakey") == [["notakey"]]
    with pytest.raises(N2ActionValidationError, match="invalid key combination"):
        parse_n2_key_expression("ctrl++a")


def test_wait_bounds_and_millisecond_conversion():
    assert translate_n2_action("wait", {"duration": 2.5}, 100, 100) == [{"type": "wait", "ms": 2500}]
    assert translate_n2_action("wait", {"duration": 300}, 100, 100) == [{"type": "wait", "ms": 300000}]
    with pytest.raises(N2ActionValidationError):
        translate_n2_action("wait", {"duration": 301}, 100, 100)


def test_hold_key_without_a_duration_is_held_through_the_next_action():
    assert translate_n2_action("hold_key", {"key": "ctrl"}, 100, 100) == [
        {"type": "hold_key_until_next_action", "key": "ctrl"}
    ]
    assert translate_n2_action("hold_key", {"key": "ctrl", "duration": 0}, 100, 100) == [
        {"type": "hold_key", "key": "ctrl", "ms": 0}
    ]


def test_shell_command_validation_mirrors_the_server_schema():
    assert translate_n2_shell_command({"command": "ls", "cwd": "/tmp"}) == [
        {"type": "run_shell_command", "command": "ls", "timeout_seconds": 10, "cwd": "/tmp"}
    ]
    with pytest.raises(N2ActionValidationError, match="timeout_seconds"):
        translate_n2_shell_command({"command": "ls", "timeout_seconds": 31})
    with pytest.raises(N2ActionValidationError, match="non-empty command"):
        translate_n2_shell_command({"command": "  "})
    with pytest.raises(N2ActionValidationError, match="unsupported field"):
        translate_n2_shell_command({"command": "ls", "shell": "zsh"})


def test_bash_validation_has_its_own_contract():
    assert translate_n2_bash({"command": "ls"}) == [
        {"type": "run_bash_command", "command": "ls", "timeout": 120.0, "run_in_background": False}
    ]
    with pytest.raises(N2ActionValidationError, match="timeout"):
        translate_n2_bash({"command": "ls", "timeout": 601})
    with pytest.raises(N2ActionValidationError, match="unsupported field"):
        translate_n2_bash({"command": "ls", "cwd": "/tmp"})


def test_read_uses_the_served_one_based_line_offset():
    assert translate_n2_read({"file_path": "notes.txt"}) == [
        {"type": "read_file", "file_path": "notes.txt", "offset": 1, "limit": 2_000}
    ]
    with pytest.raises(N2ActionValidationError, match="1-based"):
        translate_n2_read({"file_path": "notes.txt", "offset": 0})


def test_historical_glob_rejects_a_null_path_like_the_served_schema():
    output = parse_n2_tool_calls(
        {"tool_calls": [{"id": "glob", "function": {"name": "glob", "arguments": '{"pattern":"*.py","path":null}'}}]},
        100,
        100,
        tool_set=TOOL_SET_COMPUTER_USE_FILES,
    )
    assert output[-1]["output"].startswith("[ERROR] Invalid glob call:")


def test_batch_accepts_both_envelopes_and_is_all_or_nothing():
    validated, translated = translate_n2_batch(
        {
            "actions": [
                {"name": "left_click", "arguments": {"coordinates": [0, 0]}},
                {"action": "key_press", "key": "enter"},
            ]
        },
        100,
        100,
    )
    assert [member["action"] for member in validated] == ["left_click", "key_press"]
    assert [action["batch_index"] for action in translated] == [0, 1]
    # One bad member fails the whole batch before anything is returned.
    with pytest.raises(N2ActionValidationError):
        translate_n2_batch(
            {
                "actions": [
                    {"action": "left_click", "coordinates": [0, 0]},
                    {"action": "left_click", "coordinates": [0, 2000]},
                ]
            },
            100,
            100,
        )


def test_historical_batches_reject_screenshot_and_all_batches_reject_shell_members():
    with pytest.raises(N2ActionValidationError, match="screenshot inside computer_batch"):
        translate_n2_batch(
            {"actions": [{"action": "screenshot"}]},
            100,
            100,
            tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
        )
    with pytest.raises(N2ActionValidationError, match="not allowed inside computer_batch"):
        translate_n2_batch({"actions": [{"action": "bash", "command": "ls"}]}, 100, 100)


def test_current_batches_accept_screenshot_as_a_no_op_member():
    validated, translated = translate_n2_batch({"actions": [{"action": "screenshot"}]}, 100, 100)
    assert validated == [{"action": "screenshot"}]
    assert translated == [{"type": "screenshot", "batch_index": 0}]


def test_historical_batches_reject_modified_scroll_before_translating_any_member():
    with pytest.raises(N2ActionValidationError, match="does not allow a held modifier"):
        translate_n2_batch(
            {
                "actions": [
                    {"action": "left_click", "coordinates": [10, 10]},
                    {
                        "action": "scroll",
                        "coordinates": [500, 500],
                        "direction": "down",
                        "amount": 1,
                        "modifier": "ctrl",
                    },
                ]
            },
            100,
            100,
            tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        )


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_prepare_image_converts_format_only_and_never_resizes():
    frame = prepare_n2_image_data_url(_png_data_url(1920, 1080))
    assert frame.startswith("data:image/webp;base64,")
    assert image_dimensions(frame) == (1920, 1080)  # the capture defines the size
    # A source already in the target encoding passes through byte-for-byte.
    assert prepare_n2_image_data_url(frame) == frame
    png = prepare_n2_image_data_url(_png_data_url(200, 100), "png")
    assert png == _png_data_url(200, 100)


def test_image_window_keeps_only_two_newest_image_messages():
    def image_message(url):
        return {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}

    messages = [image_message(f"data:image/png;base64,{index}") for index in range(4)]
    windowed = retain_n2_image_window(messages)
    kept = [any(part["type"] == "image_url" for part in message["content"]) for message in windowed]
    assert kept == [False, False, True, True]
    # Pruned frames leave the marker the model expects; `None` drops them outright.
    assert windowed[0]["content"] == [{"type": "text", "text": "[older image omitted]"}]
    assert retain_n2_image_window(messages, omitted_text=None)[0]["content"] == []
    # The original list is untouched.
    assert all(message["content"] for message in messages)


def test_budget_drops_the_older_image_then_raises():
    big_url = _png_data_url(600, 400)

    def image_message():
        return {"role": "user", "content": [{"type": "image_url", "image_url": {"url": big_url}}]}

    budget = len(big_url) + 200
    fitted = fit_n2_request_images_to_budget([image_message(), image_message()], budget)
    assert not fitted[0]["content"] and fitted[1]["content"]
    with pytest.raises(ValueError, match="cannot fit"):
        fit_n2_request_images_to_budget([image_message(), image_message()], 100)


# ---------------------------------------------------------------------------
# Conversion and tool-call parsing
# ---------------------------------------------------------------------------


def test_converter_folds_calls_and_carries_shell_text_before_the_screenshot():
    items = [
        {"role": "user", "content": "task"},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
        {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"command":"ls"}'},
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": {"type": "input_image", "image_url": "data:x", "result": "file.txt"},
        },
        {"type": "function_call_output", "call_id": "c2", "output": "[ERROR] nope"},
    ]
    messages = convert_n2_items_to_completion_messages(items)
    assert messages[0] == {"role": "user", "content": [{"type": "text", "text": "task"}]}
    # A legacy standalone reasoning item stays assistant text; the call folds into it.
    assert messages[1]["role"] == "assistant" and messages[1]["content"] == "thinking"
    assert "reasoning_content" not in messages[1]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "bash"
    tool_message = messages[2]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert tool_message["content"][0] == {"type": "text", "text": "file.txt"}
    assert tool_message["content"][1]["type"] == "image_url"
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "c2",
        "content": [{"type": "text", "text": "[ERROR] nope"}],
    }


def test_parse_tool_calls_attaches_executions_and_feeds_back_validation_errors():
    message = {
        "content": "on it",
        "reasoning_content": "hmm",
        "tool_calls": [
            {
                "id": "c1",
                "function": {"name": "left_click", "arguments": '{"coordinates": [500, 500]}'},
            },
            {"id": "c2", "function": {"name": "left_click", "arguments": '{"coordinates": [2,'}},
        ],
    }
    output = parse_n2_tool_calls(
        message,
        1000,
        1000,
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        execution_deadline=12.5,
    )
    assert output[0]["type"] == "message"
    assert output[0]["reasoning"] == "hmm"
    valid = output[1]
    assert valid["_computer_actions"] == [{"type": "click", "x": 500, "y": 500, "button": "left"}]
    assert valid["_requires_confirmation"] is True
    assert valid["_execution_deadline"] == 12.5
    # Every call of the turn is translated; the malformed second one is answered in place.
    assert [item.get("call_id") for item in output[2:]] == ["c2", "c2"]
    assert output[-1]["output"].startswith("[ERROR] Invalid left_click call:")


def test_an_invalid_call_does_not_block_the_next_one():
    output = parse_n2_tool_calls(
        {
            "content": "",
            "tool_calls": [
                {"id": "bad", "function": {"name": "left_click", "arguments": '{"coordinates": [2,'}},
                {"id": "good", "function": {"name": "left_click", "arguments": '{"coordinates": [1, 1]}'}},
            ],
        },
        100,
        100,
    )
    # Calls come first, validation-error results after the turn's full run of
    # calls, so the wire keeps one assistant message.
    assert [item.get("call_id") for item in output] == ["bad", "good", "bad", "good"]
    assert output[2]["output"].startswith("[ERROR] Invalid left_click call:")
    # The second call is still translated on its own terms (here: the batch-only
    # default tool set does not expose a standalone left_click).
    assert "does not expose left_click" in output[-1]["output"]


def test_parse_tool_calls_terminal_message_keeps_an_empty_answer_empty():
    output = parse_n2_tool_calls({"content": "", "tool_calls": []}, 100, 100)
    assert output[-1]["type"] == "message"
    assert output[-1]["content"][0]["text"] == ""


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class FakeComputer:
    def __init__(self):
        self.calls: list[tuple] = []
        self.shell_result: Any = "ok"
        self.fail_on: str = ""

    async def screenshot(self):
        self.calls.append(("screenshot",))
        return _png_b64()

    async def click(self, x, y, button="left", count=1, modifier=None):
        if self.fail_on == "click":
            raise RuntimeError("driver refused click")
        self.calls.append(("click", x, y, button, count, modifier))

    async def triple_click(self, x, y, modifier=None):
        call = ("triple_click", x, y)
        if modifier is not None:
            call += (tuple(modifier),)
        self.calls.append(call)

    async def keypress(self, keys):
        self.calls.append(("keypress", tuple(keys)))

    async def key_down(self, key):
        self.calls.append(("key_down", key))

    async def key_up(self, key):
        self.calls.append(("key_up", key))

    async def type(self, text):
        self.calls.append(("type", text))

    async def wait(self, ms):
        self.calls.append(("wait", ms))

    async def run_bash_command(self, command, timeout, run_in_background):
        if isinstance(self.shell_result, Exception):
            raise self.shell_result
        self.calls.append(("bash", command))
        return self.shell_result

    async def read_file(self, file_path, offset, limit):
        self.calls.append(("read", file_path, offset, limit))
        return "     1\tcontents"

    async def write_file(self, file_path, content):
        self.calls.append(("write", file_path, content))
        return f"Wrote {len(content)} characters to {file_path}."

    async def edit_file(self, file_path, old_string, new_string, replace_all):
        self.calls.append(("edit", file_path, old_string, new_string, replace_all))
        return f"Edited {file_path}: replaced 1 occurrence(s)."

    async def grep_files(self, **kwargs):
        self.calls.append(("grep", kwargs))
        return "notes.txt"

    async def glob_files(self, **kwargs):
        self.calls.append(("glob", kwargs))
        return "notes.txt"

    async def goto_url(self, url):
        self.calls.append(("goto_url", url))


class FakePresentation:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def present(self, event):
        self.events.append(event)


def _batch_item(**overrides):
    message = {
        "content": "",
        "tool_calls": [
            {
                "id": "c1",
                "function": {
                    "name": "computer_batch",
                    "arguments": json.dumps(
                        {
                            "actions": [
                                {"action": "left_click", "coordinates": [1, 1]},
                                {"action": "key_press", "key": "enter"},
                            ]
                        }
                    ),
                },
            }
        ],
    }
    item = parse_n2_tool_calls(message, 100, 100, **overrides)[-1]
    return item


async def test_execute_batch_reports_one_line_per_member_with_a_frame():
    computer = FakeComputer()
    result = await execute_n2_computer_call(
        _batch_item(), computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0
    )
    output = result[0]["output"]
    assert output["type"] == "input_image" and output["result"] == "[0:left_click] \n[1:key_press]"
    assert ("click", 0, 0, "left", 1, None) in computer.calls


async def test_execute_stops_the_batch_at_the_first_gui_failure():
    computer = FakeComputer()
    computer.fail_on = "click"
    result = await execute_n2_computer_call(
        _batch_item(), computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0
    )
    assert result[0]["output"]["result"] == (
        "batch stopped at actions[0] (0:left_click): ERROR: RuntimeError: driver refused click (0 completed, 1 skipped)"
    )
    assert not [call for call in computer.calls if call[0] == "keypress"]


def _bash_call_item(command="ls"):
    message = {
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": json.dumps({"command": command})}}],
    }
    return parse_n2_tool_calls(message, 100, 100)[-1]


async def test_execute_shell_returns_the_handler_text_as_is():
    computer = FakeComputer()
    computer.shell_result = "x" * 31_000
    result = await execute_n2_computer_call(_bash_call_item(), computer, callbacks=_CallbackDispatcher(None))
    # The tool owns its result: no loop-side rendering, truncation, or frame.
    assert result[0]["output"] == "x" * 31_000


async def test_execute_shell_failures_are_recoverable_tool_errors():
    computer = FakeComputer()
    computer.shell_result = TimeoutError("killed after 120s")
    result = await execute_n2_computer_call(
        _bash_call_item(), computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0
    )
    assert result[0]["output"] == "[ERROR] bash failed: killed after 120s"

    class NoShell:
        async def screenshot(self):
            return _png_b64()

    result = await execute_n2_computer_call(
        _bash_call_item(), NoShell(), callbacks=_CallbackDispatcher(None), screenshot_delay=0
    )
    assert result[0]["output"] == "[ERROR] bash is not supported by this computer environment."


@pytest.mark.parametrize(
    ("name", "arguments", "expected_call", "expected_output"),
    [
        ("read", {"file_path": "notes.txt"}, ("read", "notes.txt", 1, 2_000), "     1\tcontents"),
        (
            "write",
            {"file_path": "notes.txt", "content": "hello"},
            ("write", "notes.txt", "hello"),
            "Wrote 5 characters to notes.txt.",
        ),
        (
            "edit",
            {"file_path": "notes.txt", "old_string": "old", "new_string": "new"},
            ("edit", "notes.txt", "old", "new", False),
            "Edited notes.txt: replaced 1 occurrence(s).",
        ),
    ],
)
async def test_current_file_tools_return_one_text_result_without_a_screenshot(
    name, arguments, expected_call, expected_output
):
    computer = FakeComputer()
    item = parse_n2_tool_calls(
        {"tool_calls": [{"id": "file", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
        100,
        100,
    )[-1]

    result = await execute_n2_computer_call(item, computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0)

    assert result == [{"type": "function_call_output", "call_id": "file", "output": expected_output}]
    assert computer.calls == [expected_call]


@pytest.mark.parametrize("tool_set", [TOOL_SET_COMPUTER_USE_FILES, TOOL_SET_COMPUTER_USE_FILES_BATCH])
@pytest.mark.parametrize(
    ("name", "arguments", "expected_output"),
    [
        ("grep", {"pattern": "TODO"}, "notes.txt"),
        ("glob", {"pattern": "**/*.py"}, "notes.txt"),
    ],
)
async def test_historical_file_search_tools_are_executable(tool_set, name, arguments, expected_output):
    computer = FakeComputer()
    item = parse_n2_tool_calls(
        {"tool_calls": [{"id": name, "function": {"name": name, "arguments": json.dumps(arguments)}}]},
        100,
        100,
        tool_set=tool_set,
    )[-1]

    result = await execute_n2_computer_call(item, computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0)

    assert result == [{"type": "function_call_output", "call_id": name, "output": expected_output}]
    assert computer.calls[0][0] == name


def test_current_file_tool_set_does_not_claim_historical_file_search_tools():
    for name, arguments in (("grep", {"pattern": "TODO"}), ("glob", {"pattern": "*.py"})):
        output = parse_n2_tool_calls(
            {"tool_calls": [{"id": name, "function": {"name": name, "arguments": json.dumps(arguments)}}]},
            100,
            100,
        )
        assert output[-1]["output"].startswith(f"[ERROR] Invalid {name} call:")


async def test_browser_tool_set_requires_and_uses_a_browser_navigation_handler():
    computer = FakeComputer()
    item = parse_n2_tool_calls(
        {
            "tool_calls": [
                {"id": "navigate", "function": {"name": "goto_url", "arguments": '{"url":"https://example.com"}'}}
            ]
        },
        100,
        100,
        tool_set=TOOL_SET_COMPUTER_USE_BROWSER_BATCH,
    )[-1]

    await execute_n2_computer_call(item, computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0)

    assert ("goto_url", "https://example.com") in computer.calls

    class NoBrowser:
        async def screenshot(self):
            return _png_b64()

    result = await execute_n2_computer_call(item, NoBrowser(), callbacks=_CallbackDispatcher(None), screenshot_delay=0)
    assert result[0]["output"] == "[ERROR] goto_url is only supported by a browser computer environment."


async def test_hold_key_without_duration_stays_down_for_one_batch_member_and_is_cleaned_up():
    computer = FakeComputer()
    item = parse_n2_tool_calls(
        {
            "tool_calls": [
                {
                    "id": "hold",
                    "function": {
                        "name": "computer_batch",
                        "arguments": json.dumps(
                            {
                                "actions": [
                                    {"action": "hold_key", "key": "ctrl"},
                                    {"action": "left_click", "coordinates": [100, 100]},
                                ]
                            }
                        ),
                    },
                }
            ]
        },
        100,
        100,
    )[-1]

    await execute_n2_computer_call(item, computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0)

    key_down = computer.calls.index(("key_down", "ctrl"))
    click = next(index for index, call in enumerate(computer.calls) if call[0] == "click")
    key_up = computer.calls.index(("key_up", "ctrl"))
    assert key_down < click < key_up


def test_current_tool_set_rejects_standalone_gui_and_screenshot_calls():
    for name, arguments in (
        ("left_click", {"coordinates": [1, 1]}),
        ("screenshot", {}),
    ):
        output = parse_n2_tool_calls(
            {"tool_calls": [{"id": name, "function": {"name": name, "arguments": json.dumps(arguments)}}]},
            100,
            100,
        )
        assert output[-1]["output"].startswith(f"[ERROR] Invalid {name} call:")


async def test_execute_denied_confirmation_runs_nothing():
    computer = FakeComputer()

    async def deny(_request):
        return False

    result = await execute_n2_computer_call(
        _batch_item(),
        computer,
        callbacks=_CallbackDispatcher(None),
        confirmation_callback=deny,
        screenshot_delay=0,
    )
    assert result[0]["output"] == "[ERROR] Action was not confirmed by the user."
    assert computer.calls == []


async def test_execute_expired_deadline_truncates_in_flight_actions():
    computer = FakeComputer()
    item = _batch_item(execution_deadline=time.monotonic() - 1)
    result = await execute_n2_computer_call(item, computer, callbacks=_CallbackDispatcher(None), screenshot_delay=0)
    assert result[0]["output"]["result"].startswith("batch stopped at actions[0] (0:left_click): deadline_reached")
    assert not [call for call in computer.calls if call[0] == "click"]


async def test_stop_during_overlay_lead_prevents_the_physical_action():
    computer = FakeComputer()
    computer.cancellation = CancellationLatch()

    class StopDuringPresentation(FakePresentation):
        async def present(self, event):
            await super().present(event)
            if event["type"] == "action":
                computer.cancellation.request("operator_stop")
                await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError, match="operator_stop"):
        await execute_n2_computer_call(
            parse_n2_tool_calls(
                {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "left_click", "arguments": '{"coordinates":[1,1]}'}},
                    ]
                },
                100,
                100,
                tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
            )[-1],
            computer,
            callbacks=_CallbackDispatcher(None),
            presentation=StopDuringPresentation(),
        )
    assert not [call for call in computer.calls if call[0] == "click"]


async def test_typed_text_never_enters_presentation_events():
    computer = FakeComputer()
    presentation = FakePresentation()
    item = parse_n2_tool_calls(
        {
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "type", "arguments": '{"text":"sensitive clipboard value"}'},
                }
            ]
        },
        100,
        100,
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
    )[-1]
    await execute_n2_computer_call(
        item,
        computer,
        callbacks=_CallbackDispatcher(None),
        presentation=presentation,
        screenshot_delay=0,
    )
    assert ("type", "sensitive clipboard value") in computer.calls
    assert "sensitive clipboard value" not in repr(presentation.events)
    assert presentation.events[0]["arguments"]["text"] == "[text]"


# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------


def _turn(message):
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


async def test_agent_requires_latest_tool_set_and_handler_capability_for_modifiers():
    response = _turn(
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "computer_batch",
                        "arguments": json.dumps(
                            {
                                "actions": [
                                    {
                                        "name": "triple_click",
                                        "arguments": {"coordinates": [500, 500], "modifier": "command"},
                                    }
                                ]
                            }
                        ),
                    },
                }
            ],
        }
    )
    computer = FakeComputer()
    latest = N2ComputerAgent(
        computer=computer,
        tool_set=TOOL_SET_COMPUTER_USE_LATEST,
        completions=FakeCompletions([response, _turn({"content": "Done.", "tool_calls": []})]),
        screenshot_delay=0,
        supports_click_modifiers=True,
    )
    async for _step in latest.run("task"):
        pass
    assert ("triple_click", 100, 50, ("cmd",)) in computer.calls

    unsupported_handler = N2ComputerAgent(
        computer=FakeComputer(),
        tool_set=TOOL_SET_COMPUTER_USE_LATEST,
        completions=FakeCompletions([response]),
    )
    unsupported_step = await unsupported_handler._predict_step([{"role": "user", "content": "task"}])
    assert unsupported_step["output"][-1]["output"].startswith("[ERROR] Invalid computer_batch call")

    with pytest.raises(ValueError, match="modifier-capable"):
        N2ComputerAgent(
            computer=FakeComputer(),
            tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
            completions=FakeCompletions([]),
            supports_click_modifiers=True,
        )


async def test_triple_click_falls_back_for_legacy_computer_handlers():
    class LegacyComputer:
        def __init__(self):
            self.calls = []
            self.double_result = None

        async def screenshot(self):
            return _png_b64()

        async def double_click(self, x, y, modifier=None):
            self.calls.append(("double_click", x, y, tuple(modifier or [])))
            return self.double_result

        async def click(self, x, y, button="left", modifier=None):
            self.calls.append(("click", x, y, button, tuple(modifier or [])))

    response = _turn(
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "computer_batch",
                        "arguments": json.dumps(
                            {
                                "actions": [
                                    {
                                        "name": "triple_click",
                                        "arguments": {"coordinates": [500, 500]},
                                    }
                                ]
                            }
                        ),
                    },
                }
            ],
        }
    )
    computer = LegacyComputer()
    agent = N2ComputerAgent(
        computer=computer,
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        completions=FakeCompletions([response, _turn({"content": "Done.", "tool_calls": []})]),
        screenshot_delay=0,
    )
    async for _step in agent.run("task"):
        pass
    assert computer.calls == [
        ("double_click", 100, 50, ()),
        ("click", 100, 50, "left", ()),
    ]

    failed_computer = LegacyComputer()
    failed_computer.double_result = {"success": False, "error": "double failed"}
    item = parse_n2_tool_calls(response["choices"][0]["message"], 200, 100)[-1]
    result = await execute_n2_computer_call(
        item,
        failed_computer,
        callbacks=_CallbackDispatcher(None),
        screenshot_delay=0,
    )
    assert failed_computer.calls == [("double_click", 100, 50, ())]
    assert "double failed" in result[0]["output"]["result"]


async def test_agent_runs_a_click_turn_then_finishes():
    completions = FakeCompletions(
        [
            _turn(
                {
                    "content": "clicking",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "left_click",
                                "arguments": '{"coordinates": [500, 500]}',
                            },
                        }
                    ],
                }
            ),
            _turn({"content": "Done.", "tool_calls": []}),
        ]
    )
    computer = FakeComputer()
    agent = N2ComputerAgent(
        computer=computer,
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        completions=completions,
        instructions="be careful",
        screenshot_delay=0,
    )
    steps = [step async for step in agent.run("open calculator")]

    # Blind start: a handler without get_dimensions is measured with one frame
    # that is NOT sent; the first request carries the guidelines and the text alone.
    assert computer.calls[0] == ("screenshot",)
    first_request = completions.requests[0]
    assert first_request["tool_set"] == TOOL_SET_COMPUTER_USE_HYBRID_BATCH
    assert first_request["model"] == "n2"
    assert first_request["max_completion_tokens"] == 20480
    assert first_request["parallel_tool_calls"] is True
    assert first_request["timeout"] == 600.0
    assert "temperature" not in first_request
    assert first_request["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "be careful"}]}
    assert first_request["messages"][1] == {"role": "user", "content": [{"type": "text", "text": "open calculator"}]}
    assert "system" not in {message["role"] for message in first_request["messages"]}
    assert not any(
        part.get("type") == "image_url"
        for message in first_request["messages"]
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
    )

    # Turn 1 yields the model step, then the execution result — a GUI turn, so it carries the frame.
    assert steps[0]["usage"]["prompt_tokens"] == 5
    result_frames = [item for step in steps for item in step["output"] if item.get("type") == "function_call_output"]
    assert len(result_frames) == 1
    assert result_frames[0]["output"]["type"] == "input_image"
    # The click executed against the measured native size (200x100).
    assert ("click", 100, 50, "left", 1, None) in computer.calls

    # Turn 2's terminal assistant message ended the run.
    final_texts = [
        part["text"]
        for step in steps
        for item in step["output"]
        if item.get("type") == "message"
        for part in item.get("content") or []
    ]
    assert final_texts[-1] == "Done."


async def test_agent_defaults_to_latest_tool_set_and_preserves_native_observation_dimensions():
    class ObservationComputer(FakeComputer):
        def __init__(self):
            super().__init__()
            self.current_observation = None

        async def screenshot(self):
            self.calls.append(("screenshot",))
            self.current_observation = _observation(2000, 1000)
            return self.current_observation

    completions = FakeCompletions(
        [
            _turn(
                {
                    "content": "",
                    "reasoning_content": "Use the center control",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "computer_batch",
                                "arguments": (
                                    '{"actions":[{"name":"left_click","arguments":{"coordinates":[500,500]}}]}'
                                ),
                            },
                        }
                    ],
                }
            ),
            _turn({"content": "Finished", "tool_calls": []}),
        ]
    )
    computer = ObservationComputer()
    presentation = FakePresentation()
    agent = N2ComputerAgent(computer=computer, completions=completions, presentation=presentation, screenshot_delay=0)
    steps = [step async for step in agent.run("task")]

    assert completions.requests[0]["tool_set"] == TOOL_SET_COMPUTER_USE_LATEST
    assert ("click", 1000, 500, "left", 1, None) in computer.calls
    # The task opens the transcript, so a presentation shows the conversation from its first message.
    assert [event["type"] for event in presentation.events] == [
        "task",
        "reasoning",
        "batch_member",
        "action_done",
        "final",
    ]
    assert presentation.events[0]["text"] == "task"
    final = steps[-1]["output"][-1]["content"][0]["text"]
    assert final == "Finished"


async def test_agent_includes_explicit_temperature():
    completions = FakeCompletions([_turn({"content": "done", "tool_calls": []})])
    agent = N2ComputerAgent(computer=FakeComputer(), completions=completions, temperature=0.2)
    async for _ in agent.run("task"):
        pass
    assert completions.requests[0]["temperature"] == 0.2


async def test_operator_stop_cancels_an_in_flight_model_request():
    class HangingCompletions:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = False

        async def create(self, **_kwargs):
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    computer = FakeComputer()
    computer.cancellation = CancellationLatch()
    completions = HangingCompletions()
    agent = N2ComputerAgent(computer=computer, completions=completions)
    generator = agent.run("task")
    prediction = asyncio.create_task(generator.__anext__())
    await completions.started.wait()
    computer.cancellation.request("operator_stop")
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await prediction
    assert computer.cancellation.cause == "operator_stop"
    assert completions.cancelled is True


async def test_outer_task_cancellation_cancels_an_in_flight_model_request():
    class HangingCompletions:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = False

        async def create(self, **_kwargs):
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    completions = HangingCompletions()
    agent = N2ComputerAgent(computer=FakeComputer(), completions=completions)
    prediction = asyncio.create_task(agent.run("task").__anext__())
    await completions.started.wait()
    prediction.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prediction
    assert completions.cancelled is True


async def test_agent_does_not_execute_calls_that_failed_validation():
    completions = FakeCompletions(
        [
            _turn(
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "left_click",
                                "arguments": '{"coordinates": [0, 5000]}',
                            },
                        }
                    ],
                }
            ),
            _turn({"content": "giving up", "tool_calls": []}),
        ]
    )
    computer = FakeComputer()
    agent = N2ComputerAgent(
        computer=computer,
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        completions=completions,
        screenshot_delay=0,
    )
    steps = [step async for step in agent.run("task")]
    outputs = [
        item["output"] for step in steps for item in step["output"] if item.get("type") == "function_call_output"
    ]
    # A call that never ran returns no frame: results come from the tool's own execution.
    (error,) = outputs
    assert str(error).startswith("[ERROR] Invalid left_click call")
    assert not [call for call in computer.calls if call[0] == "click"]
    assert computer.calls.count(("screenshot",)) == 1  # only the blind-start size measurement


async def test_agent_guard_can_stop_the_run_and_run_end_still_fires():
    class Guard:
        def __init__(self):
            self.ended = False

        async def on_run_continue(self, _kwargs, _old, _new):
            return False

        async def on_run_end(self, _kwargs, _old, _new):
            self.ended = True

    guard = Guard()
    agent = N2ComputerAgent(
        computer=FakeComputer(),
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        completions=FakeCompletions([]),
        callbacks=[guard],
    )
    steps = [step async for step in agent.run("task")]
    assert steps == []
    assert guard.ended


def test_agent_rejects_unknown_tool_sets_and_missing_credentials():
    with pytest.raises(ValueError, match="Unsupported n2 tool_set"):
        N2ComputerAgent(computer=FakeComputer(), tool_set="browser_tools_core-20260403", api_key="k")
    with pytest.raises(ValueError, match="completions or api_key"):
        N2ComputerAgent(computer=FakeComputer(), tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH)


async def test_agent_requests_stay_within_the_two_image_window():
    turns = []
    for index in range(3):
        turns.append(
            _turn(
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{index}",
                            "function": {
                                "name": "left_click",
                                "arguments": '{"coordinates": [1, 1]}',
                            },
                        }
                    ],
                }
            )
        )
    turns.append(_turn({"content": "done", "tool_calls": []}))
    completions = FakeCompletions(turns)
    agent = N2ComputerAgent(
        computer=FakeComputer(),
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        completions=completions,
        screenshot_delay=0,
    )
    async for _step in agent.run("task"):
        pass
    last_request = completions.requests[-1]
    image_parts = [
        part
        for message in last_request["messages"]
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert len(image_parts) == 2
    # Every image the wire carries is the default WebP re-encode of the raw capture.
    assert all(part["image_url"]["url"].startswith("data:image/webp;") for part in image_parts)


async def test_agent_owns_a_real_client_when_given_credentials():
    """The api_key path must construct the SDK's actual async client.

    The lazy import inside _resolve_completions is invisible to every test
    that passes a scripted completions double — a live run failed on a
    misnamed import that no mock could catch.
    """
    agent = N2ComputerAgent(
        computer=FakeComputer(),
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        api_key="yt-test",
        base_url="https://api.dev.yutori.com/v1",
    )
    completions = agent._resolve_completions()
    assert hasattr(completions, "create")
    # Resolving twice reuses the same owned client.
    assert agent._resolve_completions() is completions
    await agent.aclose()


def test_converter_preserves_plain_string_assistant_turns():
    """A chat-style history passed to run() must not lose assistant context."""
    messages = convert_n2_items_to_completion_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "continue"},
        ]
    )
    assert {"role": "assistant", "content": "earlier answer"} in messages


@pytest.mark.asyncio
async def test_completion_request_is_public_wrapup_surface() -> None:
    """completion_request() returns the actor's exact next request — windowed messages,
    sampling fields, chaining — with optional harness-owned extra messages appended,
    without advancing the loop or mutating the trajectory."""
    agent = N2ComputerAgent(
        computer=FakeComputer(),
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        completions=FakeCompletions([_turn({"content": "Done.", "tool_calls": []})]),
        screenshot_delay=0,
    )
    async for _step in agent.run("task"):
        pass
    before = copy.deepcopy(agent.trajectory)
    nudge = {"role": "user", "content": [{"type": "text", "text": "Stop here. Summarize."}]}
    request = agent.completion_request([nudge])
    assert request["model"] == agent.model
    assert request["tool_set"] == agent.tool_set
    assert request["max_completion_tokens"] == agent.max_completion_tokens
    assert request["messages"][-1] == nudge
    # the trajectory itself renders just before the nudge, exactly as the loop would send it
    assert request["messages"][:-1] == agent._prepare_completion_messages(agent.trajectory)
    assert agent.trajectory == before
    if agent.last_request_id is not None:
        assert request["extra_body"]["prev_request_id"] == agent.last_request_id


def test_n2_computer_protocol_matches_loop_surface() -> None:
    from yutori import navigator
    from yutori.navigator import N2Computer
    from yutori.navigator.n2 import FILE_ACTION_HANDLERS, SHELL_ACTION_HANDLERS

    assert "N2Computer" in navigator.__all__

    declared = {name for name in vars(N2Computer) if not name.startswith("_")}
    gui = {"screenshot", "click", "double_click", "move", "drag", "scroll", "type", "keypress", "wait"}
    current_tools = {"run_bash_command", "read_file", "write_file", "edit_file"}
    assert declared == gui | current_tools

    # The shell/file members are exactly the handlers the current tool set
    # dispatches; grep/glob (legacy file sets) and run_shell_command (hybrid
    # sets) stay outside the protocol on purpose.
    loop_handlers = set(SHELL_ACTION_HANDLERS.values()) | set(FILE_ACTION_HANDLERS.values())
    assert current_tools < loop_handlers
    assert loop_handlers - current_tools == {"run_shell_command", "grep_files", "glob_files"}


def test_reference_adapters_provide_the_n2_computer_surface() -> None:
    from yutori.navigator import N2Computer, ShellFileToolsMixin
    from yutori.navigator.macos import MacOSComputer

    members = {name for name in vars(N2Computer) if not name.startswith("_")}
    missing = {name for name in members if not callable(getattr(MacOSComputer, name, None))}
    assert not missing

    file_tools = {"read_file", "write_file", "edit_file"}
    assert all(callable(getattr(ShellFileToolsMixin, name, None)) for name in file_tools)


def test_latest_tool_set_is_pinned() -> None:
    """Bumping `latest` should be a deliberate edit, so the date is pinned once, here.

    Everywhere else follows the constant. This is the one place that names the value, which
    keeps a bump from silently changing what a caller who pinned nothing is served.
    """
    from yutori.navigator.models import (
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
        TOOL_SET_COMPUTER_USE_LATEST,
    )

    assert TOOL_SET_COMPUTER_USE_20260830 == "computer_use_tools-20260830"
    assert TOOL_SET_COMPUTER_USE_20260825 == "computer_use_tools-20260825"
    assert TOOL_SET_COMPUTER_USE_LATEST == TOOL_SET_COMPUTER_USE_20260830


def test_20260830_has_the_same_capabilities_as_20260825() -> None:
    """The two sets expose the same tools, so they belong to the same capability sets.

    Each set used to be written as "... and whatever LATEST is", so publishing a newer set
    silently moved 20260825 out of them — most damagingly into
    TOOL_SETS_WITH_STANDALONE_SCREENSHOT, a tool it does not serve. Asserting the two are
    equal members everywhere is what stops that from recurring.
    """
    from yutori.navigator import n2_actions
    from yutori.navigator.models import (
        TOOL_SET_COMPUTER_USE_20260825 as OLDER,
    )
    from yutori.navigator.models import (
        TOOL_SET_COMPUTER_USE_20260830 as NEWER,
    )

    capability_sets = {
        name: value
        for name, value in vars(n2_actions).items()
        if name.startswith("TOOL_SETS_") and isinstance(value, frozenset)
    }
    assert capability_sets, "expected the module to define TOOL_SETS_* frozensets"

    differing = {
        name: (OLDER in value, NEWER in value)
        for name, value in capability_sets.items()
        if (OLDER in value) != (NEWER in value)
    }
    assert differing == {}
    assert NEWER in n2_actions.SUPPORTED_N2_TOOL_SETS
    assert NEWER not in n2_actions.TOOL_SETS_WITH_STANDALONE_SCREENSHOT
    assert OLDER not in n2_actions.TOOL_SETS_WITH_STANDALONE_SCREENSHOT
