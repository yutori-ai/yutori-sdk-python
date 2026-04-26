"""Research namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from typing import Any

from .._http import _SyncBaseNamespace, build_payload_with_schema


class ResearchNamespace(_SyncBaseNamespace):
    """Namespace for research operations (one-time deep web research)."""

    def create(
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
        payload = build_payload_with_schema(
            query=query,
            user_timezone=user_timezone,
            user_location=user_location,
            browser=browser,
            output_schema=output_schema,
            webhook_url=webhook_url,
            webhook_format=webhook_format,
        )
        return self._request("post", "/research/tasks", json=payload)

    def get(self, task_id: str) -> dict[str, Any]:
        """Get the status and results of a research task.

        Args:
            task_id: The unique identifier of the task.

        Returns:
            Dictionary containing task status and results (if completed).
        """
        return self._request("get", f"/research/tasks/{task_id}")
