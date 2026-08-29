"""Context compaction for the Navigator n2 loop.

:class:`N2ComputerAgent <yutori.navigator.n2.N2ComputerAgent>` calls its
``compactor`` before every model request with the trajectory so far. A compactor
returns a replacement trajectory (typically the task, a summary of the work done,
and the most recent turns) or ``None`` to leave it unchanged. This module holds
the protocol; implementations plug in through ``N2ComputerAgent(compactor=...)``.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class N2Compactor(Protocol):
    """Rewrites the trajectory before a model call once the context grows too large.

    ``items`` is the full trajectory so far (responses-items dicts as kept by
    :class:`N2ComputerAgent`); ``last_usage`` is the previous response's usage
    dict (its ``prompt_tokens`` is the usual trigger). Return a replacement
    trajectory, or ``None`` to leave it unchanged.
    """

    async def compact(
        self,
        items: list[dict[str, Any]],
        *,
        last_usage: dict[str, Any],
        completions: Any,
        model: str,
        tool_set: str,
    ) -> Optional[list[dict[str, Any]]]: ...
