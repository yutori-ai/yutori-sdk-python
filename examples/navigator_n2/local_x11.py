"""Run stable Navigator n2 through direct access to an X11 Linux desktop."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from yutori import AsyncYutoriClient
from yutori.auth import require_api_key
from yutori.navigator import NAVIGATOR_N2_MODEL, N2ComputerAgent
from yutori.navigator.n2_actions import TOOL_SETS_WITH_CLICK_MODIFIERS

try:
    from .direct_x11_adapter import LocalX11Computer
    from .shared import add_common_arguments, build_confirmation_callback, run_agent, selected_tool_set
except ImportError:
    from direct_x11_adapter import LocalX11Computer
    from shared import add_common_arguments, build_confirmation_callback, run_agent, selected_tool_set


def _require_x11() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("local_x11.py drives an X11 Linux desktop; on macOS use local_macos.py.")
    if not os.environ.get("DISPLAY"):
        raise SystemExit(
            "No $DISPLAY is set. This entrypoint needs an X11 session — log into an 'on Xorg' "
            "session, or point DISPLAY at a virtual server (Xvfb/x11vnc)."
        )
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        print(
            "Warning: XDG_SESSION_TYPE=wayland — synthetic input may fail or act on the "
            "wrong surface through XWayland. An Xorg session or a virtual display is reliable.",
            file=sys.stderr,
        )


async def main(args: argparse.Namespace) -> None:
    _require_x11()
    tool_set = selected_tool_set(args.tool_set)
    computer = LocalX11Computer()
    async with AsyncYutoriClient(api_key=require_api_key()) as client:
        async with N2ComputerAgent(
            computer=computer,
            completions=client.chat.completions,
            model=NAVIGATOR_N2_MODEL,
            tool_set=tool_set,
            max_steps=args.max_steps,
            action_confirmation_callback=build_confirmation_callback(args.auto_approve, always_confirm_shell=True),
            supports_click_modifiers=tool_set in TOOL_SETS_WITH_CLICK_MODIFIERS,
        ) as agent:
            await run_agent(agent, args.task, completions=client.chat.completions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        print("Interrupted.")
