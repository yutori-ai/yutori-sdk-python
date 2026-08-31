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
    TOOL_CALL_FORMAT_NUDGE,
    TOOL_SET_COMPUTER_USE_LATEST,
    N2ComputerAgent,
    convert_n2_items_to_completion_messages,
    execute_n2_computer_call,
    parse_n2_tool_calls,
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
        self.bash_result: Any = "hello\n"

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
        return {
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 5 + len(self.requests)},
            "request_id": f"req-{len(self.requests)}",
        }


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
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "on it"}],
            "reasoning": "think first",
        },
        {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"command":"ls"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "file.txt"},
    ]
    messages = convert_n2_items_to_completion_messages(items)
    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assistant = messages[1]
    assert assistant["content"] == "on it"
    assert assistant["reasoning"] == assistant["reasoning_content"] == "think first"
    assert assistant["tool_calls"][0]["function"]["name"] == "bash"

    # A caller-supplied standalone reasoning item stays a plain assistant text message.
    legacy = [
        {"role": "user", "content": "task"},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "old shape"}]},
    ]
    assert convert_n2_items_to_completion_messages(legacy)[1] == {"role": "assistant", "content": "old shape"}


def test_parse_executes_every_call():
    message = {"content": "", "tool_calls": [_bash("ls", "b1"), _bash("pwd", "b2")]}
    output = parse_n2_tool_calls(message, 1000, 1000)
    assert [item["call_id"] for item in output] == ["b1", "b2"]
    assert all(item.get("_computer_actions") for item in output)


async def test_execute_returns_batch_member_lines_with_one_frame():
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
    result = await execute_n2_computer_call(item, desktop, callbacks=_CallbackDispatcher([]), screenshot_delay=0)
    output = result[0]["output"]
    # The batch tool's contract: member lines plus one frame captured after the batch.
    assert output["type"] == "input_image"
    assert (
        output["result"] == "[0:left_click] \n[1:type] \n[2:screenshot] screenshot queued (delivered after the batch)"
    )
    assert desktop.calls.count(("screenshot",)) == 1


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
    result = await execute_n2_computer_call(item, Desktop(), callbacks=_CallbackDispatcher([]), screenshot_delay=0)
    # A halted batch still returns its frame: completed members changed the screen.
    assert result[0]["output"]["result"] == (
        "[0:left_click] \n"
        "batch stopped at actions[1] (1:right_click): ERROR: RuntimeError: driver refused right click "
        "(1 completed, 1 skipped)"
    )


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
    agent = _agent(desktop, completions, system_prompt="Ask questions as text; end with [DONE].")
    steps = [step async for step in agent.run("open the terminal")]

    # Turn 1: no frame — the system prompt, then the task alone; default budgets.
    first = completions.requests[0]
    assert first["messages"][0] == {"role": "system", "content": "Ask questions as text; end with [DONE]."}
    assert first["messages"][1] == {"role": "user", "content": [{"type": "text", "text": "open the terminal"}]}
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
    # The capture's own size, re-encoded to the default WebP.
    assert frame.startswith("data:image/webp;base64,")
    with Image.open(io.BytesIO(base64.b64decode(frame.split(",", 1)[1]))) as image:
        assert image.size == (1920, 1080) and image.format == "WEBP"

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
    assert bash_result["content"] == [{"type": "text", "text": "hello"}] and not _images(bash_result)
    assert batch_result["content"][0]["text"] == "[0:left_click]" and len(_images(batch_result)) == 1

    # Turn 4: a bash-only turn adds no frame, and the text-only answer ends the run.
    fourth = completions.requests[3]
    assert fourth["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "b2",
        "content": [{"type": "text", "text": "hello"}],
    }
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
    # The marker concatenates into the preceding text part, one merged block —
    # the reference builder's rendering of a pruned frame.
    assert tool_messages[0]["content"] == [{"type": "text", "text": "[0:left_click][older image omitted]"}]


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
    assert second[-1] == {"role": "user", "content": [{"type": "text", "text": "Use ~/Documents"}]}
    assert agent.stopped_by == "final_answer"
    assert steps[-1]["output"][-1]["content"][0]["text"] == "Saved it. [DONE]"
    # The trajectory holds the whole conversation, including the resumed part.
    kinds = [item.get("role") or item.get("type") for item in agent.trajectory]
    assert kinds == ["user", "assistant", "user", "function_call", "function_call_output", "assistant"]
    # The caller's own convention (here: a [DONE] marker) stays in the untouched text.
    assert "[DONE]" in agent.trajectory[-1]["content"][0]["text"]


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
    assert third[0] == {"role": "user", "content": [{"type": "text", "text": "task"}]}
    assert third[1]["content"][0]["text"].startswith("<working_checkpoint>")
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
    # compactor=None: this test pins the guard itself; the default "auto"
    # compactor would intercept at these token counts before the guard fires.
    agent = _agent(Desktop(), completions, compactor=None)  # defaults: 128k window, 20480 output, 4096 margin
    async for _ in agent.run("task"):
        pass
    assert len(completions.requests) == 1
    assert agent.stopped_by == "context_limit"

    # `None` disables the guard: the loop keeps calling.
    completions = BigContextCompletions(list(turns) + [{"content": "done", "tool_calls": []}])
    agent = _agent(Desktop(), completions, context_window_tokens=None, compactor=None)
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
    assert completions.requests[1]["messages"][-1]["content"] == [
        {"type": "text", "text": "ERROR_TIMEOUT: b1 timed out after 0.05 seconds"}
    ]


