# Yutori SDK Examples

Example scripts demonstrating how to use the Yutori API.

## Setup

Install dependencies using [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# Install SDK with example dependencies
uv sync --extra examples

# Install Playwright browsers
uv run playwright install chromium
```

Set your API key (see [Authentication](https://docs.yutori.com/authentication)):

```bash
export YUTORI_API_KEY=<your-api-key>
```

## Examples

### n1.py

Demonstrate how to build a browsing agent with n1 API to navigate the web and complete tasks.

The script launches a local Playwright browser, takes screenshots, sends them to the n1 API to get predicted actions, executes those actions, and repeats until the task is complete.

**Usage:**

```bash
uv run python examples/n1.py --task "List the team member names" --start-url "https://www.yutori.com"
```