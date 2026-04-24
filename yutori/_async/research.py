"""Research namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import Any

from .._http import _AsyncBaseNamespace, build_payload
from .._schema import resolve_output_schema


class AsyncResearchNamespace(_AsyncBaseNamespace):
    """Async namespace for research operations (one-time deep web research)."""

    async def create(
        self,
        query: str,
        *,
        user_timezone: str | None = None,
        user_location: str | None = None,
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
            output_schema=resolve_output_schema(output_schema),
            webhook_url=webhook_url,
            webhook_format=webhook_format,
        )
        return await self._request("post", "/research/tasks", json=payload)

    async def get(self, task_id: str) -> dict[str, Any]:
        """Get the status and results of a research task.

        Args:
            task_id: The unique identifier of the task.

        Returns:
            Dictionary containing task status and results (if completed).
        """
        return await self._request("get", f"/research/tasks/{task_id}")
