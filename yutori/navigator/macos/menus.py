"""Project menu paths from the existing driver's ordered window AX snapshot."""

from __future__ import annotations

from typing import Any

_SYSTEM_MENU_TITLES = frozenset({"apple"})


def menu_elements(snapshot: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Keep only addressable descendants of menu-bar items, never context-menu guesses.

    Ancestry is rebuilt from the driver's DFS depths, so it holds whether or not the
    structural AXMenu wrappers appear between a menu-bar item and its items. Duplicate
    paths stay duplicated so callers can refuse ambiguity rather than silently choosing
    one. The Apple menu is skipped: it is system-owned, its items (Recent Items, Log Out,
    Shut Down) are never the task's, and Recent Items exposes the user's private history.
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
            if isinstance(label, str) and label.strip().casefold() in _SYSTEM_MENU_TITLES:
                continue
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
