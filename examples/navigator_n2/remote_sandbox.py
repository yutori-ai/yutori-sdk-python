"""Run Navigator n2-preview in a disposable Cua cloud Sandbox."""

from __future__ import annotations

import argparse
import asyncio

from cua import Image, Sandbox
from cua_agent import ComputerAgent
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
    # The Yutori request runs on this host. No host secret is copied into the sandbox.
    async with Sandbox.ephemeral(Image.linux()) as sandbox:
        agent = ComputerAgent(
            model="yutori/n2-preview",
            tools=[sandbox],
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
        print("Interrupted; disposable sandbox cleanup requested.")
