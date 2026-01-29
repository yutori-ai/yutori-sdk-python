"""Browsing namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._http import build_headers, handle_response

if TYPE_CHECKING:
    import httpx


class BrowsingNamespace:
    """Namespace for browsing operations (one-time browser automation)."""

    def __init__(self, client: httpx.Client, base_url: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    def create(
        self,
        task: str,
        start_url: str,
        *,
        max_steps: int | None = None,
        agent: str | None = None,
        require_auth: bool | None = None,
        output_schema: dict[str, Any] | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
    ) -> dict[str, Any]:
        """Create a browser automation task.

        Args:
            task: Natural language description of the browsing task.
            start_url: URL to start browsing from.
            max_steps: Maximum agent steps (1-100).
            agent: Agent to use ("navigator-n1-preview-2025-11" or
                   "claude-sonnet-4-5-computer-use-2025-01-24").
            require_auth: Use auth-optimized browser for login flows.
            output_schema: JSON schema for structured output.
            webhook_url: URL for completion notifications.
            webhook_format: "scout" (default), "slack", or "zapier".

        Returns:
            Dictionary containing task details including task_id.
        """
        payload: dict[str, Any] = {
            "task": task,
            "start_url": start_url,
        }

        if max_steps is not None:
            payload["max_steps"] = max_steps
        if agent is not None:
            payload["agent"] = agent
        if require_auth is not None:
            payload["require_auth"] = require_auth
        if output_schema is not None:
            payload["output_schema"] = output_schema
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_format is not None:
            payload["webhook_format"] = webhook_format

        response = self._client.post(
            f"{self._base_url}/browsing/tasks",
            headers=build_headers(self._api_key),
            json=payload,
        )
        return handle_response(response)

    def get(self, task_id: str) -> dict[str, Any]:
        """Get the status and results of a browsing task.

        Args:
            task_id: The unique identifier of the task.

        Returns:
            Dictionary containing task status and results (if completed).
        """
        response = self._client.get(
            f"{self._base_url}/browsing/tasks/{task_id}",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)
