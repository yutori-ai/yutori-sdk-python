"""Error types for the SDK Playwright action runtime."""

from __future__ import annotations


class PlaywrightActionError(Exception):
    """Raised when a browser-use tool call cannot be executed against Playwright."""
