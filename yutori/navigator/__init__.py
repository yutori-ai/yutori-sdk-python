"""Utilities for building agents with the Yutori Navigator API.

Provides reusable helpers for common Navigator n1.5 browser and
Navigator n2 desktop computer-use agent loop patterns:

- Screenshot preparation: capture and encode screenshots as optimized WebP data URLs
- Coordinate conversion: map the 1000x1000 tool-call space to viewport pixels
- Payload management: trim old screenshots to stay within API size limits
- Loop helpers: create trimmed requests without mutating caller state
- Key mapping: convert Navigator n1.5 lowercase key names to Playwright-compatible names
- Model constants: canonical model identifiers and tool set names
"""

from __future__ import annotations

from .content import extract_text_content
from .context import format_task_with_context, format_user_context
from .coordinates import (
    N1_COORDINATE_SCALE,
    NAVIGATOR_COORDINATE_SCALE,
    denormalize_coordinates,
    normalize_coordinates,
)
from .hooks import RunHooksBase
from .images import (
    aplaywright_screenshot_to_data_url,
    playwright_screenshot_to_data_url,
    screenshot_to_data_url,
)
from .keys import map_key_to_playwright, map_keys_individual
from .loop import acreate_trimmed, create_trimmed
from .models import (
    N1_5_MODEL,
    N1_MODEL,
    NAVIGATOR_N1_5_MODEL,
    NAVIGATOR_N1_MODEL,
    NAVIGATOR_N2_MODEL,
    TOOL_SET_COMPUTER_USE,
    TOOL_SET_COMPUTER_USE_20260825,
    TOOL_SET_COMPUTER_USE_20260830,
    TOOL_SET_COMPUTER_USE_BASH_BATCH,
    TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
    TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
    TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
    TOOL_SET_COMPUTER_USE_BATCH,
    TOOL_SET_COMPUTER_USE_BROWSER_BATCH,
    TOOL_SET_COMPUTER_USE_FILES,
    TOOL_SET_COMPUTER_USE_FILES_BATCH,
    TOOL_SET_COMPUTER_USE_HYBRID,
    TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
    TOOL_SET_COMPUTER_USE_LATEST,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
)
from .n2 import (
    TOOL_CALL_FORMAT_NUDGE,
    N2Computer,
    N2ComputerAgent,
    convert_n2_items_to_completion_messages,
    execute_n2_computer_call,
    needs_tool_call_format_nudge,
    parse_n2_tool_calls,
)
from .n2_actions import (
    SUPPORTED_N2_TOOL_SETS,
    N2ActionValidationError,
    flatten_batch_member,
    parse_n2_key_expression,
    translate_n2_action,
    translate_n2_bash,
    translate_n2_batch,
    translate_n2_edit,
    translate_n2_glob,
    translate_n2_goto_url,
    translate_n2_grep,
    translate_n2_read,
    translate_n2_shell_command,
    translate_n2_write,
)
from .n2_compaction import N2CompactionContext, N2CompactionResult, N2Compactor, N2InlineCompactor
from .n2_payload import (
    DEFAULT_IMAGE_FORMAT,
    OLDER_IMAGE_OMITTED_TEXT,
    prepare_n2_image_data_url,
    retain_n2_image_window,
)
from .payload import (
    DEFAULT_MAX_REQUEST_BYTES,
    estimate_messages_size_bytes,
    trim_images_to_fit,
    trimmed_messages_to_fit,
)
from .sandbox_tools import (
    FILE_TOOL_SCRIPT,
    ShellFileToolsMixin,
    format_shell_output,
    python_file_tool_command,
    render_image_result,
)
from .stop import format_stop_and_summarize

__all__ = [
    "N1_5_MODEL",
    "N1_COORDINATE_SCALE",
    "N1_MODEL",
    "NAVIGATOR_COORDINATE_SCALE",
    "NAVIGATOR_N1_5_MODEL",
    "NAVIGATOR_N1_MODEL",
    "NAVIGATOR_N2_MODEL",
    "N2ActionValidationError",
    "N2CompactionContext",
    "N2CompactionResult",
    "N2Computer",
    "N2ComputerAgent",
    "N2Compactor",
    "N2InlineCompactor",
    "DEFAULT_IMAGE_FORMAT",
    "OLDER_IMAGE_OMITTED_TEXT",
    "SUPPORTED_N2_TOOL_SETS",
    "DEFAULT_MAX_REQUEST_BYTES",
    "RunHooksBase",
    "TOOL_SET_COMPUTER_USE",
    "TOOL_SET_COMPUTER_USE_BASH_BATCH",
    "TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL",
    "TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS",
    "TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT",
    "TOOL_SET_COMPUTER_USE_BATCH",
    "TOOL_SET_COMPUTER_USE_BROWSER_BATCH",
    "TOOL_SET_COMPUTER_USE_FILES",
    "TOOL_SET_COMPUTER_USE_FILES_BATCH",
    "TOOL_SET_COMPUTER_USE_HYBRID",
    "TOOL_SET_COMPUTER_USE_HYBRID_BATCH",
    "TOOL_SET_COMPUTER_USE_20260825",
    "TOOL_SET_COMPUTER_USE_20260830",
    "TOOL_SET_COMPUTER_USE_LATEST",
    "TOOL_SET_CORE",
    "TOOL_SET_EXPANDED",
    "acreate_trimmed",
    "aplaywright_screenshot_to_data_url",
    "convert_n2_items_to_completion_messages",
    "create_trimmed",
    "denormalize_coordinates",
    "estimate_messages_size_bytes",
    "execute_n2_computer_call",
    "extract_text_content",
    "flatten_batch_member",
    "FILE_TOOL_SCRIPT",
    "ShellFileToolsMixin",
    "format_shell_output",
    "python_file_tool_command",
    "render_image_result",
    "format_stop_and_summarize",
    "format_task_with_context",
    "format_user_context",
    "map_key_to_playwright",
    "map_keys_individual",
    "normalize_coordinates",
    "parse_n2_key_expression",
    "parse_n2_tool_calls",
    "playwright_screenshot_to_data_url",
    "prepare_n2_image_data_url",
    "retain_n2_image_window",
    "screenshot_to_data_url",
    "translate_n2_action",
    "translate_n2_bash",
    "translate_n2_batch",
    "translate_n2_edit",
    "translate_n2_glob",
    "translate_n2_goto_url",
    "translate_n2_grep",
    "translate_n2_read",
    "translate_n2_shell_command",
    "translate_n2_write",
    "trim_images_to_fit",
    "trimmed_messages_to_fit",
    "needs_tool_call_format_nudge",
    "TOOL_CALL_FORMAT_NUDGE",
]