async def test_a_read_result_may_carry_its_own_image():
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
    # The batch result carries its own frame; the read result carries the file's image.
    (frame_url,) = _images(tool_messages[0])
    assert _decode(frame_url).size == (1920, 1080)
    assert tool_messages[1]["content"][0] == {"type": "text", "text": "[image: /tmp/a.png]"}
    (read_url,) = _images(tool_messages[1])
    shown = _decode(read_url)
    assert shown.size == (400, 400) and shown.getpixel((0, 0))[0] > 150  # never resized


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
    await execute_n2_computer_call(items[-1], desktop, callbacks=_CallbackDispatcher({}), screenshot_delay=0)
    assert desktop.calls[0] == ("keypress", ("ctrl", "shift", "t"), {"action": "key_press", "key": "ctrl+shift+t"})

    # Handlers without the parameter are called exactly as before.
    plain = Desktop()
    await execute_n2_computer_call(items[-1], plain, callbacks=_CallbackDispatcher({}), screenshot_delay=0)
    assert plain.calls[0] == ("keypress", ("ctrl", "shift", "t"))


async def test_a_leaked_tool_call_format_gets_one_ephemeral_nudge_retry():
    malformed = "Let me click.\n<tool_call>\n<function=computer_batch>\n</function>\n</tool_call>"
    completions = FakeCompletions(
        [
            {"content": malformed, "tool_calls": []},
            {"content": "", "tool_calls": [_batch({"name": "screenshot", "arguments": {}})]},
            {"content": malformed, "tool_calls": []},
            {"content": malformed, "tool_calls": []},
        ]
    )
    agent = _agent(Desktop(), completions)
    steps = [step async for step in agent.run("task")]

    # Retry request = the same conversation plus the malformed attempt and the reminder.
    retry = completions.requests[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": malformed}
    assert retry[-1] == {"role": "user", "content": [{"type": "text", "text": TOOL_CALL_FORMAT_NUDGE}]}
    # Neither entered the kept trajectory or the next request.
    assert all(TOOL_CALL_FORMAT_NUDGE not in str(item) for item in agent.trajectory)
    assert all(message.get("content") != malformed for message in completions.requests[2]["messages"])

    # A second malformed answer on the same turn is NOT retried again: the run ends with it.
    assert len(completions.requests) == 4
    assert agent.stopped_by == "final_answer"
    assert steps[-1]["output"][-1]["content"][0]["text"] == malformed


async def test_completion_kwargs_are_merged_into_every_request():
    completions = FakeCompletions([{"content": "done", "tool_calls": []}])
    agent = _agent(Desktop(), completions, completion_kwargs={"top_p": 0.95, "presence_penalty": 0.0})
    async for _ in agent.run("task"):
        pass
    assert completions.requests[0]["top_p"] == 0.95
    assert completions.requests[0]["presence_penalty"] == 0.0


async def test_a_failed_turn_frame_reports_instead_of_killing_the_run():
    class FlakyDesktop(Desktop):
        async def screenshot(self):
            self.calls.append(("screenshot",))
            raise RuntimeError("capture failed")

    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_batch({"name": "left_click", "arguments": {"coordinates": [500, 500]}})]},
            {"content": "done", "tool_calls": []},
        ]
    )
    desktop = FlakyDesktop()
    desktop.width, desktop.height = 1920, 1080
    agent = _agent(desktop, completions)
    async for _ in agent.run("task"):
        pass
    assert agent.stopped_by == "final_answer"
    tool_message = [m for m in completions.requests[1]["messages"] if m["role"] == "tool"][-1]
    assert tool_message["content"][0]["text"].endswith("[ERROR] Post-action screenshot failed: capture failed")


def test_a_legacy_reasoning_item_between_turns_stays_its_own_message():
    items = [
        {"role": "user", "content": "task"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "earlier turn"}]},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "new think"}]},
        {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"command":"ls"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
    ]
    messages = convert_n2_items_to_completion_messages(items)
    # The legacy standalone item becomes assistant text of its own turn; the call folds into it.
    assert [m["role"] for m in messages] == ["user", "assistant", "assistant", "tool"]
    assert messages[1] == {"role": "assistant", "content": "earlier turn"}
    assert messages[2]["content"] == "new think" and messages[2]["tool_calls"]
    assert "reasoning_content" not in messages[2]


