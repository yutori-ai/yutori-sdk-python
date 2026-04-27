"""Pydantic BaseModel to JSON schema conversion utility."""

from __future__ import annotations

from typing import Any


_ERROR_MSG = "output_schema must be a dict, a Pydantic BaseModel class, or a BaseModel instance"


def _try_call_schema_method(cls: type, method_name: str) -> dict[str, Any] | None:
    """Call a Pydantic schema method on `cls` and return its dict result.

    Returns None if the method is missing or not callable. Raises TypeError
    if the method exists but errors or returns a non-dict.
    """
    method = getattr(cls, method_name, None)
    if method is None or not callable(method):
        return None
    try:
        result = method()
    except TypeError:
        raise TypeError(_ERROR_MSG) from None
    if not isinstance(result, dict):
        raise TypeError(f"{method_name}() returned {type(result).__name__}, expected dict")
    return result


def resolve_output_schema(output_schema: object | None) -> dict[str, Any] | None:
    """Convert an output_schema value to a JSON schema dict.

    Accepts:
        - None -> returns None
        - dict -> returns as-is
        - Pydantic v2 BaseModel class or instance (has callable model_json_schema)
        - Pydantic v1 BaseModel class or instance (has callable schema)

    Raises:
        TypeError: If the value is not a supported type or the schema method
            returns a non-dict.
    """
    if output_schema is None:
        return None

    if isinstance(output_schema, dict):
        return output_schema

    # Resolve instances to their class
    cls = output_schema if isinstance(output_schema, type) else type(output_schema)

    # Pydantic v2 first to avoid deprecation warnings, fall back to v1.
    for method_name in ("model_json_schema", "schema"):
        result = _try_call_schema_method(cls, method_name)
        if result is not None:
            return result

    raise TypeError(_ERROR_MSG)
