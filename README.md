# Yutori Python SDK

[![PyPI version](https://img.shields.io/pypi/v/yutori.svg)](https://pypi.org/project/yutori/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

The official Python library for the Yutori API.

Yutori provides APIs for building web agents that autonomously execute tasks on the web. The SDK offers both synchronous and asynchronous clients with full type annotations.

## Documentation

- [API Reference](https://docs.yutori.com)
- [Platform Dashboard](https://platform.yutori.com)

## Installation

```bash
pip install yutori
```

## Usage

### Getting an API Key

1. Sign up at [platform.yutori.com](https://platform.yutori.com)
2. Navigate to Settings to create an API key
3. API keys start with `yt-`

### Basic Example

```python
from yutori import YutoriClient

client = YutoriClient(api_key="yt-...")

# Check your API key usage
print(client.get_usage())
```

The SDK reads the `YUTORI_API_KEY` environment variable if no key is provided:

```python
client = YutoriClient()  # Uses YUTORI_API_KEY env var
```

## API Overview

The Yutori API provides four main capabilities:

| API | Description | SDK Namespace |
|-----|-------------|---------------|
| **Scouting** | Continuous web monitoring on a schedule | `client.scouts` |
| **Browsing** | One-time browser automation tasks | `client.browsing` |
| **Research** | Deep web research using 100+ tools | `client.research` |
| **n1** | Pixels-to-actions LLM for browser control | `client.chat` |

## Scouting API

Scouts run on a configurable schedule to monitor websites and send notifications when relevant changes occur.

```python
from yutori import YutoriClient

client = YutoriClient(api_key="yt-...")

# Create a scout that runs daily
scout = client.scouts.create(
    query="Monitor https://example.com/pricing for price changes",
    output_interval=86400,  # 24 hours in seconds
    webhook_url="https://your-webhook.com/updates",
)
print(f"Created scout: {scout['id']}")

# List all active scouts
scouts = client.scouts.list(status="active")

# Get a specific scout
scout = client.scouts.get("scout_abc123")

# Pause a scout
client.scouts.update("scout_abc123", status="paused")

# Resume a scout
client.scouts.update("scout_abc123", status="active")

# Archive a scout
client.scouts.update("scout_abc123", status="done")

# Get scout reports
updates = client.scouts.get_updates("scout_abc123", limit=10)

# Delete a scout
client.scouts.delete("scout_abc123")
```

### Structured Output

Request structured JSON output by providing a JSON schema:

```python
scout = client.scouts.create(
    query="Find all job postings on https://example.com/careers",
    output_schema={
        "type": "object",
        "properties": {
            "jobs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "location": {"type": "string"},
                        "url": {"type": "string"}
                    }
                }
            }
        }
    }
)
```

## Browsing API

Run one-time browser automation tasks. An AI agent operates a cloud browser to complete your task.

```python
# Create a browsing task
task = client.browsing.create(
    task="Go to the pricing page and extract all plan prices",
    start_url="https://example.com",
    max_steps=25,
)

# Poll for completion
import time
while True:
    result = client.browsing.get(task["task_id"])
    if result["status"] in ("succeeded", "failed"):
        break
    time.sleep(5)

print(result)
```

## Research API

Perform deep web research using 100+ MCP tools including search engines, APIs, and data sources.

```python
task = client.research.create(
    query="Find the latest Series A funding rounds for AI startups",
    user_timezone="America/Los_Angeles",
)

# Poll for results
result = client.research.get(task["task_id"])
```

## n1 API

The n1 API is a pixels-to-actions LLM that processes screenshots and predicts browser actions. It follows the OpenAI Chat Completions interface.

```python
response = client.chat.completions(
    model="n1-preview-2025-11",
    messages=[
        {"role": "user", "content": "Click the login button"},
        {
            "role": "observation",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
        }
    ],
)
```

## Async Usage

The SDK provides an async client with the same interface:

```python
import asyncio
from yutori import AsyncYutoriClient

async def main():
    async with AsyncYutoriClient(api_key="yt-...") as client:
        # All methods are async
        usage = await client.get_usage()
        print(usage)

        scouts = await client.scouts.list()
        print(scouts)

        scout = await client.scouts.create(
            query="Monitor https://example.com for updates",
            output_interval=3600,
        )
        print(scout)

asyncio.run(main())
```

## Error Handling

The SDK raises typed exceptions for API errors:

```python
from yutori import YutoriClient, APIError, AuthenticationError

try:
    client = YutoriClient(api_key="invalid-key")
    client.get_usage()
except AuthenticationError as e:
    print(f"Invalid API key: {e}")
except APIError as e:
    print(f"API error (status {e.status_code}): {e.message}")
```

### Exception Types

| Exception | Status Code | Description |
|-----------|-------------|-------------|
| `AuthenticationError` | 401 | Invalid or missing API key |
| `APIError` | 4xx, 5xx | General API error with status code |

## Configuration

```python
from yutori import YutoriClient

client = YutoriClient(
    api_key="yt-...",                          # Required (or set YUTORI_API_KEY)
    base_url="https://api.yutori.com/v1",      # Default
    timeout=30.0,                               # Request timeout in seconds
)
```

## Requirements

- Python 3.9+
- [httpx](https://www.python-httpx.org/) for HTTP requests

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
