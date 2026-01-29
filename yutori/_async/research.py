"""Research namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._http import build_headers, handle_response

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
        output_schema: dict[str, Any] | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
    ) -> dict[str, Any]:
        """Create a research task.

        Performs deep web research using 100+ MCP tools.

        Args:
            query: Natural language research query.
            user_timezone: e.g., "America/Los_Angeles".
            user_location: e.g., "San Francisco, CA, US".
            output_schema: JSON schema for structured output.
            webhook_url: URL for completion notifications.
            webhook_format: "scout" (default), "slack", or "zapier".

        Returns:
            Dictionary containing task details including task_id.
        """
        payload: dict[str, Any] = {"query": query}

        if user_timezone is not None:
            payload["user_timezone"] = user_timezone
        if user_location is not None:
            payload["user_location"] = user_location
        if output_schema is not None:
            payload["output_schema"] = output_schema
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_format is not None:
            payload["webhook_format"] = webhook_format

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
