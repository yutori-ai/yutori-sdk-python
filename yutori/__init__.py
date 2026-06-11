"""Yutori Python SDK - Official client for the Yutori API."""

from importlib.metadata import PackageNotFoundError, version

from .async_client import AsyncYutoriClient
from .client import YutoriClient
from .exceptions import APIConnectionError, APIError, AuthenticationError, YutoriSDKError

__all__ = [
    "YutoriClient",
    "AsyncYutoriClient",
    "YutoriSDKError",
    "AuthenticationError",
    "APIError",
    "APIConnectionError",
]

try:
    __version__ = version("yutori")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
