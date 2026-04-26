"""Browsing namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from typing import Any

from .._http import _SyncBaseNamespace, build_payload_with_schema


class BrowsingNamespace(_SyncBaseNamespace):
    """Namespace for browsing operations (one-time browser automation)."""

    def create(
        self,
        task: str,
        start_url: str,
        *,
        max_steps: int | None = None,
        agent: str | None = None,
        require_auth: bool | None = None,
        browser: str | None = None,
        output_schema: object | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
    ) -> dict[str, Any]:
        """Create a browser automation task.

        Args:
            task: Natural language description of the browsing task.
            start_url: URL to start browsing from.
            max_steps: Maximum agent steps (1-100).
            agent: Agent to use ("navigator-n1-latest" or
                   "claude-sonnet-4-5-computer-use-2025-01-24").
            require_auth: Use auth-optimized browser for login flows.
            browser: "cloud" (default) or "local" to use the desktop app with
                     the user's logged-in sessions.
            output_schema: JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance.
            webhook_url: URL for completion notifications.
            webhook_format: "scout" (default), "slack", or "zapier".

        Returns:
            Dictionary containing task details including task_id.
        """
        payload = build_payload_with_schema(
            task=task,
            start_url=start_url,
            max_steps=max_steps,
            agent=agent,
            require_auth=require_auth,
            browser=browser,
            output_schema=output_schema,
            webhook_url=webhook_url,
            webhook_format=webhook_format,
        )
        return self._request("post", "/browsing/tasks", json=payload)

    def get(self, task_id: str) -> dict[str, Any]:
        """Get the status and results of a browsing task.

        Args:
            task_id: The unique identifier of the task.

        Returns:
            Dictionary containing task status and results (if completed).
        """
        return self._request("get", f"/browsing/tasks/{task_id}")
