"""Synchronous HTTP client for the Yutori SDK."""

from __future__ import annotations

from typing import Any

import httpx

from ._http import _SyncBaseNamespace, build_query_params
from ._sync import BrowsingNamespace, ChatNamespace, ResearchNamespace, ScoutsNamespace
from .config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT_SECONDS, sanitize_base_url


class YutoriClient(_SyncBaseNamespace):
    """Synchronous client for the Yutori API.

    Example:
        >>> from yutori import YutoriClient
        >>> client = YutoriClient(api_key="yt-...")
        >>> print(client.get_usage())
        >>> print(client.scouts.list())

    The client provides namespaced access to different API areas:
        - client.scouts: Scout management (continuous monitoring)
        - client.browsing: Browser automation tasks
        - client.research: Deep web research tasks
        - client.chat: Navigator API (pixels-to-actions LLM)
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the Yutori client.

        Args:
            api_key: Your Yutori API key (starts with "yt-"). If not provided,
                reads from the YUTORI_API_KEY environment variable.
            base_url: API base URL (default: https://api.yutori.com/v1).
            timeout: Request timeout in seconds (default: 30).

        Raises:
            AuthenticationError: If no API key is provided or found in environment.
        """
        from yutori.auth.credentials import require_api_key

        self._api_key = require_api_key(api_key)
        base_url = sanitize_base_url(base_url)
        super().__init__(httpx.Client(timeout=timeout), base_url, self._api_key)

        # Initialize namespaces
        self.scouts = ScoutsNamespace(self._client, self._base_url, self._api_key)
        self.browsing = BrowsingNamespace(self._client, self._base_url, self._api_key)
        self.research = ResearchNamespace(self._client, self._base_url, self._api_key)
        self._timeout = timeout
        self._chat: ChatNamespace | None = None

    def get_usage(self, *, period: str | None = None) -> dict[str, Any]:
        """Get usage statistics for your API key.

        Args:
            period: Time range for activity counts. One of "24h", "7d", "30d", "90d".
                Defaults to "24h" on the server.

        Returns:
            Dictionary with ``num_active_scouts``, ``active_scout_ids``,
            ``rate_limits``, ``navigator_rate_limits``, and ``activity``
            counts. The response also includes ``n1_rate_limits`` and
            ``activity.n1_calls`` as deprecated aliases of
            ``navigator_rate_limits`` / ``navigator_calls`` and will be
            removed in a future release; prefer the ``navigator_*`` names.
        """
        return self._request("get", "/usage", params=build_query_params(period=period))

    @property
    def chat(self) -> ChatNamespace:
        """Chat completions namespace, constructed lazily on first use.

        Building it eagerly would pay the OpenAI client construction cost
        (its own HTTP client and SSL context) on every YutoriClient, even
        for callers that never use chat completions.
        """
        if self._chat is None:
            self._chat = ChatNamespace(self._base_url, self._api_key, self._timeout)
        return self._chat

    def close(self) -> None:
        """Release the underlying HTTP client resources."""
        try:
            self._client.close()
        finally:
            # Close the chat client (if ever built) even if the HTTP client
            # close fails.
            if self._chat is not None:
                self._chat.close()

    def __enter__(self) -> YutoriClient:
        return self

    def __exit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        self.close()
