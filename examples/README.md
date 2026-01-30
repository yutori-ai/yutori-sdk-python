# Examples

## Setup

We recommend using [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# Install SDK with example dependencies
uv sync --extra examples

# Install Playwright browsers
uv run playwright install chromium
```

## n1.py

A complete browsing agent using the n1 API. Launches a local Playwright browser, takes screenshots, sends them to n1 to get predicted actions, and executes them until the task is complete.

```bash
uv run python examples/n1.py --task "List the team member names" --start-url "https://www.yutori.com"
```

Options:
- `--task` - The task to perform
- `--start-url` - Starting URL
- `--headless` - Run browser in headless mode
- `--max-steps` - Maximum number of steps (default: 100)

## n1_custom_tools.py

Extends the basic agent with a custom tool for extracting content and links from the page. Demonstrates how to define custom tools and pass them to the n1 API.

```bash
uv run python examples/n1_custom_tools.py \
    --task "Get the titles and links of all the blog posts" \
    --start-url "https://www.yutori.com"
```

The example implements an `extract_content_and_links` tool that parses the page's ARIA snapshot to extract all hyperlinks with their titles and URLs.

## n1_memo.py

Demonstrates how to use custom tools for the model to memorize information (into files) as it navigates. The agent takes a quiz and records every question, description, and options to a JSONL file.

```bash
uv run python examples/n1_memo.py \
    --task "Take the quiz and record every question, description, and all the options along the way" \
    --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
```

The example implements a `MemoToolSuite` with three custom tools:
- `add_question` - Add a new question and description to the memo
- `add_options` - Add new options to an existing question
- `list_records` - List all recorded questions and options in JSONL format
