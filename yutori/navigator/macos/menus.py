"""Project menu paths from the existing driver's ordered window AX snapshot."""

from __future__ import annotations

from typing import Any


def menu_elements(snapshot: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Keep only addressable descendants of menu-bar items, never context-menu guesses.

    Structural AXMenu wrappers are absent from the driver's actionable elements.
    Preserve semantic ancestry using its DFS depths. Duplicate paths stay duplicated
    so callers can refuse ambiguity rather than silently choosing one.
    """
    result: list[dict[str, Any]] = []
    ancestors: list[tuple[int, str]] = []
    for element in snapshot.get("elements") or []:
        if not isinstance(element, dict):
            continue
        depth = element.get("depth")
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            ancestors.clear()
            continue
        while ancestors and ancestors[-1][0] >= depth:
            ancestors.pop()
        role, label = element.get("role"), element.get("label")
        if role == "AXMenuBarItem":
            ancestors.clear()
        elif role != "AXMenuItem" or not ancestors:
            continue
        if not isinstance(label, str) or not label.strip():
            ancestors.clear()
            continue
        ancestors.append((depth, label))
        token = element.get("element_token")
        if isinstance(token, str) and token:
            result.append(
                {
                    "path": [title for _, title in ancestors],
                    "enabled": element.get("enabled"),
                    "element_token": token,
                }
            )
    return tuple(result)
