"""Tests for the n2 loop's evaluation-harness policies (``N2LoopOptions.harness()``).

These pin the observation stream the model sees when the SDK loop is configured
like Yutori's evaluation harness: a blind start, one PNG 1280x720 frame per GUI
turn appended to the turn's last tool result, image-less shell turns,
``[i:name]`` batch results, every tool call executed in order, prior-turn
reasoning re-sent as message fields, questions routed to a caller, and run
budgets.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from yutori.navigator import (
    N2_TASK_GUIDELINES,
    TOOL_SET_COMPUTER_USE_LATEST,
    N2ComputerAgent,
    N2LoopOptions,
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
    kwargs.setdefault("options", N2LoopOptions.harness())
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
    messages = convert_n2_items_to_completion_messages(items, reasoning_as_field=True)
    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assistant = messages[1]
    assert assistant["content"] == "on it"
    assert assistant["reasoning"] == "think first"
    assert assistant["reasoning_content"] == "think first"
    assert assistant["tool_calls"][0]["function"]["name"] == "bash"

    # Reasoning that directly precedes a tool call (no visible text) rides on the acting message.
    items_without_text = [items[0], items[1], items[3], items[4]]
    messages = convert_n2_items_to_completion_messages(items_without_text, reasoning_as_field=True)
    assert messages[1]["reasoning_content"] == "think first" and messages[1]["content"] == ""

    # The default keeps the historical separate assistant text message.
    legacy = convert_n2_items_to_completion_messages(items)
    assert legacy[1] == {"role": "assistant", "content": "think first"}


def test_parse_executes_every_call_when_asked():
    message = {"content": "", "tool_calls": [_bash("ls", "b1"), _bash("pwd", "b2")]}
    output = parse_n2_tool_calls(message, 1000, 1000, execute_all=True)
    assert [item["call_id"] for item in output] == ["b1", "b2"]
    assert all(item.get("_computer_actions") for item in output)
    refused = parse_n2_tool_calls(message, 1000, 1000)
    assert "Refused stale parallel tool call" in refused[-1]["output"]


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
    result = await execute_n2_computer_call(
        item, desktop, callbacks=_CallbackDispatcher([]), result_format="harness", capture_screenshot=False
    )
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
    result = await execute_n2_computer_call(
        item, Desktop(), callbacks=_CallbackDispatcher([]), result_format="harness", capture_screenshot=False
    )
    assert result[0]["output"] == (
        "[0:left_click] \n"
        "batch stopped at actions[1] (1:right_click): ERROR: RuntimeError: driver refused right click "
        "(1 completed, 1 skipped)"
    )


async def test_execute_renders_bash_dict_results_with_exit_codes():
    desktop = Desktop()
    desktop.bash_result = {"output": "boom\n", "exit_code": 2}
    item = parse_n2_tool_calls({"content": "", "tool_calls": [_bash("false")]}, 1920, 1080)[-1]
    result = await execute_n2_computer_call(
        item, desktop, callbacks=_CallbackDispatcher([]), result_format="harness", capture_screenshot=False
    )
    assert result[0]["output"] == "Exit code 2\nboom\n"

    desktop.bash_result = {"output": "", "exit_code": 0}
    result = await execute_n2_computer_call(
        item, desktop, callbacks=_CallbackDispatcher([]), result_format="harness", capture_screenshot=False
    )
    assert result[0]["output"] == "(Bash completed with no output)"

    # A plain-string result is passed through with the harness's 30k cap.
    desktop.bash_result = "x" * 30_010
    result = await execute_n2_computer_call(
        item, desktop, callbacks=_CallbackDispatcher([]), result_format="harness", capture_screenshot=False
    )
    assert result[0]["output"].endswith("\n\n[... output truncated, 10 more chars ...]")


# ---------------------------------------------------------------------------
# The loop under harness options
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
    agent = _agent(desktop, completions)
    steps = [step async for step in agent.run("open the terminal")]

    # Turn 1: blind start — system prompt first, the task alone, no image, harness budgets.
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

    # Turn 4: a bash-only turn adds no frame, and the run ends on the trained marker.
    fourth = completions.requests[3]
    assert fourth["messages"][-1] == {"role": "tool", "tool_call_id": "b2", "content": "hello\n"}
    # Only two image-bearing tool messages exist, so nothing was pruned yet.
    assert sum(1 for message in fourth["messages"] if _images(message)) == 2
    assert desktop.calls.count(("screenshot",)) == 2
    assert agent.stopped_by == "done"
    assert agent.last_usage == {"prompt_tokens": 9}
    final = steps[-1]["output"][-1]["content"][0]["text"]
    assert final == "All set."


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


async def test_questions_are_routed_to_the_caller_until_a_marker_or_the_cap():
    completions = FakeCompletions(
        [
            {"content": "Which folder should I use?", "tool_calls": []},
            {"content": "And which file name?", "tool_calls": []},
            {"content": "Understood, stopping here.", "tool_calls": []},
        ]
    )
    asked: list[str] = []

    async def answer(question: str):
        asked.append(question)
        return "Use ~/Documents" if len(asked) == 1 else None

    agent = _agent(Desktop(), completions, on_question=answer)
    steps = [step async for step in agent.run("save the report")]

    assert asked == ["Which folder should I use?", "And which file name?"]
    second = completions.requests[1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "Which folder should I use?"}
    assert second[-1] == {"role": "user", "content": "Use ~/Documents"}
    # The unanswered second question ended the run as a final answer.
    assert len(completions.requests) == 2
    assert agent.stopped_by == "final_answer"
    assert steps[-1]["output"][-1]["content"][0]["text"] == "And which file name?"


async def test_question_cap_and_terminal_markers_bypass_the_caller():
    asked: list[str] = []

    async def always_answer(question: str):
        asked.append(question)
        return "go on"

    completions = FakeCompletions([{"content": "q1", "tool_calls": []}, {"content": "q2", "tool_calls": []}])
    agent = _agent(
        Desktop(), completions, on_question=always_answer, options=N2LoopOptions.harness(max_consecutive_questions=1)
    )
    async for _ in agent.run("task"):
        pass
    assert asked == ["q1"] and agent.stopped_by == "final_answer"

    completions = FakeCompletions([{"content": "Cannot do this [INFEASIBLE]", "tool_calls": []}])
    agent = _agent(Desktop(), completions, on_question=always_answer)
    async for _ in agent.run("task"):
        pass
    assert asked == ["q1"] and agent.stopped_by == "infeasible"


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
    assert third[1] == {"role": "user", "content": "task"}
    assert third[2]["content"].startswith("<working_checkpoint>")
    assert len(third) == 3


async def test_default_options_keep_the_established_behaviour():
    completions = FakeCompletions(
        [
            {"content": "", "tool_calls": [_bash("ls", "b1"), _bash("pwd", "b2")]},
            {"content": "done", "tool_calls": []},
        ]
    )
    desktop = Desktop()
    desktop.bash_result = "hello"
    agent = N2ComputerAgent(computer=desktop, completions=completions, screenshot_delay=0)
    async for _ in agent.run("task"):
        pass
    first = completions.requests[0]
    assert first["messages"][-1]["content"][1] == {"type": "text", "text": "Current desktop screen"}
    assert first["max_completion_tokens"] == 16384
    assert "system" not in {message["role"] for message in first["messages"]}
    assert [call[0] for call in desktop.calls].count("bash") == 1  # second call refused, not executed
    second = completions.requests[1]["messages"]
    assert any("Refused stale parallel tool call" in str(message.get("content")) for message in second)
    assert agent.stopped_by == "final_answer"
