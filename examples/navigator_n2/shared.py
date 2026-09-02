"""Shared CLI safety and output helpers for the public n2 cookbooks."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from yutori.navigator import (
    NAVIGATOR_N2_MODEL,
    TOOL_SET_COMPUTER_USE,
    TOOL_SET_COMPUTER_USE_20260825,
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
    N2ComputerAgent,
    format_stop_and_summarize,
)
from yutori.navigator.n2_compaction import response_message

CONFIRMATION_DENIED_OUTPUT = "[ERROR] Action was not confirmed by the user."
SHELL_TOOL_NAMES = frozenset({"bash", "shell_command", "run_command"})

TOOL_SET_ALIASES = {
    "latest": TOOL_SET_COMPUTER_USE_LATEST,
    # Every dated set keeps a stable alias of its own, so bumping "latest" never
    # strands one -- 20260825 had no other name until 20260830 was published.
    "batch-files": TOOL_SET_COMPUTER_USE_20260825,
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


async def run_computer_agent(
    *,
    client: Any,
    computer: Any,
    args: argparse.Namespace,
    tool_set: str,
    always_confirm_shell: bool,
    supports_click_modifiers: bool,
) -> None:
    """Configure and run the common n2 computer-agent loop for local cookbooks."""
    async with N2ComputerAgent(
        computer=computer,
        completions=client.chat.completions,
        model=NAVIGATOR_N2_MODEL,
        tool_set=tool_set,
        max_steps=args.max_steps,
        action_confirmation_callback=build_confirmation_callback(
            args.auto_approve,
            always_confirm_shell=always_confirm_shell,
        ),
        supports_click_modifiers=supports_click_modifiers,
    ) as agent:
        await run_agent(agent, args.task, completions=client.chat.completions)


def add_common_arguments(parser: argparse.ArgumentParser, *, auto_approve_default: bool = False) -> None:
    parser.add_argument("task", help="Task for stable Navigator n2")
    parser.add_argument(
        "--tool-set",
        default="latest",
        help=(
            "Explicit n2 tool set alias ("
            + ", ".join(TOOL_SET_ALIASES)
            + ") or dated id. Default: latest (computer_use_tools-20260830)."
        ),
    )
    if auto_approve_default:
        parser.add_argument(
            "--confirm-actions",
            dest="auto_approve",
            action="store_false",
            default=True,
            help="Prompt before each action instead of approving actions in the disposable environment.",
        )
    else:
        parser.add_argument(
            "--auto-approve",
            action="store_true",
            help="Approve environment actions without prompting. Host shell commands may still require confirmation.",
        )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=500,
        help=(
            "Maximum model turns (default: 500, sized for harder tasks; "
            "pass a smaller budget for simpler tasks or to cap time/cost)"
        ),
    )


def parse_common_args(
    description: str | None,
    argv: list[str] | None = None,
    *,
    auto_approve_default: bool = False,
    configure_parser: Callable[[argparse.ArgumentParser], None] | None = None,
) -> argparse.Namespace:
    """Build the shared cookbook parser and parse it -- the ``parse_args()`` body every
    local-desktop entrypoint (``local_docker.py``, ``local_macos.py``, ``local_x11.py``)
    repeated verbatim apart from its module ``description``."""
    parser = argparse.ArgumentParser(description=description)
    add_common_arguments(parser, auto_approve_default=auto_approve_default)
    if configure_parser is not None:
        configure_parser(parser)
    return parser.parse_args(argv)


def run_cli_main(
    main: Callable[[argparse.Namespace], Awaitable[None]],
    parse_args: Callable[[], argparse.Namespace],
) -> None:
    """Run an entrypoint's ``main(parse_args())`` under ``asyncio.run``, printing a plain
    message instead of a traceback on Ctrl-C -- the ``if __name__ == "__main__":`` body every
    local-desktop entrypoint repeated verbatim."""
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        print("Interrupted.")


def _text_items(response: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


async def stop_and_summarize(agent: Any, completions: Any, task: str) -> "str | None":
    """One summarize-only completion after the step cap — no tool execution.

    Mirrors the served harness's step-cap wrap-up: the stop-and-summarize nudge
    is appended to the actor's exact next request (`agent.completion_request`),
    and the call is made by the caller, so a tool-call reply is never executed.
    Returns the model's visible text, or None if it answered with none.
    """
    nudge = {"role": "user", "content": [{"type": "text", "text": format_stop_and_summarize(task)}]}
    response = await completions.create(**agent.completion_request([nudge]))
    _, message = response_message(response)
    text = message.get("content")
    return text if isinstance(text, str) and text.strip() else None


async def run_agent(agent: Any, task: str, *, completions: Any) -> None:
    """Run the public SDK loop and print text plus non-image tool results.

    At the loop's own step cap (``stopped_by == "max_steps"``) one final
    summarize-only turn is taken and printed before raising.
    """
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
    if agent.stopped_by == "max_steps":
        summary = await stop_and_summarize(agent, completions, task)
        if summary:
            print(f"Step cap reached; the model's summary of progress so far:\n{summary}")
        raise RuntimeError(f"Stopped after {agent.max_steps} model steps (summary above)")
