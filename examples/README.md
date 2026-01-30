# Examples

## Setup

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
