"""Tests for n1/n1.5 model constants and tool set identifiers."""

from yutori.n1 import N1_5_MODEL, N1_MODEL, TOOL_SET_CORE, TOOL_SET_EXPANDED


class TestModelConstants:
    def test_n1_model_identifier(self):
        assert N1_MODEL == "n1-latest"

    def test_n1_5_model_identifier(self):
        assert N1_5_MODEL == "n1.5-latest"


class TestToolSetConstants:
    def test_core_tool_set(self):
        assert "core" in TOOL_SET_CORE
        assert TOOL_SET_CORE == "browser_tools_core-20260403"

    def test_expanded_tool_set(self):
        assert "expanded" in TOOL_SET_EXPANDED
        assert TOOL_SET_EXPANDED == "browser_tools_expanded-20260403"

    def test_tool_sets_are_distinct(self):
        assert TOOL_SET_CORE != TOOL_SET_EXPANDED
