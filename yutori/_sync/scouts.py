"""Scouts namespace for the Yutori SDK (sync)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._http import (
    build_headers,
    build_payload,
    build_query_params,
    handle_response,
    resolve_scout_status_endpoint,
)
from .._schema import resolve_output_schema

if TYPE_CHECKING:
    import httpx


class ScoutsNamespace:
    """Namespace for scout-related operations (continuous web monitoring)."""

    def __init__(self, client: httpx.Client, base_url: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    def list(
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
        # API pagination parameter is `page_size`; keep `limit` for SDK ergonomics.
        params = build_query_params(page_size=limit, status=status)
        response = self._client.get(
            f"{self._base_url}/scouting/tasks",
            headers=build_headers(self._api_key),
            params=params,
        )
        return handle_response(response)

    def get(self, scout_id: str) -> dict[str, Any]:
        """Get details of a specific scout.

        Args:
            scout_id: The unique identifier of the scout.

        Returns:
            Dictionary containing scout details.
        """
        response = self._client.get(
            f"{self._base_url}/scouting/tasks/{scout_id}",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)

    def create(
        self,
        query: str,
        *,
        output_interval: int = 86400,
        start_timestamp: int | None = None,
        user_timezone: str | None = None,
        user_location: str | None = None,
        output_schema: object | None = None,
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
            output_schema: JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance.
            skip_email: Disable email notifications.
            webhook_url: URL for completion notifications.
            webhook_format: "scout" (default), "slack", or "zapier".
            is_public: Whether the scout is publicly visible.

        Returns:
            Dictionary containing created scout details.
        """
        payload = build_payload(
            query=query,
            output_interval=output_interval,
            start_timestamp=start_timestamp,
            user_timezone=user_timezone,
            user_location=user_location,
            output_schema=resolve_output_schema(output_schema),
            skip_email=skip_email,
            webhook_url=webhook_url,
            webhook_format=webhook_format,
            is_public=is_public,
        )

        response = self._client.post(
            f"{self._base_url}/scouting/tasks",
            headers=build_headers(self._api_key),
            json=payload,
        )
        return handle_response(response)

    def update(
        self,
        scout_id: str,
        *,
        query: str | None = None,
        status: str | None = None,
        output_interval: int | None = None,
        user_timezone: str | None = None,
        user_location: str | None = None,
        output_schema: object | None = None,
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
            output_schema: JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance.
            skip_email: Updated email notification setting.
            webhook_url: Updated webhook URL.
            webhook_format: Updated webhook format.

        Returns:
            Dictionary containing updated scout details.

        Raises:
            ValueError: If status is provided along with other fields (API limitation).
        """
        payload = build_payload(
            query=query,
            output_interval=output_interval,
            user_timezone=user_timezone,
            user_location=user_location,
            output_schema=resolve_output_schema(output_schema),
            skip_email=skip_email,
            webhook_url=webhook_url,
            webhook_format=webhook_format,
        )

        # Check for conflicting parameters - API doesn't support both in one call
        if status is not None and payload:
            raise ValueError(
                "Cannot update status and other fields simultaneously. "
                "The API requires separate calls: one for status change, another for field updates."
            )

        # Handle status changes via dedicated endpoints
        if status is not None:
            endpoint = resolve_scout_status_endpoint(status)
            response = self._client.post(
                f"{self._base_url}/scouting/tasks/{scout_id}/{endpoint}",
                headers=build_headers(self._api_key),
            )
            return handle_response(response)

        # Handle field updates via PATCH
        if not payload:
            raise ValueError("At least one field must be provided for update.")

        response = self._client.patch(
            f"{self._base_url}/scouting/tasks/{scout_id}",
            headers=build_headers(self._api_key),
            json=payload,
        )
        return handle_response(response)

    def delete(self, scout_id: str) -> dict[str, Any]:
        """Delete a scout.

        Args:
            scout_id: The unique identifier of the scout.

        Returns:
            Empty dictionary on success.
        """
        response = self._client.delete(
            f"{self._base_url}/scouting/tasks/{scout_id}",
            headers=build_headers(self._api_key),
        )
        return handle_response(response)

    def get_updates(
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
        response = self._client.get(
            f"{self._base_url}/scouting/tasks/{scout_id}/updates",
            headers=build_headers(self._api_key),
            params=params,
        )
        return handle_response(response)
