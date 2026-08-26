"""Model identifiers and tool set constants for the Navigator API."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------

NAVIGATOR_N1_MODEL = "n1-latest"
"""Deprecated browser Navigator model identifier."""

NAVIGATOR_N1_5_MODEL = "n1.5-latest"
"""Current default browser Navigator model identifier."""

# Back-compat aliases. Prefer the ``NAVIGATOR_*`` names above.
N1_MODEL = NAVIGATOR_N1_MODEL
N1_5_MODEL = NAVIGATOR_N1_5_MODEL

# ---------------------------------------------------------------------------
# Browser Navigator tool sets
# ---------------------------------------------------------------------------

TOOL_SET_CORE = "browser_tools_core-20260403"
TOOL_SET_EXPANDED = "browser_tools_expanded-20260403"
