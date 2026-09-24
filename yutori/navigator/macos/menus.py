"""Project menu paths from the existing driver's ordered window AX snapshot."""

from __future__ import annotations

from typing import Any

from ..n2_actions import is_strict_int

_SYSTEM_MENU_TITLES = frozenset({"apple"})


def menu_elements(snapshot: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Keep only addressable descendants of menu-bar items, never context-menu guesses.

    Ancestry is rebuilt from the driver's DFS depths, so it holds whether or not the
    structural AXMenu wrappers appear between a menu-bar item and its items. An unlabeled
    item (a separator, or an icon-only submenu) is kept on the stack as a hole so its
    siblings still resolve while its own descendants stay unaddressable. Duplicate paths
    stay duplicated so callers decide what ambiguity means. The Apple menu is skipped: it
    is system-owned, its items (Recent Items, Log Out, Shut Down) are never the task's,
    and Recent Items exposes the user's private history.
    """
    result: list[dict[str, Any]] = []
    ancestors: list[tuple[int, str | None]] = []
    for element in snapshot.get("elements") or []:
        if not isinstance(element, dict):
            continue
        depth = element.get("depth")
        if not is_strict_int(depth) or depth < 0:
            ancestors.clear()
            continue
        while ancestors and ancestors[-1][0] >= depth:
            ancestors.pop()
        role, label = element.get("role"), element.get("label")
        title = label.strip() if isinstance(label, str) and label.strip() else None
        if role == "AXMenuBarItem":
            ancestors.clear()
            if title is None or title.casefold() in _SYSTEM_MENU_TITLES:
                continue
        elif role != "AXMenuItem" or not ancestors:
            continue
        ancestors.append((depth, title))
        token = element.get("element_token")
        path = [title for _, title in ancestors]
        if title is not None and all(part is not None for part in path) and isinstance(token, str) and token:
            result.append({"path": path, "enabled": element.get("enabled"), "element_token": token})
    return tuple(result)
