"""Tests for the n2 loop's observation policies and budgets.

These pin the stream the model sees: a start without a frame, one PNG 1280x720
frame per GUI turn appended to the turn's last tool result, image-less shell
turns, ``[i:name]`` batch results, every tool call executed in order, prior-turn
reasoning re-sent as message fields, resumable text-only turns, and run budgets.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import pytest
from PIL import Image

from yutori.navigator import (
    N2_TASK_GUIDELINES,
    TOOL_SET_COMPUTER_USE_LATEST,
    N2ComputerAgent,
    convert_n2_items_to_completion_messages,
    execute_n2_computer_call,
    parse_n2_tool_calls,
    parse_terminal_marker,
)
from yutori.navigator.n2 import _CallbackDispatcher


def _png_b64(width: int = 1920, height: int = 1080) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class Desktop:
    """A bash-capable desktop that reports its size and records every call."""

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width, self.height = width, height
        self.calls: list[tuple] = []
        self.bash_result: Any = {"output": "hello\n", "exit_code": 0}

    async def get_dimensions(self):
        return self.width, self.height

    async def screenshot(self):
        self.calls.append(("screenshot",))
        return _png_b64(self.width, self.height)

    async def click(self, x, y, button="left", count=1, modifier=None):
        if button == "right":
            raise RuntimeError("driver refused right click")
        self.calls.append(("click", x, y, button))

    async def type(self, text):
        self.calls.append(("type", text))

    async def keypress(self, keys):
        self.calls.append(("keypress", tuple(keys)))

    async def wait(self, ms):
        self.calls.append(("wait", ms))

    async def run_bash_command(self, command, timeout, run_in_background):
        self.calls.append(("bash", command))
        return self.bash_result

    async def read_file(self, file_path, offset, limit):
        self.calls.append(("read", file_path))
        return "     1\tcontents"


class FakeCompletions:
    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        message = self._turns.pop(0)
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 5 + len(self.requests)}}


def _batch(*members: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "function": {"name": "computer_batch", "arguments": json.dumps({"actions": list(members)})},
    }


def _bash(command: str, call_id: str = "b1") -> dict[str, Any]:
    return {"id": call_id, "function": {"name": "bash", "arguments": json.dumps({"command": command})}}


def _images(message: dict[str, Any]) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [part["image_url"]["url"] for part in content if part.get("type") == "image_url"]


def _agent(computer, completions, **kwargs) -> N2ComputerAgent:
    kwargs.setdefault("screenshot_delay", 0)
    return N2ComputerAgent(computer=computer, completions=completions, **kwargs)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def test_reasoning_is_resent_as_assistant_message_fields():
    items = [
        {"role": "user", "content": "task"},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think first"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "on it"}]},
        {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"command":"ls"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "file.txt"},
    ]
    messages = convert_n2_items_to_completion_messages(items)
    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assistant = messages[1]
    assert assistant["content"] == "on it"
    assert assistant["reasoning"] == "think first"
    assert assistant["reasoning_content"] == "think first"
    assert assistant["tool_calls"][0]["function"]["name"] == "bash"

    # Reasoning that directly precedes a tool call (no visible text) rides on the acting message.
    items_without_text = [items[0], items[1], items[3], items[4]]
    messages = convert_n2_items_to_completion_messages(items_without_text)
    assert messages[1]["reasoning_content"] == "think first" and messages[1]["content"] == ""


def test_parse_executes_every_call():
    message = {"content": "", "tool_calls": [_bash("ls", "b1"), _bash("pwd", "b2")]}
    output = parse_n2_tool_calls(message, 1000, 1000)
    assert [item["call_id"] for item in output] == ["b1", "b2"]
    assert all(item.get("_computer_actions") for item in output)


async def test_execute_renders_harness_batch_text_without_a_frame():
    item = parse_n2_tool_calls(
        {
            "content": "",
            "tool_calls": [
                _batch(
                    {"name": "left_click", "arguments": {"coordinates": [500, 500]}},
                    {"name": "type", "arguments": {"text": "hi"}},
                    {"name": "screenshot", "arguments": {}},
                )
            ],
        },
        1920,
        1080,
    )[-1]
    desktop = Desktop()
    result = await execute_n2_computer_call(item, desktop, callbacks=_CallbackDispatcher([]))
    assert result == [
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": "[0:left_click] \n[1:type] \n[2:screenshot] screenshot queued (delivered after the batch)",
        }
    ]
    assert ("screenshot",) not in desktop.calls


async def test_execute_reports_a_halted_batch_the_harness_way():
    item = parse_n2_tool_calls(
        {
            "content": "",
            "tool_calls": [
                _batch(
                    {"name": "left_click", "arguments": {"coordinates": [1, 1]}},
                    {"name": "right_click", "arguments": {"coordinates": [1, 1]}},
                    {"name": "type", "arguments": {"text": "never"}},
                )
            ],
        },
        1920,
        1080,
    )[-1]
    result = await execute_n2_computer_call(item, Desktop(), callbacks=_CallbackDispatcher([]))
    assert result[0]["output"] == (
        "[0:left_click] \n"
        "batch stopped at actions[1] (1:right_click): ERROR: RuntimeError: driver refused right click "
        "(1 completed, 1 skipped)"
    )


async def test_execute_renders_bash_dict_results_with_exit_codes():
    desktop = Desktop()
    desktop.bash_result = {"output": "boom\n", "exit_code": 2}
    item = parse_n2_tool_calls({"content": "", "tool_calls": [_bash("false")]}, 1920, 1080)[-1]
    result = await execute_n2_computer_call(item, desktop, callbacks=_CallbackDispatcher([]))
    assert result[0]["output"] == "Exit code 2\nboom\n"

    desktop.bash_result = {"output": "", "exit_code": 0}
    result = await execute_n2_computer_call(item, desktop, callbacks=_CallbackDispatcher([]))
    assert result[0]["output"] == "(Bash completed with no output)"

    # A plain-string result is passed through with the harness's 30k cap.
    desktop.bash_result = "x" * 30_010
    result = await execute_n2_computer_call(item, desktop, callbacks=_CallbackDispatcher([]))
    assert result[0]["output"].endswith("\n\n[... output truncated, 10 more chars ...]")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def test_harness_loop_starts_blind_and_attaches_one_frame_per_gui_turn():
    completions = FakeCompletions(
        [
            {
                "content": "Looking.",
                "reasoning_content": "I need to see the screen.",
                "tool_calls": [_batch({"name": "screenshot", "arguments": {}})],
            },
            {
                "content": "",
                "tool_calls": [
                    _bash("ls", "b1"),
                    _batch({"name": "left_click", "arguments": {"coordinates": [500, 500]}}),
                ],
            },
            {"content": "", "tool_calls": [_bash("pwd", "b2")]},
            {"content": "All set. [DONE]", "tool_calls": []},
        ]
    )
    desktop = Desktop()
    agent = _agent(desktop, completions, system_prompt=N2_TASK_GUIDELINES)
    steps = [step async for step in agent.run("open the terminal")]

    # Turn 1: no frame — the system prompt, then the task alone; default budgets.
    first = completions.requests[0]
    assert first["messages"][0] == {"role": "system", "content": N2_TASK_GUIDELINES}
    assert first["messages"][1] == {"role": "user", "content": "open the terminal"}
    assert not any(_images(message) for message in first["messages"])
    assert first["max_completion_tokens"] == 20480
    assert first["tool_set"] == TOOL_SET_COMPUTER_USE_LATEST
    assert desktop.calls[0] == ("screenshot",)  # taken after the batch, not before the turn

    # Turn 2: the batch's result carries the frame; reasoning rides on the assistant message.
    second = completions.requests[1]
    assistant = second["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "I need to see the screen." and assistant["reasoning"]
    assert assistant["content"] == "Looking."
    tool = second["messages"][3]
    assert tool["role"] == "tool" and tool["tool_call_id"] == "c1"
    assert tool["content"][0] == {
        "type": "text",
        "text": "[0:screenshot] screenshot queued (delivered after the batch)",
    }
    (frame,) = _images(tool)
    assert frame.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(frame.split(",", 1)[1]))) as image:
        assert image.size == (1280, 720) and image.format == "PNG"

    # Turn 3: both calls of turn 2 ran, in order; the frame went to the LAST tool result (the batch).
    assert [call for call in desktop.calls if call[0] in {"bash", "click"}] == [
        ("bash", "ls"),
        ("click", 960, 540, "left"),
        ("bash", "pwd"),
    ]
    third = completions.requests[2]
    tool_messages = [message for message in third["messages"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["c1", "b1", "c1"]
    bash_result, batch_result = tool_messages[1], tool_messages[2]
    assert bash_result["content"] == "hello\n" and not _images(bash_result)
    assert batch_result["content"][0]["text"] == "[0:left_click]" and len(_images(batch_result)) == 1

    # Turn 4: a bash-only turn adds no frame, and the text-only answer ends the run.
    fourth = completions.requests[3]
    assert fourth["messages"][-1] == {"role": "tool", "tool_call_id": "b2", "content": "hello\n"}
    # Only two image-bearing tool messages exist, so nothing was pruned yet.
    assert sum(1 for message in fourth["messages"] if _images(message)) == 2
    assert desktop.calls.count(("screenshot",)) == 2
    assert agent.stopped_by == "final_answer"
    assert agent.last_usage == {"prompt_tokens": 9}
    final = steps[-1]["output"][-1]["content"][0]["text"]
    assert final == "All set. [DONE]"


async def test_pruned_frames_leave_the_harness_marker_in_place():
    click = _batch({"name": "left_click", "arguments": {"coordinates": [500, 500]}})
    completions = FakeCompletions(
        [{"content": "", "tool_calls": [click]} for _ in range(3)] + [{"content": "done [DONE]", "tool_calls": []}]
    )
    agent = _agent(Desktop(), completions)
    async for _ in agent.run("task"):
        pass
    last = completions.requests[-1]["messages"]
    tool_messages = [message for message in last if message["role"] == "tool"]
    assert [len(_images(message)) for message in tool_messages] == [0, 1, 1]
    assert tool_messages[0]["content"] == [
        {"type": "text", "text": "[0:left_click]"},
        {"type": "text", "text": "[older image omitted]"},
    ]


async def test_harness_loop_sizes_a_blind_start_from_the_handler_dimensions():
    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_batch({"name": "left_click", "arguments": {"coordinates": [1000, 0]}})]},
            {"content": "done [DONE]", "tool_calls": []},
        ]
    )
    desktop = Desktop(width=2560, height=1440)
    agent = _agent(desktop, completions)
    async for _ in agent.run("task"):
        pass
    assert ("click", 2559, 0, "left") in desktop.calls


async def test_a_text_only_turn_ends_the_run_and_resume_continues_the_same_conversation():
    completions = FakeCompletions(
        [
            {"content": "Which folder should I use?", "tool_calls": []},
            {"content": "", "tool_calls": [_bash("ls ~/Documents", "b1")]},
            {"content": "Saved it. [DONE]", "tool_calls": []},
        ]
    )
    agent = _agent(Desktop(), completions)
    steps = [step async for step in agent.run("save the report")]
    # The loop does not interpret the text: no tool calls means the run ends, text untouched.
    assert agent.stopped_by == "final_answer"
    assert steps[-1]["output"][-1]["content"][0]["text"] == "Which folder should I use?"
    assert len(completions.requests) == 1

    steps = [step async for step in agent.resume("Use ~/Documents")]
    second = completions.requests[1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "Which folder should I use?"}
    assert second[-1] == {"role": "user", "content": "Use ~/Documents"}
    assert agent.stopped_by == "final_answer"
    assert steps[-1]["output"][-1]["content"][0]["text"] == "Saved it. [DONE]"
    # The trajectory holds the whole conversation, including the resumed part.
    kinds = [item.get("role") or item.get("type") for item in agent.trajectory]
    assert kinds == ["user", "assistant", "user", "function_call", "function_call_output", "assistant"]
    assert parse_terminal_marker(agent.trajectory[-1]["content"][0]["text"]) == "done"


async def test_resume_requires_a_prior_run():
    agent = _agent(Desktop(), FakeCompletions([]))
    with pytest.raises(RuntimeError):
        async for _ in agent.resume("hello"):
            pass


async def test_step_budget_stops_the_run_and_reports_why():
    turns = [{"content": "", "tool_calls": [_bash("ls", f"b{i}")]} for i in range(5)]
    completions = FakeCompletions(turns)
    agent = _agent(Desktop(), completions, max_steps=3)
    async for _ in agent.run("task"):
        pass
    assert len(completions.requests) == 3
    assert agent.stopped_by == "max_steps"


async def test_compactor_can_rewrite_the_trajectory_before_a_model_call():
    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_bash("ls", "b1")]},
            {"content": "", "tool_calls": [_bash("pwd", "b2")]},
            {"content": "done [DONE]", "tool_calls": []},
        ]
    )

    class Compactor:
        def __init__(self):
            self.seen: list[dict[str, Any]] = []

        async def compact(self, items, *, last_usage, completions, model, tool_set):
            self.seen.append(last_usage)
            if last_usage.get("prompt_tokens", 0) < 7:
                return None
            return [items[0], {"role": "user", "content": "<working_checkpoint>listed files</working_checkpoint>"}]

    compactor = Compactor()
    agent = _agent(Desktop(), completions, compactor=compactor)
    async for _ in agent.run("task"):
        pass
    assert compactor.seen == [{}, {"prompt_tokens": 6}, {"prompt_tokens": 7}]
    third = completions.requests[2]["messages"]
    assert third[0] == {"role": "user", "content": "task"}
    assert third[1]["content"].startswith("<working_checkpoint>")
    assert len(third) == 2


# ---------------------------------------------------------------------------
# Gaps found by the mirror-harness parity study
# ---------------------------------------------------------------------------


def _read(file_path: str, call_id: str = "r1") -> dict[str, Any]:
    return {"id": call_id, "function": {"name": "read", "arguments": json.dumps({"file_path": file_path})}}


async def test_requests_carry_a_long_api_timeout_and_the_five_second_wait_default():
    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_batch({"name": "wait", "arguments": {}})]},
            {"content": "[DONE]", "tool_calls": []},
        ]
    )
    desktop = Desktop()
    async for _ in _agent(desktop, completions).run("task"):
        pass
    # A thinking n2 turn can take minutes: the SDK client's 30 s default would end the run.
    assert completions.requests[0]["timeout"] == 600.0
    # The harness converter waits 5 s when the model gives no duration.
    assert ("wait", 5000) in desktop.calls

    # `None` leaves the client's own timeout in charge.
    completions = FakeCompletions([{"content": "[DONE]", "tool_calls": []}])
    async for _ in _agent(Desktop(), completions, api_timeout_seconds=None).run("task"):
        pass
    assert "timeout" not in completions.requests[0]


async def test_context_guard_ends_the_run_before_an_oversized_request():
    class BigContextCompletions(FakeCompletions):
        async def create(self, **kwargs):
            response = await super().create(**kwargs)
            response["usage"] = {"prompt_tokens": 110_000}
            return response

    turns = [{"content": "", "tool_calls": [_bash("ls", f"b{i}")]} for i in range(3)]
    completions = BigContextCompletions(turns)
    agent = _agent(Desktop(), completions)  # defaults: 128k window, 20480 output, 4096 margin
    async for _ in agent.run("task"):
        pass
    assert len(completions.requests) == 1
    assert agent.stopped_by == "context_limit"

    # `None` disables the guard: the loop keeps calling.
    completions = BigContextCompletions(list(turns) + [{"content": "done", "tool_calls": []}])
    agent = _agent(Desktop(), completions, context_window_tokens=None)
    async for _ in agent.run("task"):
        pass
    assert len(completions.requests) == 4 and agent.stopped_by == "final_answer"


async def test_a_slow_tool_call_reports_the_harness_timeout_text():
    import asyncio

    class SlowDesktop(Desktop):
        async def run_bash_command(self, command, timeout, run_in_background):
            await asyncio.sleep(5)
            return {"output": "late", "exit_code": 0}

    completions = FakeCompletions(
        [{"content": "", "tool_calls": [_bash("sleep 5", "b1")]}, {"content": "[DONE]", "tool_calls": []}]
    )
    agent = _agent(SlowDesktop(), completions, tool_call_timeout_seconds=0.05)
    steps = [step async for step in agent.run("task")]
    outputs = [item for step in steps for item in step["output"] if item.get("type") == "function_call_output"]
    assert outputs[0]["output"] == "ERROR_TIMEOUT: b1 timed out after 0.05 seconds"
    assert completions.requests[1]["messages"][-1]["content"] == "ERROR_TIMEOUT: b1 timed out after 0.05 seconds"


async def test_a_read_result_may_carry_an_image_and_keeps_the_turn_frame():
    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), (200, 0, 0)).save(buffer, format="PNG")  # a square, red file image
    file_image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    def _decode(url: str) -> Image.Image:
        return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))

    class ImageDesktop(Desktop):
        async def read_file(self, file_path, offset, limit):
            if file_path.endswith(".png"):
                return {"text": f"[image: {file_path}]", "image_url": file_image}
            return await super().read_file(file_path, offset, limit)

    completions = FakeCompletions(
        [
            {
                "content": "",
                "tool_calls": [
                    _batch({"name": "left_click", "arguments": {"coordinates": [500, 500]}}),
                    _read("/tmp/a.png"),
                ],
            },
            {"content": "[DONE]", "tool_calls": []},
        ]
    )
    async for _ in _agent(ImageDesktop(), completions).run("task"):
        pass
    tool_messages = [m for m in completions.requests[1]["messages"] if m["role"] == "tool"]
    assert _images(tool_messages[0]) == []  # the batch result stays text
    read_images = _images(tool_messages[1])
    assert len(read_images) == 2  # file image first, then the turn's frame
    assert tool_messages[1]["content"][0] == {"type": "text", "text": "[image: /tmp/a.png]"}
    file_shown, frame = _decode(read_images[0]), _decode(read_images[1])
    assert file_shown.size == (400, 400) and file_shown.getpixel((0, 0))[0] > 150  # kept its aspect, not stretched
    assert frame.size == (1280, 720)  # the 16:9 desktop frame is resized exactly


async def test_handlers_that_accept_model_action_receive_the_untranslated_call():
    class RecordingDesktop(Desktop):
        async def keypress(self, keys, model_action=None):
            self.calls.append(("keypress", tuple(keys), model_action))

    desktop = RecordingDesktop()
    items = parse_n2_tool_calls(
        {"content": "", "tool_calls": [_batch({"name": "key_press", "arguments": {"key": "ctrl+shift+t"}})]},
        1920,
        1080,
    )
    await execute_n2_computer_call(items[-1], desktop, callbacks=_CallbackDispatcher({}))
    assert desktop.calls == [("keypress", ("ctrl", "shift", "t"), {"action": "key_press", "key": "ctrl+shift+t"})]

    # Handlers without the parameter are called exactly as before.
    plain = Desktop()
    await execute_n2_computer_call(items[-1], plain, callbacks=_CallbackDispatcher({}))
    assert plain.calls == [("keypress", ("ctrl", "shift", "t"))]


async def test_text_an_adapter_already_truncated_is_not_cut_again():
    desktop = Desktop()
    desktop.bash_result = {"output": "x" * 40 + "\n[... output truncated, 999 more chars ...]", "exit_code": 0}
    items = parse_n2_tool_calls({"content": "", "tool_calls": [_bash("cat big")]}, 1920, 1080)
    result = await execute_n2_computer_call(
        items[-1],
        desktop,
        callbacks=_CallbackDispatcher({}),
        shell_result_max_chars=50,
    )
    assert result[0]["output"] == desktop.bash_result["output"]
