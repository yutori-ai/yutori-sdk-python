"""User context formatting for n1/n1.5 task messages.

Appends location, timezone, and current date/time information to a task
string, giving the model awareness of the user's environment.
"""

from __future__ import annotations

import platform
from datetime import datetime
from zoneinfo import ZoneInfo


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
    try:
        tz = ZoneInfo(user_timezone)
    except Exception:
        try:
            tz = ZoneInfo("America/Los_Angeles")
            user_timezone = str(tz)
        except Exception:
            # No IANA timezone data available (e.g. Windows without tzdata).
            tz = timezone.utc
            user_timezone = "UTC"

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
