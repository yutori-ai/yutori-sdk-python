"""Context compaction for the Navigator n2 loop.

:class:`N2ComputerAgent <yutori.navigator.n2.N2ComputerAgent>` calls its
``compactor`` before every model request with the trajectory so far. A compactor
returns a replacement trajectory (typically the task, a summary of the work done,
and the most recent turns) or ``None`` to leave it unchanged.

Use :class:`N2InlineCompactor` for Yutori's Praxis-compatible policy, or implement
the :class:`N2Compactor` protocol for a custom harness policy.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Union

from .n2_actions import is_strict_int

COMPACTED_SUMMARY_OPEN_TAG = "<conversation_compaction_summary>"
COMPACTED_SUMMARY_CLOSE_TAG = "</conversation_compaction_summary>"

_LOGICAL_REQUEST_ID_FIELD = "yutori_logical_request_id"
_LOGICAL_ATTEMPT_FIELD = "yutori_logical_attempt"
_COMPACTION_KIND_KEY = "_n2_compaction_kind"
_WORKING_CHECKPOINT_KIND = "working_checkpoint"
_IMAGE_TOKEN_ESTIMATE = 1_600

_RETAINED_TAIL_NOTE = (
    "The session's most recent turns are retained verbatim alongside the checkpoint and are not shown "
    "here — cover the removed history exactly as shown, and do not guess at what happened after it."
)
_EMPTY_TAIL_NOTE = (
    "Nothing beyond what is shown is retained: the checkpoint replaces the entire history after the "
    "original user request, so cover it fully — including the most recent turns shown."
)
_COMPACTION_PROMPT = """## Internal compaction request (not a user instruction)

Pause the task. Do not call tools, take actions, or continue the conversation. Instead, produce a context
checkpoint of the conversation shown above. Everything shown after the original user request (and after any
prior working checkpoint) is being removed and replaced by your checkpoint. {retention_note}

If a <working_checkpoint> appears earlier in the conversation, UPDATE it rather than starting over: preserve
everything still true, move finished items from In Progress to Done, drop resolved blockers and superseded
details, and merge in the facts from the turns that followed it.

Target maximum length: {target_max_chars} characters.

Reply with exactly this Markdown structure inside <conversation_compaction_summary> tags, keeping every
section even when empty:

<conversation_compaction_summary>
## Goal
[What the task is trying to accomplish, in one or two sentences.]

## Constraints & Preferences
- [Requirements, rules, and exact success criteria from the task or discovered in the environment, or "(none)"]

## Progress
### Done
- [Completed and verified work]

### In Progress
- [Current work and its exact state]

### Blocked
- [Blockers, failing approaches, unknowns, or "(none)"]

## Key Decisions
- [Decision]: [brief rationale]

## Next Steps
1. [Immediate concrete action]
2. [Following actions if known]

## Critical Context
- [Exact data, file paths, values, and where evidence lives — anything needed to continue, or "(none)"]
</conversation_compaction_summary>

