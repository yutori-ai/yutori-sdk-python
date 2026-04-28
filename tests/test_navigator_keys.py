"""Tests for Navigator n1.5 key mapping helpers."""

import pytest

from yutori.navigator.keys import map_key_to_playwright, map_keys_individual


class TestMapKeyToPlaywright:
    """map_key_to_playwright converts Navigator n1.5 key expressions to Playwright press() strings."""

    def test_single_modifier_combo(self):
        assert map_key_to_playwright("ctrl+c") == ["Control+c"]

    def test_multi_modifier_combo(self):
        assert map_key_to_playwright("ctrl+shift+t") == ["Control+Shift+t"]

    def test_sequential_presses(self):
        assert map_key_to_playwright("down down enter") == ["ArrowDown", "ArrowDown", "Enter"]

    def test_single_key(self):
        assert map_key_to_playwright("enter") == ["Enter"]

    def test_arrow_keys(self):
        assert map_key_to_playwright("left") == ["ArrowLeft"]
        assert map_key_to_playwright("right") == ["ArrowRight"]
        assert map_key_to_playwright("up") == ["ArrowUp"]
        assert map_key_to_playwright("down") == ["ArrowDown"]

    def test_escape_aliases(self):
        assert map_key_to_playwright("escape") == ["Escape"]
        assert map_key_to_playwright("esc") == ["Escape"]

    def test_enter_aliases(self):
        assert map_key_to_playwright("enter") == ["Enter"]
        assert map_key_to_playwright("return") == ["Enter"]

    def test_modifier_aliases(self):
        assert map_key_to_playwright("cmd+a") == ["Meta+a"]
        assert map_key_to_playwright("command+a") == ["Meta+a"]
        assert map_key_to_playwright("option+tab") == ["Alt+Tab"]
        assert map_key_to_playwright("alt+tab") == ["Alt+Tab"]

    def test_function_keys(self):
        assert map_key_to_playwright("f1") == ["F1"]
        assert map_key_to_playwright("f12") == ["F12"]

    def test_space_key(self):
        assert map_key_to_playwright("space") == [" "]

    def test_tab_key(self):
        assert map_key_to_playwright("tab") == ["Tab"]

    def test_backspace_and_delete(self):
        assert map_key_to_playwright("backspace") == ["Backspace"]
        assert map_key_to_playwright("delete") == ["Delete"]

    def test_page_navigation(self):
        assert map_key_to_playwright("home") == ["Home"]
        assert map_key_to_playwright("end") == ["End"]
        assert map_key_to_playwright("pageup") == ["PageUp"]
        assert map_key_to_playwright("pagedown") == ["PageDown"]

    def test_unknown_key_passes_through(self):
        assert map_key_to_playwright("x") == ["x"]
        assert map_key_to_playwright("a") == ["a"]

    def test_preserves_literal_characters(self):
        assert map_key_to_playwright("/") == ["/"]
        assert map_key_to_playwright(".") == ["."]

    def test_word_form_punctuation(self):
        assert map_key_to_playwright("plus") == ["+"]
        assert map_key_to_playwright("minus") == ["-"]
        assert map_key_to_playwright("slash") == ["/"]

    def test_strips_whitespace(self):
        assert map_key_to_playwright("  enter  ") == ["Enter"]

    def test_empty_string_returns_empty_list(self):
        assert map_key_to_playwright("") == []
        assert map_key_to_playwright("   ") == []

    def test_mixed_sequential_and_combo(self):
        # tab then ctrl+a is two separate presses
        assert map_key_to_playwright("tab ctrl+a") == ["Tab", "Control+a"]


class TestMapKeysIndividual:
    """map_keys_individual flattens combos into individual Playwright keys."""

    def test_combo_is_split(self):
        assert map_keys_individual("ctrl+c") == ["Control", "c"]

    def test_multi_modifier_combo_is_split(self):
        assert map_keys_individual("ctrl+shift+t") == ["Control", "Shift", "t"]

    def test_sequential_presses(self):
        assert map_keys_individual("down down enter") == ["ArrowDown", "ArrowDown", "Enter"]

    def test_single_key(self):
        assert map_keys_individual("enter") == ["Enter"]

    def test_ctrl_plus_maps_correctly(self):
        # "ctrl+plus" → ["Control", "+"]
        assert map_keys_individual("ctrl+plus") == ["Control", "+"]

    def test_empty_string(self):
        assert map_keys_individual("") == []

    def test_whitespace_only(self):
        assert map_keys_individual("   ") == []
