"""Asynchronous HTTP client for the Yutori SDK."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._async import (
    AsyncBrowsingNamespace,
    AsyncChatNamespace,
    AsyncResearchNamespace,
    AsyncScoutsNamespace,
)
from ._http import build_headers, handle_response
from .config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT_SECONDS, sanitize_base_url
from .exceptions import AuthenticationError


class AsyncYutoriClient:
    """Asynchronous client for the Yutori API.

    Example:
        >>> import asyncio
        >>> from yutori import AsyncYutoriClient
        >>>
        >>> async def main():
        ...     async with AsyncYutoriClient(api_key="yt-...") as client:
        ...         print(await client.get_usage())
        ...         print(await client.scouts.list())
        >>>
        >>> asyncio.run(main())

    The client provides namespaced access to different API areas:
        - client.scouts: Scout management (continuous monitoring)
        - client.browsing: Browser automation tasks
        - client.research: Deep web research tasks
        - client.chat: n1 API (pixels-to-actions LLM)
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the async Yutori client.

        Args:
            api_key: Your Yutori API key (starts with "yt-"). If not provided,
                reads from the YUTORI_API_KEY environment variable.
            base_url: API base URL (default: https://api.yutori.com/v1).
            timeout: Request timeout in seconds (default: 30).

        Raises:
            AuthenticationError: If no API key is provided or found in environment.
        """
        api_key = api_key or os.environ.get("YUTORI_API_KEY")
        if not api_key:
            raise AuthenticationError(
                "No API key provided. Pass api_key or set the YUTORI_API_KEY environment variable."
            )

        self._api_key = api_key
        self._base_url = sanitize_base_url(base_url)
        self._client = httpx.AsyncClient(timeout=timeout)

        # Initialize async namespaces
        self.scouts = AsyncScoutsNamespace(self._client, self._base_url, self._api_key)
        self.browsing = AsyncBrowsingNamespace(self._client, self._base_url, self._api_key)
        self.research = AsyncResearchNamespace(self._client, self._base_url, self._api_key)
        self.chat = AsyncChatNamespace(self._base_url, self._api_key, timeout)

    async def get_usage(self) -> dict[str, Any]:
        """Get usage statistics for your API key.

        Returns:
            Dictionary containing usage information including:
                - api_key_id: Your API key identifier
                - user_id: Your user ID
                - scouts: List of scouts with their stats
        """
        response = await self._client.get(
            f"{self._base_url}/usage",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)

    async def close(self) -> None:
        """Release the underlying HTTP client resources."""
        await self._client.aclose()
        await self.chat.close()

    async def __aenter__(self) -> AsyncYutoriClient:
        return self

    async def __aexit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        await self.close()
