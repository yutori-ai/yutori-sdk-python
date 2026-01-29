"""Chat namespace for the Yutori SDK (async)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._http import build_headers, handle_response

if TYPE_CHECKING:
    import httpx


class AsyncChatNamespace:
    """Async namespace for n1 API operations (pixels-to-actions LLM)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    async def completions(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "n1-preview-2025-11",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Get next browser action from screenshot using the n1 API.

        The n1 API is a pixels-to-actions LLM that processes screenshots
        and predicts browser actions (click, type, scroll, etc.).

        Args:
            messages: List of messages following OpenAI Chat format.
                      Use "observation" role for screenshots.
            model: Model to use (default: "n1-preview-2025-11").
            **kwargs: Additional parameters passed to the API.

        Returns:
            Dictionary containing the model's response with predicted actions.
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "model": model,
            **kwargs,
        }

        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers=build_headers(self._api_key, auth_type="bearer"),
            json=payload,
        )
        return handle_response(response)
