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

## navigator_n2_daytona.py

A computer-use agent: Navigator n2 driving a disposable [Daytona](https://www.daytona.io) Linux desktop. [Cua](https://cua.ai)'s `ComputerAgent` runs the loop — it sends the task and screenshot, executes the returned `computer_batch` and `bash` calls through a `DaytonaComputer` adapter, and sends the results back until the model answers with text. The Daytona-specific code is the adapter class; swap it to drive a different desktop. The sandbox is deleted when the run ends.

Needs Python 3.12 or 3.13, a Daytona API key, and its own dependencies (it is not part of the `examples` extra):

```bash
pip install cua-agent daytona yutori
export DAYTONA_API_KEY=...   # https://app.daytona.io
python examples/navigator_n2_daytona.py "Write 'hello from n2' to /tmp/demo.txt, then open a terminal and cat the file"
```

To drive your own Mac instead, use [Yutori MCP](https://github.com/yutori-ai/yutori-mcp) (`uvx yutori-mcp computer-use setup`). Walkthrough: [Building agents with n2](https://docs.yutori.com/reference/n2-daytona).
