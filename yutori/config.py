"""Configuration helpers for the Yutori SDK."""

from __future__ import annotations

DEFAULT_BASE_URL = "https://api.yutori.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Model-call retries for the Navigator (chat) namespace. The bundled OpenAI client retries
# connection errors, timeouts and 429/5xx with exponential backoff, honoring `Retry-After`.
#
# 4 rather than the client's own default of 2: a long-horizon agent run issues hundreds of
# sequential model calls, so a single unretried upstream blip ends the whole run, and two
# retries span under ~2s of backoff. A brief gateway incident (measured: three ~2-minute
# bursts of `upstream_error` on 2026-08-31) outlasts that. Raise `max_retries` further for
# unattended batch work; the ceiling is a caller's patience, not correctness, since every
# retried request is idempotent.
DEFAULT_MAX_RETRIES = 4


def sanitize_base_url(url: str) -> str:
    """Ensure the base URL never ends with a trailing slash."""

    return url.rstrip("/")
