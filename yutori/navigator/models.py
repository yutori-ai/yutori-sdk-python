"""Model identifiers and tool-set constants for the Navigator API."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------

NAVIGATOR_N1_MODEL = "n1-latest"
"""Alias for the latest stable Navigator n1 model."""

NAVIGATOR_N1_5_MODEL = "n1.5-latest"
"""Alias for the latest stable Navigator n1.5 model (current default)."""

NAVIGATOR_N2_PREVIEW_MODEL = "n2-preview"
"""Gated preview identifier for the Navigator n2 computer-use model."""

# Back-compat aliases. Prefer the ``NAVIGATOR_*`` names above.
N1_MODEL = NAVIGATOR_N1_MODEL
N1_5_MODEL = NAVIGATOR_N1_5_MODEL

# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------

# Navigator n1.5 browser-use tool sets.
TOOL_SET_CORE = "browser_tools_core-20260403"
TOOL_SET_EXPANDED = "browser_tools_expanded-20260403"

# Navigator n2 computer-use tool sets. The batch contract is experimental and
# must be selected explicitly; the non-batch contract remains the safe default.
TOOL_SET_COMPUTER_USE = "computer_use_tools-20260708"
TOOL_SET_COMPUTER_USE_BATCH = "computer_use_tools-20260716"
