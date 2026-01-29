"""Test configuration for Yutori SDK tests."""

import pytest

from yutori import YutoriClient


@pytest.fixture
def client():
    """Shared YutoriClient fixture for sync tests."""
    client = YutoriClient(api_key="yt-test")
    yield client
    client.close()
