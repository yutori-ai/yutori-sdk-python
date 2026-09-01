"""Run stable Navigator n2 in a disposable local Docker container."""

from __future__ import annotations

import argparse

from yutori import AsyncYutoriClient
from yutori.auth import require_api_key
from yutori.navigator import NAVIGATOR_N2_MODEL, N2ComputerAgent

try:
    from .cua_adapter import CuaSandboxComputer
    from .shared import build_confirmation_callback, parse_common_args, run_agent, run_cli_main, selected_tool_set
except ImportError:
    from cua_adapter import CuaSandboxComputer
    from shared import build_confirmation_callback, parse_common_args, run_agent, run_cli_main, selected_tool_set


def _watch_url(sandbox) -> "str | None":
    """Browser URL for the sandbox's noVNC viewer, when the runtime exposes one.

    Reads the pinned cua-sandbox runtime's connection info (private attribute,
    stable for the exact version pinned in pyproject.toml).
    """
    info = getattr(sandbox, "_runtime_info", None)
    port = getattr(info, "vnc_port", None)
    if not port:
        return None
    host = getattr(info, "host", None) or "localhost"
    return f"http://{host}:{port}/vnc.html"


async def main(args: argparse.Namespace) -> None:
    from cua_sandbox import Image, Sandbox

    # Resolve every Yutori-owned input before third-party infrastructure can be allocated.
    api_key = require_api_key()
    tool_set = selected_tool_set(args.tool_set)
    async with AsyncYutoriClient(api_key=api_key) as client:
        async with Sandbox.ephemeral(Image.linux(kind="container"), local=True) as sandbox:
            watch = _watch_url(sandbox)
            if watch:
                print(f"Watch the desktop live: {watch}")
            computer = CuaSandboxComputer(sandbox)
            async with N2ComputerAgent(
                computer=computer,
                completions=client.chat.completions,
                model=NAVIGATOR_N2_MODEL,
                tool_set=tool_set,
                max_steps=args.max_steps,
                action_confirmation_callback=build_confirmation_callback(args.auto_approve, always_confirm_shell=False),
                supports_click_modifiers=True,
            ) as agent:
                await run_agent(agent, args.task, completions=client.chat.completions)


def parse_args() -> argparse.Namespace:
    return parse_common_args(__doc__)


if __name__ == "__main__":
    run_cli_main(main, parse_args)
