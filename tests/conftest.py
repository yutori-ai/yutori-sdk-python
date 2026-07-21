"""Test configuration for Yutori SDK tests."""

import pytest

from yutori import AsyncYutoriClient, YutoriClient


@pytest.fixture
def client():
    """Shared YutoriClient fixture for sync tests."""
    client = YutoriClient(api_key="yt-test")
    yield client
    client.close()


@pytest.fixture
async def async_client():
    """Shared AsyncYutoriClient fixture for async tests, mirroring `client` above."""
    client = AsyncYutoriClient(api_key="yt-test")
    yield client
    await client.close()


def require_examples_extra() -> None:
    """Skip collection of the calling test module if the "examples" extra isn't installed.

    examples/_common.py pulls in the optional "examples" extra (loguru, openai, tenacity)
    which isn't installed by the `.[dev]`-only CI test job; skip cleanly rather than
    erroring on collection when it's unavailable. Call this before importing anything
    from ``examples`` at module level.
    """
    pytest.importorskip("loguru")
