import pytest

from yutori.navigator import TOOL_SET_COMPUTER_USE_HYBRID_BATCH, N2ComputerAgent

from .conftest import FakeCompletions
from .test_navigator_n2 import FakeComputer, _turn


def call(call_id, text):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "type", "arguments": '{"text": "' + text + '"}'},
    }


async def test_guidance_during_inference_discards_stale_actions():
    injected = []

    class Events:
        async def on_api_end(self, *_args):
            if not injected:
                assert agent.queue_guidance("one", "Use Hostfinder") == "queued"

        async def on_guidance_injected(self, messages):
            injected.extend(messages)

    completions = FakeCompletions(
        [
            _turn({"content": "old answer", "tool_calls": [call("old", "stale")]}),
            _turn({"content": "Done", "tool_calls": []}),
        ]
    )
    computer = FakeComputer()
    agent = N2ComputerAgent(
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH, computer=computer, completions=completions, callbacks=[Events()]
    )
    steps = [step async for step in agent.run("Find a bike")]
    assert len(steps) == 1
    assert not any(action[0] == "type" for action in computer.calls)
    assert injected == [{"id": "one", "text": "Use Hostfinder", "status": "injected"}]
    message = completions.requests[-1]["messages"][-1]
    assert message["role"] == "user"
    assert any(part.get("text") == "Use Hostfinder" for part in message["content"])
    assert any(part["type"] == "image_url" for part in message["content"])
    assert agent.queue_guidance("one", "Use Hostfinder") == "injected"
    with pytest.raises(ValueError, match="not accepting"):
        agent.queue_guidance("new", "too late")


async def test_guidance_between_tools_skips_remaining_calls_with_results():
    class Events:
        async def on_computer_call_end(self, *_args):
            agent.queue_guidance("one", "Change course")

    completions = FakeCompletions(
        [
            _turn({"content": "", "tool_calls": [call("first", "first"), call("second", "second")]}),
            _turn({"content": "Done", "tool_calls": []}),
        ]
    )
    computer = FakeComputer()
    agent = N2ComputerAgent(
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        computer=computer,
        completions=completions,
        callbacks=[Events()],
        screenshot_delay=0,
    )
    _ = [step async for step in agent.run("task")]
    assert ("type", "first") in computer.calls
    assert ("type", "second") not in computer.calls
    skipped = next(
        item for item in agent.trajectory if item.get("type") == "function_call_output" and item["call_id"] == "second"
    )
    assert "Skipped" in skipped["output"]


async def test_guidance_validation_idempotency_and_limit():
    class Events:
        async def on_run_start(self, *_args):
            for i in range(32):
                agent.queue_guidance(str(i), "hello")
            assert agent.queue_guidance("0", "hello") == "queued"
            with pytest.raises(ValueError, match="different text"):
                agent.queue_guidance("0", "different")
            with pytest.raises(ValueError, match="limit"):
                agent.queue_guidance("33", "extra")
            for text in ["", " ", "x" * 4001]:
                with pytest.raises(ValueError):
                    agent.queue_guidance("invalid", text)

    agent = N2ComputerAgent(
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        computer=FakeComputer(),
        completions=FakeCompletions([_turn({"content": "Done"})]),
        callbacks=[Events()],
    )
    _ = [step async for step in agent.run("task")]
    assert len([m for m in agent._guidance.values() if m["status"] == "injected"]) == 32


async def test_failed_capture_does_not_acknowledge_guidance():
    class Computer(FakeComputer):
        async def screenshot(self):
            raise RuntimeError("capture failed")

    class Events:
        async def on_run_start(self, *_args):
            agent.queue_guidance("one", "change")

        async def on_guidance_injected(self, _messages):
            pytest.fail("Must not acknowledge failed injection")

    agent = N2ComputerAgent(
        tool_set=TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        computer=Computer(),
        completions=FakeCompletions([]),
        callbacks=[Events()],
    )
    with pytest.raises(RuntimeError, match="capture failed"):
        _ = [step async for step in agent.run("task")]
    assert agent.queue_guidance("one", "change") == "not_sent"


async def test_stale_final_is_not_presented():
    events = []

    class Presentation:
        async def present(self, event):
            events.append(event)

    class Callbacks:
        async def on_api_end(self, *_args):
            if len(completions.requests) == 1:
                agent.queue_guidance("one", "Change course")

    completions = FakeCompletions([_turn({"content": "Stale"}), _turn({"content": "Correct"})])
    agent = N2ComputerAgent(
        computer=FakeComputer(), completions=completions, callbacks=[Callbacks()], presentation=Presentation()
    )
    _ = [step async for step in agent.run("task")]
    assert [event["text"] for event in events if event["type"] == "final"] == ["Correct"]


async def test_guidance_arriving_during_capture_is_retained_for_next_boundary():
    captured = 0

    class Computer(FakeComputer):
        async def screenshot(self):
            nonlocal captured
            captured += 1
            if captured == 1:
                agent.queue_guidance("two", "Newer correction")
            return await super().screenshot()

    class Events:
        async def on_run_start(self, *_args):
            agent.queue_guidance("one", "First correction")

    agent = N2ComputerAgent(
        computer=Computer(),
        completions=FakeCompletions([_turn({"content": "stale"}), _turn({"content": "Done"})]),
        callbacks=[Events()],
    )
    _ = [step async for step in agent.run("task")]
    assert [message["status"] for message in agent._guidance.values()] == ["injected", "injected"]


async def test_guidance_after_final_commit_is_rejected_during_presentation():
    class Presentation:
        async def present(self, event):
            if event["type"] == "final":
                with pytest.raises(ValueError, match="not accepting"):
                    agent.queue_guidance("late", "Change course")

    agent = N2ComputerAgent(
        computer=FakeComputer(), completions=FakeCompletions([_turn({"content": "Done"})]), presentation=Presentation()
    )
    _ = [step async for step in agent.run("task")]
    assert agent.stopped_by == "final_answer"
