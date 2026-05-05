"""User context formatting for Navigator task messages.

Appends location, timezone, and current date/time information to a task
string, giving the model awareness of the user's environment.
"""

from __future__ import annotations

import platform
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _resolve_user_timezone(user_timezone: str) -> tuple[tzinfo, str]:
    """Resolve a tz string to a (tzinfo, display label), with safe fallbacks.

    Falls back to America/Los_Angeles, then UTC, when:
    - ZoneInfoNotFoundError: unknown key or no tzdata.
    - ValueError: path-traversal segments (e.g. "../foo").
    - OSError: on Python <3.13, IANA *directory* keys like "America" or "US"
      raise IsADirectoryError (a subclass of OSError) instead of
      ZoneInfoNotFoundError (CPython issue #85702, fixed in 3.13).
    """
    try:
        return ZoneInfo(user_timezone), user_timezone
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass
    try:
        tz = ZoneInfo("America/Los_Angeles")
        return tz, str(tz)
    except (ZoneInfoNotFoundError, OSError):
        # No IANA timezone data available (e.g. Windows without tzdata).
        return timezone.utc, "UTC"


def format_user_context(
    *,
    user_timezone: str = "America/Los_Angeles",
    user_location: str = "San Francisco, CA, US",
) -> str:
    """Build a user context block with location, timezone, and current time.

    Returns a multi-line string like::

        User's location: San Francisco, CA, US
        User's timezone: America/Los_Angeles
        Current Date: April 7, 2026
        Current Time: 10:05:49 PDT
        Today is: Tuesday
    """
    tz, user_timezone = _resolve_user_timezone(user_timezone)

    now = datetime.now(tz)

    # macOS/Linux use %-d for non-padded day; Windows uses %#d.
    day_fmt = "%#d" if platform.system() == "Windows" else "%-d"

    lines = [
        f"User's location: {user_location}",
        f"User's timezone: {user_timezone}",
        f"Current Date: {now.strftime(f'%B {day_fmt}, %Y')}",
        f"Current Time: {now.strftime('%H:%M:%S %Z')}",
        f"Today is: {now.strftime('%A')}",
    ]
    return "\n".join(lines)


def format_task_with_context(
    task: str,
    *,
    user_timezone: str = "America/Los_Angeles",
    user_location: str = "San Francisco, CA, US",
) -> str:
    """Append user context to a task string.

    Example::

        >>> format_task_with_context("Book a table for 2 tonight")
        'Book a table for 2 tonight\\n\\nUser\\'s location: San Francisco, CA, US\\n...'
    """
    context = format_user_context(
        user_timezone=user_timezone,
        user_location=user_location,
    )
    return f"{task}\n\n{context}"
