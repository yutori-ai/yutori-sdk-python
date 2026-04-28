"""Model identifiers and tool set constants for the Navigator API.

The Navigator API hosts a family of computer-use models. Current public
versions are Navigator n1 and Navigator n1.5. The API model ID strings
(``n1-latest`` / ``n1.5-latest``) are unchanged — only the user-facing
naming was updated.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------

NAVIGATOR_N1_MODEL = "n1-latest"
"""Alias for the latest stable Navigator n1 model."""

NAVIGATOR_N1_5_MODEL = "n1.5-latest"
"""Alias for the latest stable Navigator n1.5 model (current default)."""

# Back-compat aliases. Prefer the ``NAVIGATOR_*`` names above.
N1_MODEL = NAVIGATOR_N1_MODEL
N1_5_MODEL = NAVIGATOR_N1_5_MODEL

# ---------------------------------------------------------------------------
# Tool sets (Navigator n1.5 only)
# ---------------------------------------------------------------------------

TOOL_SET_CORE = "browser_tools_core-20260403"
TOOL_SET_EXPANDED = "browser_tools_expanded-20260403"
