from __future__ import annotations

import json

from yutori.navigator.tools import (
    EXECUTE_JS_SCRIPT,
    EXTRACT_ELEMENTS_SCRIPT,
    FIND_SCRIPT,
    GET_ELEMENT_BY_REF_SCRIPT,
    SET_ELEMENT_VALUE_SCRIPT,
    coerce_result,
    evaluate_tool_script,
    load_tool_script,
)


class FakePage:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[str] = []

    async def evaluate(self, expression: str) -> object:
        self.calls.append(expression)
        return self.result


def test_script_constants_are_non_empty_and_contain_expected_markers() -> None:
    script_markers = [
        (EXTRACT_ELEMENTS_SCRIPT, "__yutoriElementRefs"),
        (FIND_SCRIPT, "__yutoriElementRefs"),
        (GET_ELEMENT_BY_REF_SCRIPT, "scrollIntoView"),
        (SET_ELEMENT_VALUE_SCRIPT, "set_element_value"),
        (EXECUTE_JS_SCRIPT, "AsyncFunction"),
    ]

    for script, marker in script_markers:
        assert isinstance(script, str)
        assert script.strip()
        assert marker in script


def test_load_tool_script_reads_the_packaged_js_assets() -> None:
    assert load_tool_script("find.js") == FIND_SCRIPT
    assert load_tool_script("execute_js.js") == EXECUTE_JS_SCRIPT


def test_coerce_result_handles_common_page_evaluate_outputs() -> None:
    assert coerce_result({"ok": True}) == {"ok": True}
    assert coerce_result('{"ok": true}') == {"ok": True}
    assert coerce_result("plain text") == {"value": "plain text"}

    # None indicates the script returned nothing — surfaced as a failure.
    none_result = coerce_result(None)
    assert none_result["success"] is False
    assert "message" in none_result

    # JSON strings that parse to non-dict types get wrapped.
    assert coerce_result("[1, 2, 3]") == {"value": [1, 2, 3]}
    assert coerce_result("42") == {"value": 42}

    # Non-string, non-dict, non-None scalars get wrapped.
    assert coerce_result(42) == {"value": 42}
    assert coerce_result(True) == {"value": True}


async def test_evaluate_tool_script_builds_an_iife_call_and_coerces_result() -> None:
    page = FakePage({"ok": True})
    script = "(value) => ({ ok: true, value })"

    result = await evaluate_tool_script(page, script, {"answer": 42}, [1, 2, 3])

    assert result == {"ok": True}
    assert len(page.calls) == 1
    expression = page.calls[0]
    assert script in expression
    assert json.dumps({"answer": 42}) in expression
    assert json.dumps([1, 2, 3]) in expression


async def test_evaluate_tool_script_with_zero_args() -> None:
    page = FakePage({"elements": "..."})
    script = "() => ({ elements: '...' })"

    result = await evaluate_tool_script(page, script)

    assert result == {"elements": "..."}
    expression = page.calls[0]
    # IIFE should be called with no arguments: (script)()
    assert expression.endswith("()")


async def test_evaluate_tool_script_propagates_page_errors() -> None:
    import pytest

    class ErrorPage:
        async def evaluate(self, expression: str) -> object:
            raise RuntimeError("page crashed")

    with pytest.raises(RuntimeError, match="page crashed"):
        await evaluate_tool_script(ErrorPage(), "() => {}")