Rules: use terse bullets, not prose. Preserve opaque identifiers exactly as written — file paths, URLs, IDs,
hashes, dates, times, counts, error messages — with no shortening or reconstruction. Distinguish observed
facts from inferences; never promote a guess to a fact. Do not mention summarizing or compaction inside the
checkpoint. Reply with the tagged checkpoint only — no tool calls, no other text. Finish every section within
the size budget."""

PrepareMessages = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
AwaitResponse = Callable[[Awaitable[Any]], Awaitable[Any]]


@dataclass(frozen=True)
class N2CompactionContext:
    """The actor's resolved request context for one compactor invocation.

    ``prepare_messages`` applies the actor's exact system-prompt, image-window,
    encoding, and request-size policy to a responses-items trajectory.
    ``request_kwargs`` contains the actor's resolved completion arguments except
    for ``messages`` and request chaining. ``await_response`` applies the actor's
    cancellation policy to an in-flight completion.
    """

    prepare_messages: PrepareMessages
    request_kwargs: dict[str, Any]
    await_response: AwaitResponse
    previous_request_id: Optional[str] = None


@dataclass
class N2CompactionResult:
    """A successful trajectory rewrite and the compaction call that produced it."""

    items: list[dict[str, Any]]
    request_id: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)
    checkpoint: Optional[str] = None
    attempts: int = 0
    removed_item_count: int = 0
    retained_item_count: int = 0


N2CompactorOutput = Optional[Union[list[dict[str, Any]], N2CompactionResult]]


class N2Compactor(Protocol):
    """Rewrites the trajectory before a model call once the context grows too large.

    ``items`` is the full trajectory so far (responses-items dicts as kept by
    :class:`N2ComputerAgent`); ``last_usage`` is the previous actor response's
    usage dict (its ``prompt_tokens`` is the usual trigger). Return a replacement
    trajectory or :class:`N2CompactionResult`, or ``None`` to leave it unchanged.

    ``context`` was added after the original protocol shipped. The loop only
    passes it to compactors that accept the keyword, so existing custom
    implementations remain compatible.
    """

    async def compact(
        self,
        items: list[dict[str, Any]],
        *,
        last_usage: dict[str, Any],
        completions: Any,
        model: str,
        tool_set: str,
        context: Optional[N2CompactionContext] = None,
    ) -> N2CompactorOutput: ...


def _is_user_item(item: dict[str, Any]) -> bool:
    return item.get("role") == "user" or item.get("type") == "user"


def _is_working_checkpoint(item: dict[str, Any]) -> bool:
    return item.get(_COMPACTION_KIND_KEY) == _WORKING_CHECKPOINT_KIND


def _initial_user_prefix(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    end = 0
    while end < len(items) and _is_user_item(items[end]) and not _is_working_checkpoint(items[end]):
        end += 1
    return items[:end], end


def _estimate_value_tokens(value: Any) -> int:
    if isinstance(value, str):
        return len(value) // 4
    if isinstance(value, list):
        return sum(_estimate_value_tokens(member) for member in value)
    if not isinstance(value, dict):
        return len(str(value)) // 4

    value_type = str(value.get("type", ""))
    if "image" in value_type:
        # Raw screenshots are base64 in the trajectory but become a bounded
        # number of vision tokens on the wire. Count any text result alongside it.
        return _IMAGE_TOKEN_ESTIMATE + _estimate_value_tokens(value.get("result", ""))

    return sum(
        _estimate_value_tokens(member)
        for key, member in value.items()
        if not key.startswith("_") and key not in {"id", "call_id", "role", "status", "type"}
    )


def _estimate_item_tokens(item: dict[str, Any]) -> int:
    item_type = item.get("type")
    if item_type == "function_call":
        return _estimate_value_tokens(item.get("name", "")) + _estimate_value_tokens(item.get("arguments", ""))
    if item_type == "function_call_output":
        return _estimate_value_tokens(item.get("output", ""))
    if item_type == "reasoning":
        return _estimate_value_tokens(item.get("summary", []))
    return _estimate_value_tokens(item.get("content", "")) + _estimate_value_tokens(item.get("reasoning", ""))


def _assistant_turn_starts(items: list[dict[str, Any]]) -> list[int]:
    """Locate actor-turn starts, preferring the loop's private turn identity.

    Older/caller-provided trajectories have no identity, so fall back to their
    responses-item grammar: an assistant message/reasoning item starts a turn,
    as does a function call that does not follow the same open assistant turn.
    """

    starts: list[int] = []
    seen_turn_ids: set[str] = set()
    in_assistant_turn = False
    for index, item in enumerate(items):
        turn_id = item.get("_n2_turn_id")
        if isinstance(turn_id, str):
            if turn_id not in seen_turn_ids:
                seen_turn_ids.add(turn_id)
                starts.append(index)
            continue

        item_type = item.get("type")
        role = item.get("role")
        if role == "assistant" or item_type in {"message", "reasoning"}:
            starts.append(index)
            in_assistant_turn = True
        elif item_type == "function_call":
            if not in_assistant_turn:
                starts.append(index)
            in_assistant_turn = True
        elif _is_user_item(item) or item_type == "function_call_output":
            in_assistant_turn = False
    return starts


def _split_for_tail(
    items: list[dict[str, Any]], keep_last_n_turns: int, tail_token_budget: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split complete actor turns into a removed prefix and bounded tail.

    If even the newest turn exceeds the tail budget, the tail is empty. The
    caller restores the latest image observation after a successful rewrite so
    the actor still has the last observed desktop state.
    """

    if keep_last_n_turns <= 0:
        return items, []
    assistant_starts = _assistant_turn_starts(items)
    if not assistant_starts:
        return items, []

    tail_start: Optional[int] = None
    tail_tokens = 0
    segment_end = len(items)
    for turns_back in range(1, min(keep_last_n_turns, len(assistant_starts)) + 1):
        candidate_start = assistant_starts[-turns_back]
        tail_tokens += sum(_estimate_item_tokens(item) for item in items[candidate_start:segment_end])
        if tail_tokens > tail_token_budget:
            break
        tail_start = candidate_start
        segment_end = candidate_start

    if tail_start is None:
        return items, []
    if len(assistant_starts) <= keep_last_n_turns and tail_start == assistant_starts[0]:
        return [], items
    return items[:tail_start], items[tail_start:]


