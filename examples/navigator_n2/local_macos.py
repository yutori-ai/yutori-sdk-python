"""Run Navigator n2-preview against the local macOS desktop through Cua Driver."""

from __future__ import annotations

import argparse
import asyncio

from cua_agent import ComputerAgent
from local_driver import CuaDriverDesktop
from shared import (
    RunGuard,
    add_common_arguments,
    build_confirmation_callback,
    run_agent,
    selected_tool_set,
)

from yutori.auth import require_api_key


async def main(args: argparse.Namespace) -> None:
    guard = RunGuard(args.max_steps)
    async with CuaDriverDesktop() as desktop:
        agent = ComputerAgent(
            model="yutori/n2-preview",
            tools=[desktop],
            api_key=require_api_key(),
            callbacks=[guard],
            action_confirmation_callback=build_confirmation_callback(args.auto_approve),
            tool_set=selected_tool_set(args.batch),
        )
        await run_agent(agent, args.task, guard)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        print("Interrupted; Cua Driver session closed.")
