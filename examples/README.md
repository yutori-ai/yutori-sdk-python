# Examples

## Setup

We recommend using [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# Install SDK with example dependencies
uv sync --extra examples

# Authenticate once, or set YUTORI_API_KEY
yutori auth login

# Install Playwright browsers
uv run playwright install chromium
```

These steps cover the `navigator_n1_5*` examples. The two Navigator n2 examples manage their own environments (see their sections below) — only the `yutori auth login` step applies to them.

The examples rely on the SDK's normal credential resolution. They do not expose a separate `--api-key` flag.

## navigator_n1_5.py

A complete browsing agent using the Navigator API with the Navigator n1.5 model. Launches a local Playwright browser, captures screenshots through `yutori.navigator.aplaywright_screenshot_to_data_url(...)`, converts tool-call coordinates with `yutori.navigator.denormalize_coordinates(...)`, sends them to Navigator n1.5, and executes predicted actions until the task is complete. Demonstrates selectable tool sets (`TOOL_SET_CORE`, `TOOL_SET_EXPANDED`), optional structured JSON output via `--json-schema`, lowercase key names, and the packaged JS helpers from `yutori.navigator.tools` for expanded browser tools. The example keeps the complete message history for replay and uses `update_trimmed_history(...)` from `yutori.navigator.loop` to bound a separate, screenshot-trimmed request copy, then still ends with a standard `client.chat.completions.create(...)` call.

```bash
uv run python examples/navigator_n1_5.py --task "List the team member names" --start-url "https://www.yutori.com"
```

Options:
- `--task` - The task to perform
- `--start-url` - Starting URL
- `--headless` - Run browser in headless mode
- `--max-steps` - Maximum number of steps (default: 100)
- `--tool-set` - Tool set to use: `core` or `expanded` (default: core)
- `--disable-tools` - Space-separated list of tools to disable
- `--json-schema` - JSON schema string for structured output
- `--timezone` - User timezone (default: America/Los_Angeles)
- `--location` - User location (default: San Francisco, CA, US)

## navigator_n1_5_custom_tools.py

Extends the Navigator n1.5 agent with a custom tool for extracting content and links from the page. Demonstrates how to define custom tools, pass them to the Navigator API alongside the built-in browser actions, and handle their calls.

```bash
uv run python examples/navigator_n1_5_custom_tools.py \
    --task "Get the titles and links of all the blog posts" \
    --start-url "https://www.yutori.com"
```

The example implements an `extract_content_and_links` tool that parses the page's ARIA snapshot to extract all hyperlinks with their titles and URLs.

## navigator_n1_5_memo.py

Demonstrates how to use custom tools for the model to memorize information (into files) as it navigates. The agent takes a quiz and records every question, description, and options to a JSONL file.

```bash
uv run python examples/navigator_n1_5_memo.py \
    --task "Take the quiz and record every question, description, and all the options along the way" \
    --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
```

The example implements a `MemoToolSuite` with three custom tools:
- `add_question` - Add a new question and description to the memo
- `add_options` - Add new options to an existing question
- `list_records` - List all recorded questions and options in JSONL format

## navigator_n2/

The [public Cua cookbook](navigator_n2/README.md) runs Navigator n2 in a local Docker container. It keeps its own environment (`cua-sandbox` needs Python 3.11–3.13), so it works independently of the Setup above — only `yutori auth login` carries over:

```bash
cd examples/navigator_n2
uv sync --python 3.12
uv run python remote_sandbox.py --auto-approve \
    "Open Calculator and compute 17 * 23"
```

## navigator_n2_daytona.py

A computer-use agent using third-party [Daytona](https://www.daytona.io) infrastructure. `N2ComputerAgent` runs the loop, while this Yutori-maintained example provides a compact `DaytonaComputer` adapter plus sandbox lifecycle wiring. It serves the full current tool set: GUI and `bash` natively, and the file tools (`read`/`write`/`edit`) via the SDK's `ShellFileToolsMixin` — [cua_adapter.py](navigator_n2/cua_adapter.py) shows a second wiring of the same mixin, and [api.md](../api.md)'s "Navigator n2 loop" section documents the contract. The ephemeral sandbox is deleted, with deletion confirmation requested, when the run ends.

The script declares Python 3.10+, the Yutori SDK, and the tested Daytona version as inline metadata; `uv` installs them into an isolated environment automatically. The default turn budget (`--max-steps 500`) gives harder tasks room to finish; pass a smaller budget (e.g. `--max-steps 100`) for simpler tasks, or to truncate a trajectory for time or cost. If the run hits the cap, the example takes one summarize-only turn (no tool execution) and prints the model's summary of progress before exiting:

```bash
export DAYTONA_API_KEY=...   # https://app.daytona.io
uv run examples/navigator_n2_daytona.py \
    "Find the OS version and free disk space of this machine, and save a summary to a file on the desktop"
```

Pass `--record` to capture the desktop during the run; the video is downloaded to `n2-daytona-run.mp4` before the sandbox is deleted.

To drive your own Mac instead, use [Yutori MCP](https://github.com/yutori-ai/yutori-mcp) (`uvx yutori-mcp computer-use setup`). Walkthrough: [Run n2 on Daytona](https://docs.yutori.com/reference/n2-daytona).