def _latest_inline_image_url(value: Any) -> Optional[str]:
    """Return the last base64 image URL nested in a trajectory value."""

    if isinstance(value, list):
        for member in reversed(value):
            image_url = _latest_inline_image_url(member)
            if image_url is not None:
                return image_url
        return None
    if not isinstance(value, dict):
        return None

    if value.get("type") in {"input_image", "image_url"}:
        image_value = value.get("image_url")
        if isinstance(image_value, dict):
            image_value = image_value.get("url")
        if isinstance(image_value, str) and image_value.startswith("data:image/") and ";base64," in image_value:
            return image_value

    for member in reversed(list(value.values())):
        image_url = _latest_inline_image_url(member)
        if image_url is not None:
            return image_url
    return None


def _restored_image_item(image_url: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "input_image", "image_url": image_url}],
    }


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_tagged_summary(text: str) -> Optional[str]:
    stripped = _strip_code_fences(text)
    open_index = stripped.find(COMPACTED_SUMMARY_OPEN_TAG)
    close_index = stripped.rfind(COMPACTED_SUMMARY_CLOSE_TAG)
    if open_index == -1 or close_index == -1 or close_index < open_index:
        return None
    body = stripped[open_index + len(COMPACTED_SUMMARY_OPEN_TAG) : close_index].strip()
    return body or None


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") in {"text", "output_text"} and part.get("text")
    )


