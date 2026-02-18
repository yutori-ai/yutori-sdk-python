"""Pydantic BaseModel to JSON schema conversion utility."""

from __future__ import annotations

from typing import Any


_ERROR_MSG = "output_schema must be a dict, a Pydantic BaseModel class, or a BaseModel instance"


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

    # Pydantic v2: model_json_schema (check before v1 to avoid deprecation warnings)
    v2_method = getattr(cls, "model_json_schema", None)
    if v2_method is not None and callable(v2_method):
        try:
            result = v2_method()
        except TypeError:
            raise TypeError(_ERROR_MSG) from None
        if not isinstance(result, dict):
            raise TypeError(f"model_json_schema() returned {type(result).__name__}, expected dict")
        return result

    # Pydantic v1: schema
    v1_method = getattr(cls, "schema", None)
    if v1_method is not None and callable(v1_method):
        try:
            result = v1_method()
        except TypeError:
            raise TypeError(_ERROR_MSG) from None
        if not isinstance(result, dict):
            raise TypeError(f"schema() returned {type(result).__name__}, expected dict")
        return result

    raise TypeError(_ERROR_MSG)
