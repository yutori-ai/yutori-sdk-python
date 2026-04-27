"""Tests for Navigator model constants and tool set identifiers."""

from yutori.navigator import (
    N1_5_MODEL,
    N1_MODEL,
    NAVIGATOR_N1_5_MODEL,
    NAVIGATOR_N1_MODEL,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
)


class TestModelConstants:
    def test_navigator_n1_model_identifier(self):
        assert NAVIGATOR_N1_MODEL == "n1-latest"

    def test_navigator_n1_5_model_identifier(self):
        assert NAVIGATOR_N1_5_MODEL == "n1.5-latest"

    def test_legacy_aliases_match_canonical(self):
        # ``N1_MODEL`` / ``N1_5_MODEL`` are kept as deprecated aliases of the
        # canonical ``NAVIGATOR_*`` names. Asserting identity (``is``) catches
        # accidental drift if anyone re-defines either constant independently.
        assert N1_MODEL is NAVIGATOR_N1_MODEL
        assert N1_5_MODEL is NAVIGATOR_N1_5_MODEL


class TestToolSetConstants:
    def test_core_tool_set(self):
        assert "core" in TOOL_SET_CORE
        assert TOOL_SET_CORE == "browser_tools_core-20260403"

    def test_expanded_tool_set(self):
        assert "expanded" in TOOL_SET_EXPANDED
        assert TOOL_SET_EXPANDED == "browser_tools_expanded-20260403"

    def test_tool_sets_are_distinct(self):
        assert TOOL_SET_CORE != TOOL_SET_EXPANDED