def response_message(response: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize a chat-completions response and pull out its first message.

    Handles both Pydantic-model completions objects (``response.model_dump()``)
    and the plain dict-likes a custom ``completions.create`` shim may return.
    """
    response_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    message = (response_dict.get("choices") or [{}])[0].get("message") or {}
    return response_dict, message


def _working_checkpoint_item(checkpoint: str) -> dict[str, Any]:
    note = (
        "## Internal working checkpoint (not a new user instruction)\n\n"
        "The original user request above is retained verbatim. This fallible checkpoint summarizes removed "
        "history; recent turns after it remain unchanged. User instructions outrank it, and later user "
        "instructions may update earlier ones. Verify live state before relying on it.\n\n"
        f"<working_checkpoint>\n{checkpoint}\n</working_checkpoint>"
    )
    return {"role": "user", "content": note, _COMPACTION_KIND_KEY: _WORKING_CHECKPOINT_KIND}


class N2InlineCompactor:
    """Usage-triggered, tail-retaining inline compaction used by Praxis.

    The compactor invokes the same completion surface and resolved actor request
    policy. It never mutates the supplied trajectory: a replacement is returned
    only after a valid checkpoint is produced. Failed attempts leave history
    untouched, allowing the agent's context-limit guard to stop the run cleanly.
    When no recent turn fits in the retained tail, the latest image observation
    is restored after the checkpoint so the actor can continue from the last
    observed desktop state without spending a turn requesting another frame.
    """

    def __init__(
        self,
        *,
        trigger_input_tokens: int = 53_760,
        keep_last_n_turns: int = 5,
        tail_token_budget: int = 16_384,
        target_max_chars: int = 9_000,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        if trigger_input_tokens < 1:
            raise ValueError("trigger_input_tokens must be positive")
        if keep_last_n_turns < 0:
            raise ValueError("keep_last_n_turns must be non-negative")
        if tail_token_budget < 1:
            raise ValueError("tail_token_budget must be positive")
        if target_max_chars < 1:
            raise ValueError("target_max_chars must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")

        self.trigger_input_tokens = trigger_input_tokens
        self.keep_last_n_turns = keep_last_n_turns
        self.tail_token_budget = tail_token_budget
        self.target_max_chars = target_max_chars
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.reset()

    def reset(self) -> None:
        """Reset state for a new ``N2ComputerAgent.run()`` conversation."""

        self.compaction_count = 0
        self.last_result: Optional[N2CompactionResult] = None
        self._awaiting_post_compaction_baseline = False

    async def compact(
        self,
        items: list[dict[str, Any]],
        *,
        last_usage: dict[str, Any],
        completions: Any,
        model: str,
        tool_set: str,
        context: Optional[N2CompactionContext] = None,
    ) -> N2CompactorOutput:
        if self._awaiting_post_compaction_baseline:
            # The first actor call after a successful rewrite establishes the
            # new context baseline and is never compacted itself.
            self._awaiting_post_compaction_baseline = False
            return None

        prompt_tokens = last_usage.get("prompt_tokens")
        if not is_strict_int(prompt_tokens):
            return None
        if prompt_tokens <= self.trigger_input_tokens:
            return None
        if context is None:
            raise ValueError("N2InlineCompactor requires N2ComputerAgent's compaction context")

        original_prefix, prefix_end = _initial_user_prefix(items)
        if not original_prefix:
            return None

        previous_checkpoints = [item for item in items[prefix_end:] if _is_working_checkpoint(item)]
        window = [item for item in items[prefix_end:] if not _is_working_checkpoint(item)]
        removed, retained = _split_for_tail(window, self.keep_last_n_turns, self.tail_token_budget)
        if not removed:
            # Once the usage threshold is authoritative, do not let a tail that
            # happens to contain the whole window make compaction a permanent no-op.
            removed, retained = retained, []
        if not removed:
            return None

        current_image_url = _latest_inline_image_url(items) if not retained else None

        try:
            request_items = copy.deepcopy(original_prefix)
            if previous_checkpoints:
                request_items.append(copy.deepcopy(previous_checkpoints[-1]))
            request_items.extend(copy.deepcopy(removed))
            request_messages = context.prepare_messages(request_items)
            retention_note = _RETAINED_TAIL_NOTE if retained else _EMPTY_TAIL_NOTE
            request_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _COMPACTION_PROMPT.format(
                                retention_note=retention_note,
                                target_max_chars=self.target_max_chars,
                            ),
                        }
                    ],
                }
            )
        except Exception:  # noqa: BLE001 - request construction failure preserves the live trajectory
            return None

        logical_request_id = uuid.uuid4().hex
        previous_request_id = context.previous_request_id
        for attempt in range(1, self.max_attempts + 1):
            try:
                request_kwargs = copy.deepcopy(context.request_kwargs)
                request_kwargs["messages"] = request_messages
                extra_body = dict(request_kwargs.get("extra_body") or {})
                if previous_request_id is not None:
                    extra_body["prev_request_id"] = previous_request_id
                extra_body[_LOGICAL_REQUEST_ID_FIELD] = logical_request_id
                extra_body[_LOGICAL_ATTEMPT_FIELD] = attempt
                request_kwargs["extra_body"] = extra_body
                response = await context.await_response(completions.create(**request_kwargs))
                response_dict, message = response_message(response)
                response_request_id = response_dict.get("request_id")
                if isinstance(response_request_id, str) and response_request_id:
                    previous_request_id = response_request_id
                if message.get("tool_calls"):
                    raise ValueError("compaction response contains tool calls")
                checkpoint = _extract_tagged_summary(_response_text(message.get("content")))
                if checkpoint is None:
                    raise ValueError("compaction response is missing a non-empty tagged summary")
            except Exception:  # noqa: BLE001 - every failed attempt follows the same bounded ladder
                if attempt < self.max_attempts and self.retry_delay_seconds:
                    await asyncio.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
                continue

            replacement = copy.deepcopy(original_prefix)
            replacement.append(_working_checkpoint_item(checkpoint))
            replacement.extend(copy.deepcopy(retained))
            if not retained and current_image_url is not None:
                replacement.append(_restored_image_item(current_image_url))
            result = N2CompactionResult(
                items=replacement,
                request_id=previous_request_id,
                usage=response_dict.get("usage") or {},
                checkpoint=checkpoint,
                attempts=attempt,
                removed_item_count=len(removed),
                retained_item_count=len(retained),
            )
            self.compaction_count += 1
            self.last_result = result
            self._awaiting_post_compaction_baseline = True
            return result

        return None