async def test_requests_chain_via_prev_request_id():
    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_bash("ls", "b1")]},
            {"content": "Which folder?", "tool_calls": []},
            {"content": "done", "tool_calls": []},
        ]
    )
    agent = _agent(Desktop(), completions)
    async for _ in agent.run("task"):
        pass
    # The first call starts the chain; every later call echoes the previous request_id.
    assert "extra_body" not in completions.requests[0]
    assert completions.requests[1]["extra_body"] == {"prev_request_id": "req-1"}

    # resume() continues the same conversation.
    async for _ in agent.resume("use ~/Documents"):
        pass
    assert completions.requests[2]["extra_body"] == {"prev_request_id": "req-2"}

    # A new run() starts a fresh chain.
    completions._turns = [{"content": "done", "tool_calls": []}]
    async for _ in agent.run("other task"):
        pass
    assert "extra_body" not in completions.requests[3]


async def test_a_length_cut_turn_is_re_requested_once():
    class LengthCutCompletions(FakeCompletions):
        async def create(self, **kwargs):
            response = await super().create(**kwargs)
            if len(self.requests) == 1:
                response["choices"][0]["finish_reason"] = "length"
            return response

    completions = LengthCutCompletions(
        [
            {"content": "I will now open the ter", "tool_calls": []},  # truncated fragment
            {"content": "done", "tool_calls": []},
        ]
    )
    agent = _agent(Desktop(), completions)
    steps = [step async for step in agent.run("task")]
    # The fragment never entered the run; the retry's answer is the final one.
    assert len(completions.requests) == 2
    assert completions.requests[1]["messages"] == completions.requests[0]["messages"]
    assert agent.stopped_by == "final_answer"
    assert steps[-1]["output"][-1]["content"][0]["text"] == "done"


async def test_compaction_result_commits_the_trajectory_immediately():
    from yutori.navigator.n2_compaction import N2CompactionResult

    class ResultCompactor:
        async def compact(self, items, *, last_usage, completions, model, tool_set):
            if last_usage.get("prompt_tokens", 0) < 7:
                return None
            rewritten = [items[0], {"role": "user", "content": [{"type": "text", "text": "checkpoint"}]}]
            return N2CompactionResult(
                items=rewritten, removed_item_count=len(items) - 1, retained_item_count=1, request_id="c-1"
            )

    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_bash("ls", "b1")]},
            {"content": "", "tool_calls": [_bash("pwd", "b2")]},
            {"content": "done", "tool_calls": []},
        ]
    )
    agent = _agent(Desktop(), completions, compactor=ResultCompactor())
    async for step in agent.run("task"):
        if step.get("message") and len(completions.requests) == 3:
            break  # abandon the generator right after compaction's next turn
    assert agent.trajectory[1]["content"] == [{"type": "text", "text": "checkpoint"}]
    assert agent.last_request_id in ("c-1", "req-3")


async def test_result_backstop_is_length_only_and_never_touches_images():
    big = "x" * (256 * 1024 + 100)
    marked = "y" * 1000 + "\n\n[... output truncated, 999 more chars ...]"
    file_image = "data:image/png;base64," + _png_b64(8, 8)

    class BigDesktop(Desktop):
        async def read_file(self, file_path, offset, limit):
            return {"text": big, "image_url": file_image}

    desktop = BigDesktop()
    desktop.bash_result = marked
    items = parse_n2_tool_calls(
        {"content": "", "tool_calls": [_bash("ok", "b1"), _read("/tmp/a.png", "r1")]}, 1920, 1080
    )
    bash_out = await execute_n2_computer_call(items[0], desktop, callbacks=_CallbackDispatcher({}))
    read_out = await execute_n2_computer_call(items[1], desktop, callbacks=_CallbackDispatcher({}))
    # Under the cap: untouched, whatever markers it carries.
    assert bash_out[0]["output"] == [marked] or bash_out[0]["output"] == marked
    # Over the cap: cut by length alone; the image payload is byte-identical.
    out = read_out[0]["output"]
    assert out["image_url"] == file_image
    assert out["result"].startswith("x" * 1000)
    assert out["result"].endswith("[... output truncated, 100 more chars ...]")


async def test_breaking_at_the_model_turn_keeps_that_turn_in_the_trajectory():
    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_bash("ls", "b1")]},
            {"content": "done", "tool_calls": []},
        ]
    )
    agent = _agent(Desktop(), completions)
    async for step in agent.run("task"):
        break  # abandon at the first yielded model turn
    kinds = [item.get("type") or item.get("role") for item in agent.trajectory]
    assert kinds == ["user", "function_call"]  # the yielded turn is already committed
