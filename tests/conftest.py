"""Test configuration for Yutori SDK tests."""

from typing import Any

import pytest

from yutori import AsyncYutoriClient, YutoriClient


class FakeCompletions:
    """Scripted chat surface: returns each response in turn, records requests.

    Shared by test_navigator_n2.py and test_navigator_n2_cookbooks.py, which
    both drive `N2ComputerAgent` against a canned sequence of Chat Completions
    responses.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        payload = self.responses.pop(0)

        class _Response:
            def model_dump(self) -> dict[str, Any]:
                return payload

        return _Response()


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
