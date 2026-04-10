# Examples

## Setup

We recommend using [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# Install SDK with example dependencies
uv sync --extra examples

# Install Playwright browsers
uv run playwright install chromium
```

## Quick Start

Run the basic n1 browser example with replay output:

```bash
export YUTORI_API_KEY=your_key_here
uv run python examples/n1.py --task "List the team member names" --start-url "https://www.yutori.com" --replay-dir runs
```

This writes replay artifacts under `runs/<run_id>/`:
- `messages.jsonl`
- `step_payloads.jsonl`
- `visualization.html`

Open `visualization.html` in your browser to inspect the run.

## Other Examples

`n1_custom_tools.py` adds a read-only extraction tool:

```bash
uv run python examples/n1_custom_tools.py \
    --task "Get the titles and links of all the blog posts" \
    --start-url "https://www.yutori.com"
```

`n1_memo.py` adds memo-writing tools:

```bash
uv run python examples/n1_memo.py \
    --task "Take the quiz and record every question, description, and all the options along the way" \
    --start-url "https://www.triviaplaza.com/three-letter-computer-terms-quiz/"
```

`n1_5.py` runs the n1.5 API:

```bash
uv run python examples/n1_5.py --tool-set expanded --task "List the team member names" --start-url "https://www.yutori.com" --replay-dir runs
```

All browser examples accept `--replay-dir`.
