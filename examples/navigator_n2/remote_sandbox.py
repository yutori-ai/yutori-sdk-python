"""Run stable Navigator n2 in a disposable local Docker Linux sandbox."""

from __future__ import annotations

import argparse
import asyncio

from yutori.auth import require_api_key
from yutori.navigator import NAVIGATOR_N2_MODEL, N2ComputerAgent

try:
    from .cua_adapter import CuaSandboxComputer
    from .shared import RunGuard, add_common_arguments, build_confirmation_callback, run_agent, selected_tool_set
except ImportError:
    from cua_adapter import CuaSandboxComputer
    from shared import RunGuard, add_common_arguments, build_confirmation_callback, run_agent, selected_tool_set


async def main(args: argparse.Namespace) -> None:
    from cua import Image, Sandbox

    # Resolve every Yutori-owned input before third-party infrastructure can be allocated.
    api_key = require_api_key()
    tool_set = selected_tool_set(args.tool_set)
    guard = RunGuard(args.max_steps)
    async with Sandbox.ephemeral(Image.linux(kind="container"), local=True) as sandbox:
        computer = CuaSandboxComputer(sandbox)
        async with N2ComputerAgent(
            computer=computer,
            api_key=api_key,
            model=NAVIGATOR_N2_MODEL,
            tool_set=tool_set,
            callbacks=[guard],
            action_confirmation_callback=build_confirmation_callback(args.auto_approve, always_confirm_shell=False),
            supports_click_modifiers=True,
        ) as agent:
            await run_agent(agent, args.task, guard)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        print("Interrupted.")
