"""Shared safety, limits, and output helpers for the n2 cookbooks."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from yutori.navigator import TOOL_SET_COMPUTER_USE, TOOL_SET_COMPUTER_USE_BATCH


class RunGuard:
    """Stop at a model-step limit and fail fast on executor errors."""

    def __init__(self, max_steps: int) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.max_steps = max_steps
        self.steps = 0
        self.limit_reached = False

    async def on_run_continue(self, _kwargs: dict, _old_items: list, _new_items: list) -> bool:
        if self.steps >= self.max_steps:
            self.limit_reached = True
            return False
        self.steps += 1
        return True

    async def on_computer_call_end(self, _item: dict, outputs: list[dict]) -> None:
        for output in outputs:
            value = output.get("output")
            if isinstance(value, str) and value.startswith("[ERROR]"):
                raise RuntimeError(value)
            if isinstance(value, dict):
                result = value.get("result")
                if isinstance(result, dict) and result.get("status") == "stopped":
                    raise RuntimeError(f"Computer execution stopped: {result.get('error') or result}")


def selected_tool_set(batch: bool) -> str:
    """Return the safe default or the explicitly requested batch tool set."""
    return TOOL_SET_COMPUTER_USE_BATCH if batch else TOOL_SET_COMPUTER_USE


def describe_confirmation(request: dict[str, Any]) -> str:
    """Render the original, fully validated Yutori call for one confirmation."""
    tool_name = str(request.get("tool_name") or "computer action")
    arguments = request.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass
    return f"{tool_name}: {json.dumps(arguments, indent=2, ensure_ascii=False)}"


def build_confirmation_callback(auto_approve: bool):
    """Build the cookbook confirmation policy callback.

    Cua asks this callback only for clicks, drag, typing, and key presses. A
    validated batch is passed here once as one complete request.
    """

    async def confirm(request: dict[str, Any]) -> bool:
        rendered = describe_confirmation(request)
        if auto_approve:
            print(f"AUTO-APPROVED\n{rendered}")
            return True
        print(f"\nApproval required\n{rendered}")
        answer = await asyncio.to_thread(input, "Execute this action? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    return confirm


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task", help="Task for Navigator n2-preview")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Opt in to the experimental computer_batch tool set",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Execute confirmable actions without prompting (unsafe on untrusted tasks)",
    )
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum model turns (default: 30)")


def _text_from_item(item: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    if item.get("type") != "message":
        return texts
    for part in item.get("content") or []:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return texts


async def run_agent(agent: Any, task: str, guard: RunGuard) -> None:
    """Run a Cua agent, printing model text and compact action progress."""
    async for response in agent.run(task, stream=False):
        for item in response.get("output") or []:
            for value in _text_from_item(item):
                print(value)
            if item.get("type") == "function_call":
                print(f"ACTION {item.get('name')}: {item.get('arguments')}")
            if item.get("type") == "function_call_output":
                output = item.get("output")
                if isinstance(output, dict) and isinstance(output.get("result"), dict):
                    print(f"RESULT {json.dumps(output['result'], sort_keys=True)}")
    if guard.limit_reached:
        raise RuntimeError(f"Stopped after the configured {guard.max_steps} model steps")
