"""Validation and translation for Navigator n2 computer-use actions.

The Yutori API injects its versioned computer-use tools server-side; this
module turns the model's tool calls into strictly validated, executor-ready
actions. Dated tool-set compatibility lives here so callers can safely replay
published n2 trajectories without changing their execution semantics.
"""

from __future__ import annotations

import copy
from typing import Any

from ._key_symbols import PUNCTUATION_KEY_NAMES
from .models import (
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
)

N2_COORDINATE_SCALE = 1000
N2_MAX_BATCH_ACTIONS = 20
N2_MAX_WAIT_SECONDS = 300
# A `wait` without a duration waits this long.
N2_DEFAULT_WAIT_SECONDS = 5.0
N2_MAX_SCROLL_AMOUNT = 50

SUPPORTED_N2_TOOL_SETS = frozenset(
    {
        TOOL_SET_COMPUTER_USE,
        TOOL_SET_COMPUTER_USE_BATCH,
        TOOL_SET_COMPUTER_USE_BROWSER_BATCH,
        TOOL_SET_COMPUTER_USE_HYBRID,
        TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        TOOL_SET_COMPUTER_USE_FILES,
        TOOL_SET_COMPUTER_USE_FILES_BATCH,
        TOOL_SET_COMPUTER_USE_BASH_BATCH,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)

TOOL_SETS_WITH_CLICK_MODIFIERS = frozenset(
    {
        TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)
TOOL_SETS_WITH_BATCH_SCREENSHOT = frozenset(
    {
        TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)
TOOL_SETS_WITH_FULL_BATCH = frozenset(
    {
        TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)
TOOL_SETS_WITH_BASH = frozenset(
    {
        TOOL_SET_COMPUTER_USE_BASH_BATCH,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_MODIFIERS,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_SCREENSHOT,
        TOOL_SET_COMPUTER_USE_BASH_BATCH_FULL,
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)
TOOL_SETS_WITH_FILE_TOOLS = frozenset(
    {
        TOOL_SET_COMPUTER_USE_FILES,
        TOOL_SET_COMPUTER_USE_FILES_BATCH,
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)
TOOL_SETS_WITH_LEGACY_FILE_SEARCH = frozenset(
    {
        TOOL_SET_COMPUTER_USE_FILES,
        TOOL_SET_COMPUTER_USE_FILES_BATCH,
    }
)
TOOL_SETS_WITH_BATCH = SUPPORTED_N2_TOOL_SETS - {
    TOOL_SET_COMPUTER_USE,
    TOOL_SET_COMPUTER_USE_HYBRID,
    TOOL_SET_COMPUTER_USE_FILES,
}
TOOL_SETS_WITH_BROWSER_NAVIGATION = frozenset({TOOL_SET_COMPUTER_USE_BROWSER_BATCH})
# The sets that drive the GUI through `computer_batch` alone: no standalone `screenshot`
# tool and no standalone single-action tools. Defined by membership rather than by
# subtracting "the latest set", which silently readmitted a set the moment a newer one
# was published -- 20260825 would have regained a standalone screenshot it does not serve.
TOOL_SETS_BATCH_ONLY_GUI = frozenset(
    {
        TOOL_SET_COMPUTER_USE_20260825,
        TOOL_SET_COMPUTER_USE_20260830,
    }
)
TOOL_SETS_WITH_STANDALONE_SCREENSHOT = SUPPORTED_N2_TOOL_SETS - TOOL_SETS_BATCH_ONLY_GUI
TOOL_SETS_WITH_SHELL_COMMAND = frozenset(
    {
        TOOL_SET_COMPUTER_USE_HYBRID,
        TOOL_SET_COMPUTER_USE_HYBRID_BATCH,
        TOOL_SET_COMPUTER_USE_FILES,
        TOOL_SET_COMPUTER_USE_FILES_BATCH,
    }
)

# The model may emit either name for the same shell tool.
SHELL_COMMAND_TOOL_NAMES = frozenset({"shell_command", "run_command"})
N2_SHELL_DEFAULT_TIMEOUT_SECONDS = 10
N2_SHELL_MAX_TIMEOUT_SECONDS = 30
_SHELL_COMMAND_FIELDS = {"command", "cwd", "timeout_seconds"}

# `bash` is the shell tool of the 20260812/20260815 sets, and a different
# contract from `shell_command`: a much higher timeout ceiling, a detached mode,
# and a working directory that persists across calls instead of a per-call
# `cwd`. Bounds mirror the schemas the served tool sets are built from, so what
# the model is told and what runs agree.
BASH_TOOL_NAME = "bash"
N2_BASH_DEFAULT_TIMEOUT_SECONDS = 120
N2_BASH_MAX_TIMEOUT_SECONDS = 600
_BASH_FIELDS = {"command", "timeout", "run_in_background"}

FILE_TOOL_NAMES = frozenset({"read", "write", "edit", "grep", "glob"})
LEGACY_FILE_SEARCH_TOOL_NAMES = frozenset({"grep", "glob"})
N2_FILE_READ_DEFAULT_OFFSET = 1
N2_FILE_READ_DEFAULT_LIMIT = 2_000
N2_FILE_WRITE_MAX_CHARS = 256_000
N2_GREP_DEFAULT_HEAD_LIMIT = 250
_READ_FILE_FIELDS = {"file_path", "offset", "limit"}
_WRITE_FILE_FIELDS = {"file_path", "content"}
_EDIT_FILE_FIELDS = {"file_path", "old_string", "new_string", "replace_all"}
_GREP_FIELDS = {
    "pattern",
    "path",
    "glob",
    "type",
    "output_mode",
    "-i",
    "-n",
    "-B",
    "-A",
    "-C",
    "head_limit",
    "multiline",
}
_GLOB_FIELDS = {"pattern", "path"}
_GOTO_URL_FIELDS = {"url"}

# Neither shell tool may ever join this set: a model-driven shell command is
# always confirmable.
SAFE_WITHOUT_CONFIRMATION = frozenset({"screenshot", "wait", "mouse_move", "scroll"})

# The spellings the tool schema names, for error text the model can act on.
MODIFIER_NAMES = ("ctrl", "shift", "alt", "meta", "command", "super")
_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
    "meta": "cmd",
    "cmd": "cmd",
    "command": "cmd",
    "super": "cmd",
    "win": "cmd",
    "windows": "cmd",
}

# `modifier` is accepted exactly where the served schema carries it — the click
# family and `scroll`. `drag` and `mouse_move` have no modifier slot in the
# served tool definitions, so accepting one there would promise a gesture the
# model was never taught to ask for.
MODIFIABLE_ACTIONS = frozenset({"left_click", "double_click", "triple_click", "middle_click", "right_click", "scroll"})

_KEY_ALIASES = {
    "meta": "cmd",
    "command": "cmd",
    "super": "cmd",
    "win": "cmd",
    "control": "ctrl",
    "escape": "esc",
    "pageup": "page_up",
    "pagedown": "page_down",
    "return": "enter",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    **PUNCTUATION_KEY_NAMES,
}

_ACTION_FIELDS = {
    "left_click": {"coordinates", "modifier"},
    "double_click": {"coordinates", "modifier"},
    "triple_click": {"coordinates", "modifier"},
    "middle_click": {"coordinates", "modifier"},
    "right_click": {"coordinates", "modifier"},
    "mouse_move": {"coordinates"},
    "drag": {"start_coordinates", "coordinates"},
    "scroll": {"coordinates", "direction", "amount", "modifier"},
    "type": {"text"},
    "key_press": {"key"},
    "hold_key": {"key", "duration"},
    "wait": {"duration"},
    "mouse_down": {"coordinates"},
    "mouse_up": {"coordinates"},
    "screenshot": set(),
}


class N2ActionValidationError(ValueError):
    """Raised when an n2 action cannot be safely executed."""


def is_strict_int(value: Any) -> bool:
    """True for a genuine ``int``. ``bool`` is an ``int`` subclass, so it is excluded."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_strict_number(value: Any) -> bool:
    """Like :func:`is_strict_int`, but also accepts ``float``."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _tool_arguments(args: Any, tool_name: str, allowed_fields: set[str]) -> dict[str, Any]:
    """Validate a tool-object envelope before checking its individual fields."""
    if not isinstance(args, dict):
        raise N2ActionValidationError(f"{tool_name} arguments must be an object")
    unknown = set(args) - allowed_fields
    if unknown:
        raise N2ActionValidationError(f"{tool_name} received unsupported field(s): {', '.join(sorted(unknown))}")
    return args


def normalize_modifier_args(args: dict[str, Any]) -> dict[str, Any]:
    """Fold a ``modifier_keys`` spelling into the canonical ``modifier`` one.

    The model holds a modifier through ``modifier``, a single string. Every
    tool set before 20260815 stripped the parameter, and a model whose
    served schema has no modifier slot reaches for the plural instead — which
    strict field validation then rejects, costing the whole batch rather than
    just the modifier. Accepting the plural is cheap and keeps that step.

    A list is joined into the ``ctrl+shift`` chord spelling the validator parses.
    Anything else passes through unchanged so a junk value is rejected by
    validation rather than silently dropped: an unmodified click is a different
    action, not a degraded one.
    """
    if "modifier_keys" not in args:
        return args
    rest = {key: value for key, value in args.items() if key != "modifier_keys"}
    if rest.get("modifier") is not None:
        return rest
    plural = args["modifier_keys"]
    merged = "+".join(str(part) for part in plural if str(part)) if isinstance(plural, list) else plural
    if merged is None or merged == "":
        return rest
    return {**rest, "modifier": merged}


def flatten_batch_member(value: dict[str, Any]) -> dict[str, Any]:
    """Read either batch envelope and return the flat one.

    ``{"name": "left_click", "arguments": {"coordinates": [x, y]}}`` is the shape
    tool sets 20260812/20260815 advertise. The flat
    ``{"action": "left_click", "coordinates": [x, y]}`` shape stays accepted
    because 20260807/20260808 still serve it, and a model can mix the two within
    one batch. Everything downstream sees the flat shape.
    """
    if not isinstance(value.get("name"), str) or set(value) - {"name", "arguments"}:
        return normalize_modifier_args(value)
    arguments = value.get("arguments")
    flattened = arguments if isinstance(arguments, dict) else {}
    return normalize_modifier_args({"action": value["name"], **flattened})


def parse_n2_modifier(value: Any, path: str) -> list[str]:
    """Validate a ``modifier`` argument into canonical held-key names.

    Absent/null/empty means "no modifier held", so a model that emits
    ``"modifier": null`` alongside an ordinary click gets an ordinary click
    rather than a rejected batch. A chord is accepted because holding two keys is
    a real gesture, even though the model only ever emits one.
    """
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        raise N2ActionValidationError(f"{path}.modifier must be a string")
    invalid_message = (
        f'{path}.modifier must be one or more of {", ".join(MODIFIER_NAMES)} — e.g. "ctrl" or "ctrl+shift"'
    )
    keys: list[str] = []
    for raw_part in value.split("+"):
        part = raw_part.strip().lower()
        if not part:
            continue
        mapped = _MODIFIER_ALIASES.get(part)
        if mapped is None:
            raise N2ActionValidationError(invalid_message)
        if mapped not in keys:
            keys.append(mapped)
    if not keys:
        raise N2ActionValidationError(invalid_message)
    return keys


def _map_key_token(token: str) -> str:
    token = token.strip().lower()
    if not token:
        raise N2ActionValidationError("key expressions cannot contain empty keys")
    if len(token) == 1:
        return token
    # Named keys normalize to the SDK vocabulary; names outside it pass through
    # lowercased for the computer handler to accept or reject.
    return _KEY_ALIASES.get(token, token)


def parse_n2_key_expression(expression: Any) -> list[list[str]]:
    """Parse Yutori's ``+`` combo and space-separated sequence grammar."""
    if not isinstance(expression, str) or not expression.strip():
        raise N2ActionValidationError("key must be a non-empty string")
    sequence: list[list[str]] = []
    for raw_combo in expression.split():
        raw_keys = raw_combo.split("+")
        if any(not key.strip() for key in raw_keys):
            raise N2ActionValidationError(f"invalid key combination: {raw_combo}")
        sequence.append([_map_key_token(key) for key in raw_keys])
    return sequence


def _coordinate(value: Any, path: str) -> "tuple[int, int]":
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(not is_strict_int(component) for component in value)
        or any(component < 0 or component > N2_COORDINATE_SCALE for component in value)
    ):
        raise N2ActionValidationError(f"{path} must be two integers in the inclusive 0-1000 range")
    return int(value[0]), int(value[1])


def native_point(value: Any, path: str, width: int, height: int) -> "tuple[int, int]":
    """Map one model coordinate pair from the 0-1000 space to native pixels."""
    if width <= 0 or height <= 0:
        raise N2ActionValidationError("native screenshot dimensions must be positive")
    x, y = _coordinate(value, path)
    return (
        min(width - 1, round((x / N2_COORDINATE_SCALE) * width)),
        min(height - 1, round((y / N2_COORDINATE_SCALE) * height)),
    )


def _validate_wait_seconds(duration: Any, field: str) -> "int | float":
    """Validate a ``hold_key``/``wait`` duration: a number in [0, N2_MAX_WAIT_SECONDS]."""
    if not is_strict_number(duration) or not 0 <= duration <= N2_MAX_WAIT_SECONDS:
        raise N2ActionValidationError(f"{field} must be between 0 and {N2_MAX_WAIT_SECONDS} seconds")
    return duration


def _validate_fields(action: str, args: dict[str, Any]) -> None:
    if action not in _ACTION_FIELDS:
        raise N2ActionValidationError(f"unsupported n2 action: {action}")
    _tool_arguments(args, action, _ACTION_FIELDS[action])


def translate_n2_action(
    action: str,
    args: dict[str, Any],
    native_width: int,
    native_height: int,
    *,
    batch_index: "int | None" = None,
    allow_click_modifiers: bool = False,
    allow_scroll_modifiers: "bool | None" = None,
) -> list[dict[str, Any]]:
    """Strictly validate and translate one Yutori action to computer-handler calls."""
    if not isinstance(args, dict):
        raise N2ActionValidationError(f"{action} arguments must be an object")
    args = normalize_modifier_args(args)
    _validate_fields(action, args)

    modifier = parse_n2_modifier(args.get("modifier"), action) if action in MODIFIABLE_ACTIONS else []
    modifier_allowed = (
        allow_click_modifiers if action != "scroll" or allow_scroll_modifiers is None else allow_scroll_modifiers
    )
    if modifier and not modifier_allowed:
        # No fallback to the unmodified gesture on purpose: a ctrl-click that
        # lands as a click opens the file the model meant to add to a selection.
        raise N2ActionValidationError(
            f"{action} with a held modifier is not supported by this computer handler "
            "— use key_press for keyboard shortcuts"
        )
    args = {key: value for key, value in args.items() if key != "modifier"}
    modifier_args = {"modifier": modifier} if modifier else {}

    def internal(action_type: str, **kwargs: Any) -> dict[str, Any]:
        result = {"type": action_type, **kwargs}
        if batch_index is not None:
            result["batch_index"] = batch_index
        return result

    if action in {
        "left_click",
        "double_click",
        "triple_click",
        "middle_click",
        "right_click",
        "mouse_move",
    }:
        x, y = native_point(args.get("coordinates"), f"{action}.coordinates", native_width, native_height)
        if action == "left_click":
            return [internal("click", x=x, y=y, button="left", **modifier_args)]
        if action == "right_click":
            return [internal("click", x=x, y=y, button="right", **modifier_args)]
        if action == "middle_click":
            return [internal("click", x=x, y=y, button="middle", **modifier_args)]
        if action == "double_click":
            return [internal("double_click", x=x, y=y, **modifier_args)]
        if action == "triple_click":
            return [internal("triple_click", x=x, y=y, **modifier_args)]
        return [internal("move", x=x, y=y)]

    if action in {"mouse_down", "mouse_up"}:
        coordinates = args.get("coordinates")
        action_type = "left_mouse_down" if action == "mouse_down" else "left_mouse_up"
        if coordinates is None:
            return [internal(action_type)]
        x, y = native_point(coordinates, f"{action}.coordinates", native_width, native_height)
        return [internal(action_type, x=x, y=y)]

    if action == "drag":
        start_x, start_y = native_point(
            args.get("start_coordinates"),
            "drag.start_coordinates",
            native_width,
            native_height,
        )
        end_x, end_y = native_point(args.get("coordinates"), "drag.coordinates", native_width, native_height)
        return [
            internal(
                "drag",
                path=[{"x": start_x, "y": start_y}, {"x": end_x, "y": end_y}],
            )
        ]

    if action == "scroll":
        x, y = native_point(args.get("coordinates"), "scroll.coordinates", native_width, native_height)
        direction = args.get("direction")
        if direction not in {"up", "down", "left", "right"}:
            raise N2ActionValidationError("scroll.direction must be up, down, left, or right")
        amount = args.get("amount")
        if not is_strict_int(amount) or not 1 <= amount <= N2_MAX_SCROLL_AMOUNT:
            raise N2ActionValidationError(f"scroll.amount must be an integer between 1 and {N2_MAX_SCROLL_AMOUNT}")
        # All four directions the served schema names are valid; a handler that
        # cannot scroll horizontally fails that one action rather than the call.
        if direction in {"up", "down"}:
            scroll_x = 0
            scroll_y = round(amount * native_height * 0.1) * (1 if direction == "down" else -1)
        else:
            scroll_x = round(amount * native_width * 0.1) * (1 if direction == "right" else -1)
            scroll_y = 0
        return [
            internal(
                "scroll",
                x=x,
                y=y,
                scroll_x=scroll_x,
                scroll_y=scroll_y,
                **modifier_args,
            )
        ]

    if action == "type":
        text = args.get("text")
        if not isinstance(text, str):
            raise N2ActionValidationError("type.text must be a string")
        return [internal("type", text=text)]

    if action == "key_press":
        return [internal("keypress", keys=keys) for keys in parse_n2_key_expression(args.get("key"))]

    if action == "hold_key":
        sequence = parse_n2_key_expression(args.get("key"))
        if len(sequence) != 1 or len(sequence[0]) != 1:
            raise N2ActionValidationError("hold_key.key must name exactly one key")
        if "duration" not in args:
            return [internal("hold_key_until_next_action", key=sequence[0][0])]
        duration = _validate_wait_seconds(args["duration"], "hold_key.duration")
        return [internal("hold_key", key=sequence[0][0], ms=round(float(duration) * 1000))]

    if action == "wait":
        duration = _validate_wait_seconds(args.get("duration", N2_DEFAULT_WAIT_SECONDS), "wait.duration")
        return [internal("wait", ms=round(float(duration) * 1000))]

    if action == "screenshot":
        return [internal("screenshot")]

    raise N2ActionValidationError(f"unsupported n2 action: {action}")


def translate_n2_shell_command(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Strictly validate one shell_command call into an executor shell action.

    Mirrors the server schema: ``command`` required non-empty, ``cwd`` optional
    string, ``timeout_seconds`` an optional integer in [1, 30] defaulting to 10.
    The executor duck-types the optional ``run_shell_command`` handler method.
    """
    args = _tool_arguments(args, "shell_command", _SHELL_COMMAND_FIELDS)
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise N2ActionValidationError("shell_command requires a non-empty command string")
    cwd = args.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise N2ActionValidationError("shell_command.cwd must be a string")
    timeout_seconds = args.get("timeout_seconds", N2_SHELL_DEFAULT_TIMEOUT_SECONDS)
    if not is_strict_int(timeout_seconds) or not 1 <= timeout_seconds <= N2_SHELL_MAX_TIMEOUT_SECONDS:
        raise N2ActionValidationError(
            f"shell_command.timeout_seconds must be an integer between 1 and {N2_SHELL_MAX_TIMEOUT_SECONDS}"
        )
    action: dict[str, Any] = {
        "type": "run_shell_command",
        "command": command,
        "timeout_seconds": timeout_seconds,
    }
    if cwd:
        action["cwd"] = cwd
    return [action]


def translate_n2_bash(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Strictly validate one ``bash`` call into an executor shell action.

    Mirrors the served schema: ``command`` required non-empty, ``timeout`` a
    number in [0, 600] defaulting to 120, and ``run_in_background`` a boolean
    defaulting to false. There is deliberately no ``cwd``: ``bash``'s contract
    is that the working directory persists across calls, so a per-call override
    would contradict what the model is told.
    """
    args = _tool_arguments(args, "bash", _BASH_FIELDS)
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise N2ActionValidationError("bash requires a non-empty command string")
    timeout = args.get("timeout", N2_BASH_DEFAULT_TIMEOUT_SECONDS)
    if not is_strict_number(timeout) or not 0 <= timeout <= N2_BASH_MAX_TIMEOUT_SECONDS:
        raise N2ActionValidationError(
            f"bash.timeout must be a number between 0 and {N2_BASH_MAX_TIMEOUT_SECONDS} seconds"
        )
    run_in_background = args.get("run_in_background", False)
    if not isinstance(run_in_background, bool):
        raise N2ActionValidationError("bash.run_in_background must be a boolean")
    return [
        {
            "type": "run_bash_command",
            "command": command,
            "timeout": float(timeout),
            "run_in_background": run_in_background,
        }
    ]


def _file_path(args: dict[str, Any], tool_name: str) -> str:
    file_path = args.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise N2ActionValidationError(f"{tool_name}.file_path must be a non-empty string")
    return file_path


def translate_n2_read(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a read call for the current desktop file-tool contract."""
    args = _tool_arguments(args, "read", _READ_FILE_FIELDS)
    offset = args.get("offset", N2_FILE_READ_DEFAULT_OFFSET)
    if not is_strict_int(offset) or offset < 1:
        raise N2ActionValidationError("read.offset must be a positive 1-based integer")
    limit = args.get("limit", N2_FILE_READ_DEFAULT_LIMIT)
    if not is_strict_int(limit) or limit <= 0:
        raise N2ActionValidationError("read.limit must be a positive integer")
    return [internal_file_action("read_file", file_path=_file_path(args, "read"), offset=offset, limit=limit)]


def translate_n2_write(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a write call without routing its content through a shell."""
    args = _tool_arguments(args, "write", _WRITE_FILE_FIELDS)
    content = args.get("content")
    if not isinstance(content, str):
        raise N2ActionValidationError("write.content must be a string")
    if len(content) > N2_FILE_WRITE_MAX_CHARS:
        raise N2ActionValidationError(f"write.content must not exceed {N2_FILE_WRITE_MAX_CHARS} characters")
    return [internal_file_action("write_file", file_path=_file_path(args, "write"), content=content)]


def translate_n2_edit(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate an exact-string edit call for a previously observed file."""
    args = _tool_arguments(args, "edit", _EDIT_FILE_FIELDS)
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    replace_all = args.get("replace_all", False)
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        raise N2ActionValidationError("edit.old_string and edit.new_string must be strings")
    if old_string == new_string:
        raise N2ActionValidationError("edit.new_string must differ from edit.old_string")
    if not isinstance(replace_all, bool):
        raise N2ActionValidationError("edit.replace_all must be a boolean")
    return [
        internal_file_action(
            "edit_file",
            file_path=_file_path(args, "edit"),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
    ]


def _optional_string(args: dict[str, Any], field: str, tool_name: str) -> str | None:
    value = args.get(field)
    if value is not None and not isinstance(value, str):
        raise N2ActionValidationError(f"{tool_name}.{field} must be a string or null")
    return value


def _optional_boolean(args: dict[str, Any], field: str, tool_name: str, *, default: bool | None = False) -> bool | None:
    value = args.get(field, default)
    if value is not None and not isinstance(value, bool):
        raise N2ActionValidationError(f"{tool_name}.{field} must be a boolean or null")
    return value


def _optional_nonnegative_integer(
    args: dict[str, Any], field: str, tool_name: str, *, default: int | None = None
) -> int | None:
    value = args.get(field, default)
    if value is not None and (not is_strict_int(value) or value < 0):
        raise N2ActionValidationError(f"{tool_name}.{field} must be a non-negative integer or null")
    return value


def translate_n2_grep(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the immutable 20260807/20260808 file-search tool contract."""
    args = _tool_arguments(args, "grep", _GREP_FIELDS)
    pattern = args.get("pattern")
    if not isinstance(pattern, str):
        raise N2ActionValidationError("grep.pattern must be a string")
    output_mode = args.get("output_mode", "files_with_matches")
    if output_mode is None:
        output_mode = "files_with_matches"
    if output_mode not in {"content", "files_with_matches", "count"}:
        raise N2ActionValidationError("grep.output_mode must be content, files_with_matches, count, or null")
    head_limit = _optional_nonnegative_integer(args, "head_limit", "grep", default=N2_GREP_DEFAULT_HEAD_LIMIT)
    return [
        internal_file_action(
            "grep_files",
            pattern=pattern,
            path=_optional_string(args, "path", "grep"),
            glob_pattern=_optional_string(args, "glob", "grep"),
            file_type=_optional_string(args, "type", "grep"),
            output_mode=output_mode,
            ignore_case=bool(_optional_boolean(args, "-i", "grep")),
            show_line_numbers=_optional_boolean(args, "-n", "grep", default=None),
            before_context=_optional_nonnegative_integer(args, "-B", "grep"),
            after_context=_optional_nonnegative_integer(args, "-A", "grep"),
            context=_optional_nonnegative_integer(args, "-C", "grep"),
            head_limit=head_limit,
            multiline=bool(_optional_boolean(args, "multiline", "grep")),
        )
    ]


def translate_n2_glob(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the immutable 20260807/20260808 file-name search contract."""
    args = _tool_arguments(args, "glob", _GLOB_FIELDS)
    pattern = args.get("pattern")
    if not isinstance(pattern, str):
        raise N2ActionValidationError("glob.pattern must be a string")
    if "path" in args and not isinstance(args["path"], str):
        raise N2ActionValidationError("glob.path must be a string")
    path = args.get("path")
    return [internal_file_action("glob_files", pattern=pattern, path=path)]


def translate_n2_goto_url(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the browser-only navigation tool in the immutable 20260818 set."""
    args = _tool_arguments(args, "goto_url", _GOTO_URL_FIELDS)
    url = args.get("url")
    if not isinstance(url, str) or not url:
        raise N2ActionValidationError("goto_url.url must be a non-empty string")
    return [{"type": "goto_url", "url": url}]


def internal_file_action(action_type: str, **kwargs: Any) -> dict[str, Any]:
    """Construct a file action in the computer-handler protocol."""
    return {"type": action_type, **kwargs}


def translate_n2_batch(
    args: dict[str, Any],
    native_width: int,
    native_height: int,
    *,
    tool_set: str = TOOL_SET_COMPUTER_USE_LATEST,
    allow_click_modifiers: bool = False,
    allow_scroll_modifiers: "bool | None" = None,
) -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
    """Validate a complete batch before returning any executable actions."""
    if not isinstance(args, dict) or set(args) != {"actions"}:
        raise N2ActionValidationError("computer_batch requires exactly one actions field")
    raw_actions = args.get("actions")
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= N2_MAX_BATCH_ACTIONS:
        raise N2ActionValidationError(f"computer_batch.actions must contain 1-{N2_MAX_BATCH_ACTIONS} actions")

    translated: list[dict[str, Any]] = []
    validated: list[dict[str, Any]] = []
    for index, raw_action in enumerate(raw_actions):
        if not isinstance(raw_action, dict):
            raise N2ActionValidationError(f"computer_batch.actions[{index}] must be an object")
        member = flatten_batch_member(raw_action)
        action = member.get("action")
        if not isinstance(action, str):
            raise N2ActionValidationError(f"computer_batch.actions[{index}].action is required")
        if action == "screenshot":
            if tool_set not in TOOL_SETS_WITH_BATCH_SCREENSHOT:
                raise N2ActionValidationError(f"{tool_set} does not allow screenshot inside computer_batch")
        if action in SHELL_COMMAND_TOOL_NAMES or action == BASH_TOOL_NAME:
            raise N2ActionValidationError(f"{action} is not allowed inside computer_batch")
        if action in {"mouse_down", "mouse_up", "hold_key"} and tool_set not in TOOL_SETS_WITH_FULL_BATCH:
            raise N2ActionValidationError(f"{tool_set} does not allow {action} inside computer_batch")
        member_args = {key: value for key, value in member.items() if key != "action"}
        if (
            parse_n2_modifier(member_args.get("modifier"), f"computer_batch.actions[{index}]")
            and tool_set not in TOOL_SETS_WITH_CLICK_MODIFIERS
        ):
            raise N2ActionValidationError(f"{tool_set} does not allow a held modifier inside computer_batch")
        translated.extend(
            translate_n2_action(
                action,
                member_args,
                native_width,
                native_height,
                batch_index=index,
                allow_click_modifiers=allow_click_modifiers,
                allow_scroll_modifiers=allow_scroll_modifiers,
            )
        )
        # The flattened member, not the raw one: confirmation prompts then render
        # one shape regardless of which envelope arrived.
        validated.append(copy.deepcopy(member))
    return validated, translated
