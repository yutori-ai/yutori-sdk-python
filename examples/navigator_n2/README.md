# Navigator n2 with public Cua

These runnable cookbooks use the public Python SDK loop, stable model id n2, and the public Cua 0.1.6 package. They do not import an agent package or source code from another repository.

The cookbook environment is separate because Cua 0.1.6 requires Python 3.12 or 3.13. Its dependency is pinned in pyproject.toml, and the SDK source is used from this checkout while developing the release.

~~~bash
cd examples/navigator_n2
uv sync --python 3.12
~~~

Authenticate Yutori with yutori auth login or YUTORI_API_KEY. The sandbox example additionally requires normal Cua cloud authentication (CUA_API_KEY or its login flow). The Yutori key remains on the host and is never copied into the sandbox.

## Local macOS

The local entrypoint uses yutori.navigator.macos.MacOSComputer, the public SDK runtime that yutori-mcp uses. Install the SDK macOS extra, grant Accessibility and Screen Recording to CuaDriver.app in an unlocked GUI session, and then run it:

~~~bash
pip install 'yutori[macos]==0.9.3'
uv run --extra macos python local_macos.py "Open Calculator and compute 17 * 23"
~~~

The local runtime can execute bash and file tools only because the example explicitly enables its local-shell option. Every local shell command remains confirmable even with --auto-approve.

## Disposable sandbox

The remote entrypoint adapts public Cua Sandbox mouse, keyboard, screenshot, and shell interfaces to N2ComputerAgent. It uses a disposable Linux desktop and destroys it when the process exits.

~~~bash
uv run python remote_sandbox.py "Open Calculator and compute 17 * 23"
~~~

The adapter supports the five current n2 tools: computer_batch, edit, read, write, and bash. It executes normalized coordinates against the sandbox’s native screen, preserves a bash working directory, and uses Cua key-down/key-up primitives to keep modifiers attached to their click or scroll gesture.

## Tool sets

Both examples always send an explicit immutable tool-set id. The default alias is latest, which resolves to computer_use_tools-20260825. Use an older published set only to replay a compatible trajectory:

| Alias | Tool set |
| --- | --- |
| latest | computer_use_tools-20260825 |
| full-batch | computer_use_tools-20260822 |
| screenshot-batch | computer_use_tools-20260821 |
| modifiers-batch | computer_use_tools-20260815 |
| bash-batch | computer_use_tools-20260812 |
| files-batch | computer_use_tools-20260808 |
| files | computer_use_tools-20260807 |
| hybrid-batch | computer_use_tools-20260729 |
| hybrid | computer_use_tools-20260728 |
| gui-batch | computer_use_tools-20260716 |
| gui | computer_use_tools-20260708 |

~~~bash
uv run python remote_sandbox.py --tool-set gui "Open Calculator and compute 17 * 23"
~~~

The public n2 reference documents the current five tools, all 15 batch actions, screenshot compression, and the single-result-per-tool-call rule: https://docs.yutori.com/reference/n2
