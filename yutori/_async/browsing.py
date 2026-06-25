"""Browsing namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import Any

from .._http import _AsyncBaseNamespace, build_payload_with_schema, build_query_params


class AsyncBrowsingNamespace(_AsyncBaseNamespace):
    """Async namespace for browsing operations (one-time browser automation)."""

    async def list(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List browsing tasks for the authenticated user.

        Args:
            limit: Maximum number of tasks to return. If omitted, returns all
                browsing tasks.
            status: Filter by status ("running", "succeeded", "failed"). This
                lightweight status is derived from stored task state without a
                live workflow lookup, so "running" also covers queued tasks and
                tasks whose workflow finished but isn't yet reconciled; call
                ``get(task_id)`` for the authoritative per-task status.
            cursor: Pagination cursor from a previous response's ``next_cursor``
                or ``prev_cursor``.

        Returns:
            Dictionary with a ``tasks`` list plus ``total``, ``filtered_total``,
            ``summary`` counts, ``has_more``, and ``next_cursor`` / ``prev_cursor``
            pagination info.
        """
        # API pagination parameter is `page_size`; keep `limit` for SDK ergonomics.
        params = build_query_params(page_size=limit, status=status, cursor=cursor)
        return await self._request("get", "/browsing/tasks", params=params)

    async def create(
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
        return await self._request("post", "/browsing/tasks", json=payload)

    async def get(self, task_id: str) -> dict[str, Any]:
        """Get the status and results of a browsing task.

        Args:
            task_id: The unique identifier of the task.

        Returns:
            Dictionary containing task status and results (if completed).
        """
        return await self._request("get", f"/browsing/tasks/{task_id}")
