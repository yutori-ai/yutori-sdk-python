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

## navigator_n1.py

A complete browsing agent using the n1 API. Launches a local Playwright browser, captures screenshots through `yutori.navigator.aplaywright_screenshot_to_data_url(...)`, converts tool-call coordinates with `yutori.navigator.denormalize_coordinates(...)`, sends them to n1, and executes predicted actions until the task is complete. The example keeps its own long-lived message history bounded with `estimate_messages_size_bytes(...)` plus `trimmed_messages_to_fit(...)`, then still ends with a standard `client.chat.completions.create(...)` call.

```bash
uv run python examples/navigator_n1.py --task "List the team member names" --start-url "https://www.yutori.com"
```

Options:
- `--task` - The task to perform
- `--start-url` - Starting URL
- `--headless` - Run browser in headless mode
- `--max-steps` - Maximum number of steps (default: 100)

## navigator_n1_5.py

A navigator agent using the n1.5 API. Demonstrates selectable tool sets (`TOOL_SET_CORE`, `TOOL_SET_EXPANDED`), optional structured JSON output via `--json-schema`, a redesigned action space with lowercase key names, and the packaged JS helpers from `yutori.navigator.tools` for expanded browser tools.

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

## navigator_n1_custom_tools.py

Extends the basic agent with a custom tool for extracting content and links from the page. Demonstrates how to define custom tools and pass them to the n1 API.

```bash
uv run python examples/navigator_n1_custom_tools.py \
    --task "Get the titles and links of all the blog posts" \
    --start-url "https://www.yutori.com"
```

The example implements an `extract_content_and_links` tool that parses the page's ARIA snapshot to extract all hyperlinks with their titles and URLs.

## navigator_n1_memo.py

Demonstrates how to use custom tools for the model to memorize information (into files) as it navigates. The agent takes a quiz and records every question, description, and options to a JSONL file.

```bash
uv run python examples/navigator_n1_memo.py \
    --task "Take the quiz and record every question, description, and all the options along the way" \
    --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
```

The example implements a `MemoToolSuite` with three custom tools:
- `add_question` - Add a new question and description to the memo
- `add_options` - Add new options to an existing question
- `list_records` - List all recorded questions and options in JSONL format
