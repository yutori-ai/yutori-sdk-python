"""Model identifiers and immutable tool-set constants for the Navigator API."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------

NAVIGATOR_N1_MODEL = "n1-latest"
"""Deprecated browser Navigator model identifier."""

NAVIGATOR_N1_5_MODEL = "n1.5-latest"
"""Current default browser Navigator model identifier."""

NAVIGATOR_N2_MODEL = "n2"
"""Stable Navigator n2 computer-use model identifier."""

# Back-compat aliases. Prefer the ``NAVIGATOR_*`` names above.
N1_MODEL = NAVIGATOR_N1_MODEL
N1_5_MODEL = NAVIGATOR_N1_5_MODEL

# ---------------------------------------------------------------------------
# Browser Navigator tool sets
# ---------------------------------------------------------------------------

TOOL_SET_CORE = "browser_tools_core-20260403"
TOOL_SET_EXPANDED = "browser_tools_expanded-20260403"

# Historical GUI-only and shell/file surfaces remain accepted because published
# dated identifiers are immutable. New callers should use the latest constant.
TOOL_SET_COMPUTER_USE = "computer_use_tools-20260708"
TOOL_SET_COMPUTER_USE_BATCH = "computer_use_tools-20260716"
TOOL_SET_COMPUTER_USE_BROWSER_BATCH = "computer_use_tools-20260818"
TOOL_SET_COMPUTER_USE_HYBRID = "computer_use_tools-20260728"
TOOL_SET_COMPUTER_USE_HYBRID_BATCH = "computer_use_tools-20260729"
TOOL_SET_COMPUTER_USE_FILES = "computer_use_tools-20260807"
TOOL_SET_COMPUTER_USE_FILES_BATCH = "computer_use_tools-20260808"
TOOL_SET_COMPUTER_USE_BASH_BATCH = "computer_use_tools-20260812"
TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS = "computer_use_tools-20260815"
TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT = "computer_use_tools-20260821"
TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL = "computer_use_tools-20260822"

# The n2 desktop surface: computer_batch, edit, read, write, and bash. Both sets expose the
# same five tools and the same batch actions; they differ in the tool descriptions and in
# computer_batch's parameter schemas. On 20260830 an optional argument is optional -- a
# left_click needs only `coordinates` -- where 20260825 marks every argument required.
TOOL_SET_COMPUTER_USE_20260825 = "computer_use_tools-20260825"
TOOL_SET_COMPUTER_USE_20260830 = "computer_use_tools-20260830"

TOOL_SET_COMPUTER_USE_LATEST = TOOL_SET_COMPUTER_USE_20260830
