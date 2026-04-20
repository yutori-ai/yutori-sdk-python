"""Tests for shared HTTP helpers in yutori._http."""

import pytest

from yutori._http import resolve_scout_status_endpoint


class TestResolveScoutStatusEndpoint:
    def test_paused_maps_to_pause(self):
        assert resolve_scout_status_endpoint("paused") == "pause"

    def test_active_maps_to_resume(self):
        assert resolve_scout_status_endpoint("active") == "resume"

    def test_done_maps_to_done(self):
        assert resolve_scout_status_endpoint("done") == "done"

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            resolve_scout_status_endpoint("deleted")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            resolve_scout_status_endpoint("")
