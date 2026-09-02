"""Run stable Navigator n2 through direct access to an X11 Linux desktop."""

from __future__ import annotations

import argparse
import os
import sys

from yutori import AsyncYutoriClient
from yutori.auth import require_api_key
from yutori.navigator.n2_actions import TOOL_SETS_WITH_CLICK_MODIFIERS

try:
    from .direct_x11_adapter import LocalX11Computer
    from .shared import parse_common_args, run_cli_main, run_computer_agent, selected_tool_set
except ImportError:
    from direct_x11_adapter import LocalX11Computer
    from shared import parse_common_args, run_cli_main, run_computer_agent, selected_tool_set


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
    computer = LocalX11Computer(cwd=args.workspace)
    async with AsyncYutoriClient(api_key=require_api_key()) as client:
        await run_computer_agent(
            client=client,
            computer=computer,
            args=args,
            tool_set=tool_set,
            always_confirm_shell=not getattr(args, "auto_approve_shell", False),
            supports_click_modifiers=tool_set in TOOL_SETS_WITH_CLICK_MODIFIERS,
        )


def _add_internal_arguments(parser: argparse.ArgumentParser) -> None:
    # Internal bridges for local_x11_docker.py. Native X11 keeps its home
    # workspace and shell confirmation armed even with --auto-approve.
    parser.add_argument("--workspace", help=argparse.SUPPRESS)
    parser.add_argument("--auto-approve-shell", action="store_true", help=argparse.SUPPRESS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return parse_common_args(__doc__, argv, configure_parser=_add_internal_arguments)


if __name__ == "__main__":
    run_cli_main(main, parse_args)
