"""Scouts namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._http import build_headers, build_query_params, handle_response

if TYPE_CHECKING:
    import httpx


class AsyncScoutsNamespace:
    """Async namespace for scout-related operations (continuous web monitoring)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    async def list(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List scouts for the authenticated user.

        Args:
            limit: Maximum number of scouts to return.
            status: Filter by status ("active", "paused", "done").

        Returns:
            Dictionary containing list of scouts.
        """
        params = build_query_params(limit=limit, status=status)
        response = await self._client.get(
            f"{self._base_url}/scouting/tasks",
            headers=build_headers(self._api_key),
            params=params,
        )
        return handle_response(response)

    async def get(self, scout_id: str) -> dict[str, Any]:
        """Get details of a specific scout.

        Args:
            scout_id: The unique identifier of the scout.

        Returns:
            Dictionary containing scout details.
        """
        response = await self._client.get(
            f"{self._base_url}/scouting/tasks/{scout_id}",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)

    async def create(
        self,
        query: str,
        *,
        output_interval: int = 86400,
        start_timestamp: int | None = None,
        user_timezone: str | None = None,
        user_location: str | None = None,
        output_schema: dict[str, Any] | None = None,
        skip_email: bool | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
        is_public: bool | None = None,
    ) -> dict[str, Any]:
        """Create a new monitoring scout.

        Args:
            query: Natural language description of what to monitor.
            output_interval: Seconds between runs (min: 1800, default: 86400 = daily).
            start_timestamp: Unix timestamp to start (0 = immediately).
            user_timezone: e.g., "America/Los_Angeles".
            user_location: e.g., "San Francisco, CA, US".
            output_schema: JSON schema for structured output.
            skip_email: Disable email notifications.
            webhook_url: URL for completion notifications.
            webhook_format: "scout" (default), "slack", or "zapier".
            is_public: Whether the scout is publicly visible.

        Returns:
            Dictionary containing created scout details.
        """
        payload: dict[str, Any] = {
            "query": query,
            "output_interval": output_interval,
        }

        if start_timestamp is not None:
            payload["start_timestamp"] = start_timestamp
        if user_timezone is not None:
            payload["user_timezone"] = user_timezone
        if user_location is not None:
            payload["user_location"] = user_location
        if output_schema is not None:
            payload["output_schema"] = output_schema
        if skip_email is not None:
            payload["skip_email"] = skip_email
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_format is not None:
            payload["webhook_format"] = webhook_format
        if is_public is not None:
            payload["is_public"] = is_public

        response = await self._client.post(
            f"{self._base_url}/scouting/tasks",
            headers=build_headers(self._api_key),
            json=payload,
        )
        return handle_response(response)

    async def update(
        self,
        scout_id: str,
        *,
        query: str | None = None,
        status: str | None = None,
        output_interval: int | None = None,
        user_timezone: str | None = None,
        user_location: str | None = None,
        output_schema: dict[str, Any] | None = None,
        skip_email: bool | None = None,
        webhook_url: str | None = None,
        webhook_format: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing scout.

        Use status="paused" to pause, status="active" to resume, status="done" to archive.

        Args:
            scout_id: The unique identifier of the scout.
            query: Updated monitoring query.
            status: New status ("active", "paused", "done").
            output_interval: Updated interval between runs.
            user_timezone: Updated timezone.
            user_location: Updated location.
            output_schema: Updated JSON schema for structured output.
            skip_email: Updated email notification setting.
            webhook_url: Updated webhook URL.
            webhook_format: Updated webhook format.

        Returns:
            Dictionary containing updated scout details.

        Raises:
            ValueError: If status is provided along with other fields (API limitation).
        """
        # Build payload from non-None field values (single source of truth for field list)
        payload: dict[str, Any] = {
            k: v
            for k, v in {
                "query": query,
                "output_interval": output_interval,
                "user_timezone": user_timezone,
                "user_location": user_location,
                "output_schema": output_schema,
                "skip_email": skip_email,
                "webhook_url": webhook_url,
                "webhook_format": webhook_format,
            }.items()
            if v is not None
        }

        # Check for conflicting parameters - API doesn't support both in one call
        if status is not None and payload:
            raise ValueError(
                "Cannot update status and other fields simultaneously. "
                "The API requires separate calls: one for status change, another for field updates."
            )

        # Handle status changes via dedicated endpoints
        if status is not None:
            status_endpoints = {
                "paused": "pause",
                "active": "resume",
                "done": "done",
            }
            if status not in status_endpoints:
                raise ValueError(f"Invalid status: {status}. Must be 'active', 'paused', or 'done'.")
            endpoint = status_endpoints[status]
            response = await self._client.post(
                f"{self._base_url}/scouting/tasks/{scout_id}/{endpoint}",
                headers=build_headers(self._api_key),
            )
            return handle_response(response)

        # Handle field updates via PATCH
        if not payload:
            raise ValueError("At least one field must be provided for update.")

        response = await self._client.patch(
            f"{self._base_url}/scouting/tasks/{scout_id}",
            headers=build_headers(self._api_key),
            json=payload,
        )
        return handle_response(response)

    async def delete(self, scout_id: str) -> dict[str, Any]:
        """Delete a scout.

        Args:
            scout_id: The unique identifier of the scout.

        Returns:
            Empty dictionary on success.
        """
        response = await self._client.delete(
            f"{self._base_url}/scouting/tasks/{scout_id}",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)

    async def get_updates(
        self,
        scout_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Get updates (reports) for a scout.

        Args:
            scout_id: The unique identifier of the scout.
            limit: Maximum number of updates to return.
            cursor: Pagination cursor for fetching more results.

        Returns:
            Dictionary containing list of updates and pagination info.
        """
        params = build_query_params(limit=limit, cursor=cursor)
        response = await self._client.get(
            f"{self._base_url}/scouting/tasks/{scout_id}/updates",
            headers=build_headers(self._api_key),
            params=params,
        )
        return handle_response(response)
