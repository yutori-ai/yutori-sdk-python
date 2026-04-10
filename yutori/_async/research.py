"""Research namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._http import build_headers, build_payload, handle_response
from .._schema import resolve_output_schema

if TYPE_CHECKING:
    import httpx


class AsyncResearchNamespace:
    """Async namespace for research operations (one-time deep web research)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    async def create(
        self,
        query: str,
        *,
        user_timezone: str | None = None,
        user_location: str | None = None,
        browser: str | None = None,
        output_schema: object | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
    ) -> dict[str, Any]:
        """Create a research task.

        Performs deep web research using 100+ MCP tools.

        Args:
            query: Natural language research query.
            user_timezone: e.g., "America/Los_Angeles".
            user_location: e.g., "San Francisco, CA, US".
            browser: "cloud" (default) or "local" to use the desktop app with
                     the user's logged-in sessions.
            output_schema: JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance.
            webhook_url: URL for completion notifications.
            webhook_format: "scout" (default), "slack", or "zapier".

        Returns:
            Dictionary containing task details including task_id.
        """
        payload = build_payload(
            query=query,
            user_timezone=user_timezone,
            user_location=user_location,
            browser=browser,
            output_schema=resolve_output_schema(output_schema),
            webhook_url=webhook_url,
            webhook_format=webhook_format,
        )

        response = await self._client.post(
            f"{self._base_url}/research/tasks",
            headers=build_headers(self._api_key),
            json=payload,
        )
        return handle_response(response)

    async def get(self, task_id: str) -> dict[str, Any]:
        """Get the status and results of a research task.

        Args:
            task_id: The unique identifier of the task.

        Returns:
            Dictionary containing task status and results (if completed).
        """
        response = await self._client.get(
            f"{self._base_url}/research/tasks/{task_id}",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)
