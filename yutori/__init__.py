"""Yutori Python SDK - Official client for the Yutori API."""

from importlib.metadata import PackageNotFoundError, version

from .async_client import AsyncYutoriClient
from .client import YutoriClient
from .exceptions import APIError, AuthenticationError, YutoriSDKError

__all__ = [
    "YutoriClient",
    "AsyncYutoriClient",
    "YutoriSDKError",
    "AuthenticationError",
    "APIError",
]

try:
    __version__ = version("yutori")
except PackageNotFoundError:
    __version__ = "0.1.0"
