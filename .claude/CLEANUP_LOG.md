# Cleanup Run Log

## 2026-04-03

**Action taken:** Replaced deprecated `Optional[Any]` with `Any | None` in `yutori/exceptions.py` and removed unused `Optional` import. This was the only file in the codebase still using the old pattern.

**Merged:** PR #24 (commit `a2643dc`)

**Other findings:** Identified 8 additional cleanup items across yutori-sdk-python, yutori-mcp, and halluminate. Added to `.claude/CLEANUP.md`.
