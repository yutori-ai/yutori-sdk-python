"""Praxis-compatible context compaction for the SDK-owned n2 loop."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from yutori.navigator import (
    TOOL_SET_COMPUTER_USE_BASH_BATCH,
    N2CompactionContext,
    N2CompactionResult,
    N2ComputerAgent,
    N2InlineCompactor,
    convert_n2_items_to_completion_messages,
)
from yutori.navigator.macos.types import CancellationLatch


def _response(
    content: str,
    *,
    request_id: str,
    prompt_tokens: int = 1,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content, "tool_calls": tool_calls or []}}],
        "usage": {"prompt_tokens": prompt_tokens},
        "request_id": request_id,
    }


def _checkpoint_response(name: str, *, request_id: str) -> dict[str, Any]:
    return _response(
        f"preamble\n<conversation_compaction_summary>\n## Goal\n{name}\n</conversation_compaction_summary>\ntrailing",
        request_id=request_id,
    )


class QueueCompletions:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(copy.deepcopy(kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _context(previous_request_id: str = "actor-1") -> N2CompactionContext:
    def prepare(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"role": "system", "content": "actor system"}] + convert_n2_items_to_completion_messages(items)

    return N2CompactionContext(
        prepare_messages=prepare,
        request_kwargs={
            "model": "n2",
            "tool_set": TOOL_SET_COMPUTER_USE_BASH_BATCH,
            "max_completion_tokens": 20_480,
            "parallel_tool_calls": True,
            "temperature": 0.6,
            "extra_body": {"caller": "test"},
        },
        await_response=lambda awaitable: awaitable,
        previous_request_id=previous_request_id,
    )


def _tool_turn(turn_id: str, command: str, output: Any = "ok") -> list[dict[str, Any]]:
    return [
        {
            "type": "function_call",
            "call_id": f"call-{turn_id}",
            "name": "bash",
            "arguments": json.dumps({"command": command}),
            "_n2_turn_id": turn_id,
        },
        {
            "type": "function_call_output",
            "call_id": f"call-{turn_id}",
            "output": output,
            "_n2_turn_id": turn_id,
        },
    ]


async def test_inline_compactor_uses_strict_threshold_and_atomically_rewrites_history():
    items = [
        {"role": "user", "content": "persistent instructions"},
        {"role": "user", "content": "do the task"},
        *_tool_turn("one", "ls"),
        *_tool_turn("two", "pwd"),
    ]
    original = copy.deepcopy(items)
    completions = QueueCompletions([_checkpoint_response("finish the task", request_id="compact-1")])
    compactor = N2InlineCompactor(
        trigger_input_tokens=10,
        keep_last_n_turns=1,
        tail_token_budget=10_000,
        retry_delay_seconds=0,
    )

    assert (
        await compactor.compact(
            items,
            last_usage={"prompt_tokens": 10},
            completions=completions,
            model="n2",
            tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
            context=_context(),
        )
        is None
    )
    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 11},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert isinstance(result, N2CompactionResult)
    assert items == original
    assert result.items[:2] == original[:2]
    assert "<working_checkpoint>\n## Goal\nfinish the task" in result.items[2]["content"]
    assert result.items[3:] == original[-2:]
    assert result.removed_item_count == result.retained_item_count == 2
    assert result.request_id == "compact-1"
    assert compactor.compaction_count == 1

    request = completions.requests[0]
    assert [message["role"] for message in request["messages"]] == [
        "system",
        "user",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert request["messages"][-2]["content"] == [{"type": "text", "text": "ok"}]
    assert request["messages"][-1]["content"][0]["text"].startswith("## Internal compaction request")
    assert request["temperature"] == 0.6
    assert request["extra_body"]["prev_request_id"] == "actor-1"

    # The first actor usage after the rewrite is a baseline, even if still large.
    assert (
        await compactor.compact(
            result.items,
            last_usage={"prompt_tokens": 999},
            completions=completions,
            model="n2",
            tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
            context=_context("compact-1"),
        )
        is None
    )


async def test_inline_compactor_retries_unusable_responses_and_chains_attempts():
    tool_call = [{"id": "bad", "function": {"name": "bash", "arguments": '{"command":"ls"}'}}]
    completions = QueueCompletions(
        [
            _response("", request_id="compact-1", tool_calls=tool_call),
            _response("not tagged", request_id="compact-2"),
            _checkpoint_response("valid", request_id="compact-3"),
        ]
    )
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=0,
        max_attempts=3,
        retry_delay_seconds=0,
    )
    items = [{"role": "user", "content": "task"}, *_tool_turn("one", "ls")]

    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert isinstance(result, N2CompactionResult) and result.attempts == 3
    bodies = [request["extra_body"] for request in completions.requests]
    assert [body["yutori_logical_attempt"] for body in bodies] == [1, 2, 3]
    assert len({body["yutori_logical_request_id"] for body in bodies}) == 1
    assert [body["prev_request_id"] for body in bodies] == ["actor-1", "compact-1", "compact-2"]
    assert result.request_id == "compact-3"


async def test_repeated_compaction_updates_one_checkpoint_and_keeps_new_tail():
    completions = QueueCompletions(
        [
            _checkpoint_response("first checkpoint", request_id="compact-1"),
            _checkpoint_response("updated checkpoint", request_id="compact-2"),
        ]
    )
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=1,
        tail_token_budget=10_000,
        retry_delay_seconds=0,
    )
    items = [{"role": "user", "content": "task"}, *_tool_turn("one", "ls"), *_tool_turn("two", "pwd")]

    first = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )
    assert isinstance(first, N2CompactionResult)
    assert (
        await compactor.compact(
            first.items,
            last_usage={"prompt_tokens": 2},
            completions=completions,
            model="n2",
            tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
            context=_context("compact-1"),
        )
        is None
    )
    continued = [*first.items, *_tool_turn("three", "whoami")]
    second = await compactor.compact(
        continued,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context("actor-2"),
    )

    assert isinstance(second, N2CompactionResult)
    checkpoint_items = [item for item in second.items if item.get("_n2_compaction_kind") == "working_checkpoint"]
    assert len(checkpoint_items) == 1
    assert "updated checkpoint" in checkpoint_items[0]["content"]
    assert second.items[-2:] == continued[-2:]
    second_request_messages = completions.requests[1]["messages"]
    second_request_text = "\n".join(str(message.get("content", "")) for message in second_request_messages)
    requested_commands = [
        json.loads(call["function"]["arguments"])["command"]
        for message in second_request_messages
        for call in message.get("tool_calls", [])
    ]
    assert "first checkpoint" in second_request_text
    assert requested_commands == ["pwd"]


async def test_inline_compactor_preserves_history_after_exhausting_failures():
    items = [{"role": "user", "content": "task"}, *_tool_turn("one", "ls")]
    original = copy.deepcopy(items)
    completions = QueueCompletions(
        [RuntimeError("network"), _response("", request_id="two"), _response("bad", request_id="three")]
    )
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=0,
        max_attempts=3,
        retry_delay_seconds=0,
    )

    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert result is None
    assert items == original
    assert compactor.compaction_count == 0
    assert len(completions.requests) == 3


async def test_inline_compactor_preserves_history_when_request_preparation_fails():
    items = [{"role": "user", "content": "task"}, *_tool_turn("one", "ls")]
    completions = QueueCompletions([])
    compactor = N2InlineCompactor(trigger_input_tokens=1, retry_delay_seconds=0)

    def fail_to_prepare(_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise ValueError("request is too large")

    context = N2CompactionContext(
        prepare_messages=fail_to_prepare,
        request_kwargs={"model": "n2"},
        await_response=lambda awaitable: awaitable,
        previous_request_id="actor",
    )
    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=context,
    )

    assert result is None
    assert completions.requests == []


async def test_tail_budget_counts_an_image_as_tokens_not_base64_text():
    image_output = {
        "type": "input_image",
        "image_url": "data:image/png;base64," + "x" * 100_000,
        "result": "screen",
    }
    items = [
        {"role": "user", "content": "task"},
        *_tool_turn("one", "ls"),
        *_tool_turn("two", "screenshot", image_output),
    ]
    completions = QueueCompletions([_checkpoint_response("image retained", request_id="compact")])
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=1,
        tail_token_budget=1_700,
        retry_delay_seconds=0,
    )

    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert isinstance(result, N2CompactionResult)
    assert result.retained_item_count == 2
    assert result.items[-1]["output"]["image_url"].endswith("x" * 100_000)


async def test_empty_tail_restores_latest_image_after_checkpoint():
    older_image = "data:image/png;base64,b2xk"
    latest_image = "data:image/webp;base64,bmV3"
    items = [
        {"role": "user", "content": "task"},
        *_tool_turn(
            "one",
            "screenshot",
            {"type": "input_image", "image_url": older_image, "result": "older"},
        ),
        *_tool_turn(
            "two",
            "screenshot",
            {"type": "input_image", "image_url": latest_image, "result": "latest"},
        ),
    ]
    completions = QueueCompletions([_checkpoint_response("whole window", request_id="compact")])
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=0,
        retry_delay_seconds=0,
    )

    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert isinstance(result, N2CompactionResult)
    assert result.removed_item_count == 4
    assert result.retained_item_count == 0
    assert result.items[-1] == {
        "role": "user",
        "content": [{"type": "input_image", "image_url": latest_image}],
    }


async def test_empty_tail_without_an_image_adds_only_the_checkpoint():
    items = [{"role": "user", "content": "task"}, *_tool_turn("one", "ls")]
    completions = QueueCompletions([_checkpoint_response("no image", request_id="compact")])
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=0,
        retry_delay_seconds=0,
    )

    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert isinstance(result, N2CompactionResult)
    assert len(result.items) == 2
    assert result.items[-1].get("_n2_compaction_kind") == "working_checkpoint"


async def test_private_turn_identity_keeps_interleaved_parallel_calls_together():
    second_turn = [
        {
            "type": "function_call",
            "call_id": "bad",
            "name": "bash",
            "arguments": "{}",
            "_n2_turn_id": "two",
        },
        {
            "type": "function_call_output",
            "call_id": "bad",
            "output": "invalid",
            "_n2_turn_id": "two",
        },
        {
            "type": "function_call",
            "call_id": "good",
            "name": "bash",
            "arguments": '{"command":"pwd"}',
            "_n2_turn_id": "two",
        },
        {
            "type": "function_call_output",
            "call_id": "good",
            "output": "/tmp",
            "_n2_turn_id": "two",
        },
    ]
    items = [{"role": "user", "content": "task"}, *_tool_turn("one", "ls"), *second_turn]
    completions = QueueCompletions([_checkpoint_response("parallel", request_id="compact")])
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=1,
        tail_token_budget=10_000,
        retry_delay_seconds=0,
    )

    result = await compactor.compact(
        items,
        last_usage={"prompt_tokens": 2},
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        context=_context(),
    )

    assert isinstance(result, N2CompactionResult)
    assert result.items[-4:] == second_turn
    assert result.retained_item_count == 4


class Desktop:
    async def get_dimensions(self) -> tuple[int, int]:
        return 200, 100

    async def run_bash_command(self, command: str, timeout: float, run_in_background: bool) -> str:
        return f"ran {command}"


def _bash(command: str, call_id: str = "bash") -> dict[str, Any]:
    return {"id": call_id, "function": {"name": "bash", "arguments": json.dumps({"command": command})}}


async def test_agent_compacts_end_to_end_before_context_guard_and_preserves_request_policy():
    completions = QueueCompletions(
        [
            _response("", request_id="actor-1", prompt_tokens=127_000, tool_calls=[_bash("ls")]),
            _checkpoint_response("listed files", request_id="compact-1"),
            _response("done", request_id="actor-2", prompt_tokens=100),
        ]
    )
    compactor = N2InlineCompactor(
        trigger_input_tokens=53_760,
        keep_last_n_turns=0,
        retry_delay_seconds=0,
    )
    agent = N2ComputerAgent(
        computer=Desktop(),
        completions=completions,
        model="n2",
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        system_prompt="actor system",
        max_completion_tokens=1_234,
        reasoning_effort="medium",
        api_timeout_seconds=77,
        temperature=0.6,
        completion_kwargs={"top_p": 0.8, "extra_body": {"caller": "example"}},
        compactor=compactor,
    )

    steps = [step async for step in agent.run("task")]

    assert steps[-1]["message"]["content"] == "done"
    assert agent.stopped_by == "final_answer"
    assert compactor.compaction_count == 1
    assert len(completions.requests) == 3
    actor_one, compact, actor_two = completions.requests
    request_fields = (
        "model",
        "tool_set",
        "max_completion_tokens",
        "parallel_tool_calls",
        "temperature",
        "reasoning_effort",
        "timeout",
        "top_p",
    )
    for field in request_fields:
        assert compact[field] == actor_one[field]
    assert compact["messages"][0] == actor_one["messages"][0] == {"role": "system", "content": "actor system"}
    assert compact["extra_body"]["prev_request_id"] == "actor-1"
    assert actor_two["extra_body"] == {"caller": "example", "prev_request_id": "compact-1"}
    assert "<working_checkpoint>\n## Goal\nlisted files" in actor_two["messages"][2]["content"][0]["text"]
    assert agent.trajectory[1].get("_n2_compaction_kind") == "working_checkpoint"


async def test_operator_stop_cancels_an_in_flight_compaction_request():
    class HangingCompactionCompletions:
        def __init__(self) -> None:
            self.calls = 0
            self.compaction_started = asyncio.Event()
            self.compaction_cancelled = False

        async def create(self, **_kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return _response("", request_id="actor-1", prompt_tokens=2, tool_calls=[_bash("ls")])
            self.compaction_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.compaction_cancelled = True
                raise

    computer = Desktop()
    computer.cancellation = CancellationLatch()
    completions = HangingCompactionCompletions()
    compactor = N2InlineCompactor(
        trigger_input_tokens=1,
        keep_last_n_turns=0,
        retry_delay_seconds=0,
    )
    agent = N2ComputerAgent(
        computer=computer,
        completions=completions,
        tool_set=TOOL_SET_COMPUTER_USE_BASH_BATCH,
        compactor=compactor,
    )

    async def consume_run() -> None:
        async for _ in agent.run("task"):
            pass

    running = asyncio.create_task(consume_run())
    await completions.compaction_started.wait()
    computer.cancellation.request("operator_stop")
    with pytest.raises(asyncio.CancelledError):
        await running

    assert computer.cancellation.cause == "operator_stop"
    assert completions.calls == 2
    assert completions.compaction_cancelled is True


async def test_new_run_resets_usage_and_compactor_but_resume_continues_them():
    class RecordingCompactor:
        def __init__(self) -> None:
            self.reset_calls = 0
            self.seen: list[dict[str, Any]] = []

        def reset(self) -> None:
            self.reset_calls += 1

        async def compact(self, items, *, last_usage, completions, model, tool_set):
            self.seen.append(dict(last_usage))
            return None

    compactor = RecordingCompactor()
    completions = QueueCompletions(
        [
            _response("question", request_id="one", prompt_tokens=11),
            _response("answer", request_id="two", prompt_tokens=12),
            _response("fresh", request_id="three", prompt_tokens=13),
        ]
    )
    agent = N2ComputerAgent(computer=Desktop(), completions=completions, compactor=compactor)

    async for _ in agent.run("first"):
        pass
    async for _ in agent.resume("continue"):
        pass
    async for _ in agent.run("second"):
        pass

    assert compactor.reset_calls == 2
    assert compactor.seen == [{}, {"prompt_tokens": 11}, {}]


async def test_compactor_defaults_to_a_fresh_inline_compactor_per_agent():
    a = N2ComputerAgent(computer=Desktop(), api_key="yt_test")
    b = N2ComputerAgent(computer=Desktop(), api_key="yt_test")

    assert isinstance(a.compactor, N2InlineCompactor)
    assert isinstance(b.compactor, N2InlineCompactor)
    assert a.compactor is not b.compactor  # per-agent state, never shared
    await a.aclose()
    await b.aclose()


async def test_compactor_none_disables_compaction():
    agent = N2ComputerAgent(computer=Desktop(), api_key="yt_test", compactor=None)

    assert agent.compactor is None
    await agent.aclose()


async def test_explicit_compactor_instance_is_used_as_is():
    compactor = N2InlineCompactor(trigger_input_tokens=7)
    agent = N2ComputerAgent(computer=Desktop(), api_key="yt_test", compactor=compactor)

    assert agent.compactor is compactor
    await agent.aclose()


async def test_applied_compaction_fires_on_compaction_with_item_counts():
    class OneShotCompactor:
        def __init__(self) -> None:
            self.calls = 0

        async def compact(self, items, **_kwargs):
            self.calls += 1
            return list(items) if self.calls == 1 else None

    class Recorder:
        def __init__(self) -> None:
            self.events = []

        async def on_compaction(self, info) -> None:
            self.events.append(info)

    completions = QueueCompletions([_response("done", request_id="r-done")])
    recorder = Recorder()
    agent = N2ComputerAgent(
        computer=Desktop(),
        completions=completions,
        compactor=OneShotCompactor(),
        callbacks=[recorder],
    )

    [step async for step in agent.run("task")]

    assert agent.stopped_by == "final_answer"
    assert recorder.events, "on_compaction never fired"
    info = recorder.events[0]
    assert set(info) == {"items_before", "items_after"}
    assert info["items_after"] <= info["items_before"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"trigger_input_tokens": 0}, "trigger_input_tokens"),
        ({"keep_last_n_turns": -1}, "keep_last_n_turns"),
        ({"tail_token_budget": 0}, "tail_token_budget"),
        ({"target_max_chars": 0}, "target_max_chars"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"retry_delay_seconds": -1}, "retry_delay_seconds"),
    ],
)
def test_inline_compactor_validates_constructor_settings(kwargs: dict[str, Any], message: str):
    with pytest.raises(ValueError, match=message):
        N2InlineCompactor(**kwargs)
