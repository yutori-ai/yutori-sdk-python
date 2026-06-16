# Refactoring Backlog — yutori-sdk-python

This file tracks refactoring candidates for automated runs. Checked items have
been completed; open items are sized and scoped for future runs.

## Open Items

- [ ] **Sync/async namespace duplication** — `yutori/_sync/` and `yutori/_async/`
  are near-identical mirrors (`scouts.py`, `browsing.py`, `research.py`,
  `chat.py`). The only difference is `def` vs `async def` and `await`. Total
  duplication is ~700 lines. Eliminating this would require a codegen step
  (like Anthropic SDK's `_utils/transform.py` pattern) or a generic
  `awaitable_or_not` wrapper — both are significant changes to the SDK
  architecture. Too large for a single automated run; defer until the team
  decides on the approach. **Do not refactor piecemeal.**

## Completed Items

_(none yet — this file was created to initialize the backlog)_

## Investigation Notes

- `yutori/_http.py` is well-factored: `_BaseNamespace`, `_SyncBaseNamespace`,
  `_AsyncBaseNamespace` share HTTP plumbing; `build_payload_with_schema`,
  `prepare_scout_update`, `apply_chat_extra_body`, `build_query_params` are
  shared utilities used by both sync and async namespaces.
- No small isolated duplication found — the sync/async pattern is the only
  major source of repeated code and it is intentional design.
