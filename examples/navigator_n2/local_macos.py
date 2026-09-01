"""Run stable Navigator n2 against the local macOS desktop."""

from __future__ import annotations

import argparse

from yutori import AsyncYutoriClient
from yutori.auth import require_api_key
from yutori.navigator import NAVIGATOR_N2_MODEL, N2ComputerAgent
from yutori.navigator.macos import MacOSComputer
from yutori.navigator.n2_actions import TOOL_SETS_WITH_CLICK_MODIFIERS

try:
    from .shared import build_confirmation_callback, parse_common_args, run_agent, run_cli_main, selected_tool_set
except ImportError:
    from shared import build_confirmation_callback, parse_common_args, run_agent, run_cli_main, selected_tool_set


async def main(args: argparse.Namespace) -> None:
    tool_set = selected_tool_set(args.tool_set)
    client = AsyncYutoriClient(api_key=require_api_key())
    async with client, MacOSComputer(allow_local_shell=True) as computer:
        async with N2ComputerAgent(
            computer=computer,
            completions=client.chat.completions,
            model=NAVIGATOR_N2_MODEL,
            tool_set=tool_set,
            max_steps=args.max_steps,
            action_confirmation_callback=build_confirmation_callback(args.auto_approve, always_confirm_shell=True),
            supports_click_modifiers=tool_set in TOOL_SETS_WITH_CLICK_MODIFIERS,
            supports_scroll_modifiers=False,
        ) as agent:
            await run_agent(agent, args.task, completions=client.chat.completions)


def parse_args() -> argparse.Namespace:
    return parse_common_args(__doc__)


if __name__ == "__main__":
    run_cli_main(main, parse_args)
