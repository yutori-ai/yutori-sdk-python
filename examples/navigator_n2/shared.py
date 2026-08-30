"""Shared CLI safety and output helpers for the public n2 cookbooks."""

from __future__ import annotations

import argparse
import json
from typing import Any

from yutori.navigator import (
    TOOL_SET_COMPUTER_USE,
    TOOL_SET_COMPUTER_USE_BASH_BATCH,
    TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
    TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
    TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
    TOOL_SET_COMPUTER_USE_BATCH,
    TOOL_SET_COMPUTER_USE_BROWSER_BATCH,
    TOOL_SET_COMPUTER_USE_FILES,
    TOOL_SET_COMPUTER_USE_FILES_BATCH,
    TOOL_SET_COMPUTER_USE_HYBRID,
    TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
    TOOL_SET_COMPUTER_USE_LATEST,
)

CONFIRMATION_DENIED_OUTPUT = "[ERROR] Action was not confirmed by the user."
SHELL_TOOL_NAMES = frozenset({"bash", "shell_command", "run_command"})

TOOL_SET_ALIASES = {
    "latest": TOOL_SET_COMPUTER_USE_LATEST,
    "full-batch": TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
    "screenshot-batch": TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
    "modifiers-batch": TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
    "bash-batch": TOOL_SET_COMPUTER_USE_BASH_BATCH,
    "browser-batch": TOOL_SET_COMPUTER_USE_BROWSER_BATCH,
    "files-batch": TOOL_SET_COMPUTER_USE_FILES_BATCH,
    "files": TOOL_SET_COMPUTER_USE_FILES,
    "hybrid-batch": TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
    "hybrid": TOOL_SET_COMPUTER_USE_HYBRID,
    "gui-batch": TOOL_SET_COMPUTER_USE_BATCH,
    "gui": TOOL_SET_COMPUTER_USE,
}


class RunGuard:
    """Stop a cookbook after a bounded number of model turns."""

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


def selected_tool_set(value: str) -> str:
    """Resolve an explicit cookbook tool-set alias or dated identifier."""
    resolved = TOOL_SET_ALIASES.get(value, value)
    if resolved not in TOOL_SET_ALIASES.values():
        aliases = ", ".join(TOOL_SET_ALIASES)
        raise ValueError(f"unknown tool set: {value}. Choose an alias ({aliases}) or a supported dated id.")
    return resolved


def describe_confirmation(request: dict[str, Any]) -> str:
    arguments = request.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass
    tool_name = request.get("tool_name") or "computer action"
    return f"{tool_name}: {json.dumps(arguments, indent=2, ensure_ascii=False)}"


def build_confirmation_callback(auto_approve: bool, *, always_confirm_shell: bool):
    """Build the explicit confirmation policy used by the cookbook entrypoints."""

    async def confirm(request: dict[str, Any]) -> bool:
        tool_name = str(request.get("tool_name") or "")
        if auto_approve and not (always_confirm_shell and tool_name in SHELL_TOOL_NAMES):
            print(f"AUTO-APPROVED\n{describe_confirmation(request)}")
            return True
        if auto_approve and tool_name in SHELL_TOOL_NAMES:
            print(f"\n{tool_name} still requires confirmation on this host.")
        print(f"\nApproval required\n{describe_confirmation(request)}")
        try:
            answer = input("Execute this action? [y/N] ")
        except EOFError:
            print("[ERROR] No interactive input is available; action denied.")
            return False
        return answer.strip().lower() in {"y", "yes"}

    return confirm


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task", help="Task for stable Navigator n2")
    parser.add_argument(
        "--tool-set",
        default="latest",
        help=(
            "Explicit n2 tool set alias ("
            + ", ".join(TOOL_SET_ALIASES)
            + ") or dated id. Default: latest (computer_use_tools-20260825)."
        ),
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve environment actions without prompting. Host shell commands may still require confirmation.",
    )
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum model turns (default: 30)")


def _text_items(response: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


async def run_agent(agent: Any, task: str, guard: RunGuard) -> None:
    """Run the public SDK loop and print text plus non-image tool results."""
    async for response in agent.run(task):
        for text in _text_items(response):
            print(text)
        for item in response.get("output") or []:
            if item.get("type") == "function_call":
                print(f"ACTION {item.get('name')}: {item.get('arguments')}")
            elif item.get("type") == "function_call_output":
                output = item.get("output")
                if isinstance(output, str):
                    print(output)
                elif isinstance(output, dict) and output.get("result") is not None:
                    print(f"RESULT {json.dumps(output['result'], sort_keys=True)}")
    if guard.limit_reached:
        raise RuntimeError(f"Stopped after the configured {guard.max_steps} model steps")
