# Cleanup Backlog

Items identified for future cleanup runs. Pick one per run.

## yutori-sdk-python

- [ ] `cli/main.py:48` — Unused `_ = version` assignment in `main()` callback. Remove or document why the parameter must exist.
- [ ] `cli/__init__.py`, `cli/commands/__init__.py` — Missing `__all__` exports (other subpackages define them).

## yutori-mcp

- [ ] `formatters.py:7` — Unused constant `DEFAULT_LIMIT = 10` (never referenced anywhere).
- [ ] `schemas.py` — Duplicate `validate_webhook_url` method copied identically in 4 classes; extract to shared validator.
- [ ] `schemas.py:119-127` — `output_fields` field defined after a `@field_validator`, breaking conventional Pydantic class ordering.

## halluminate

- [ ] `navi_bench/demo.py:70` — Typo: "generat the task config" should be "generate the task config".
- [ ] `navi_bench/demo.py:19` — Deprecated `Dict[str, Any]` import; use `dict[str, Any]` (already has `from __future__ import annotations`).
- [ ] `metrics/opentable/opentable_refresh_dates.py:11` — Deprecated `List, Tuple` imports from `typing`.
