"""Tests for yutori.n1.coordinates."""

import pytest

from yutori.n1 import N1_COORDINATE_SCALE, denormalize_coordinates, normalize_coordinates


class TestDenormalizeCoordinates:
    def test_scales_n1_coordinates_to_viewport_pixels(self):
        assert denormalize_coordinates([500, 250], width=1280, height=800) == (640, 200)

    def test_origin_returns_zero(self):
        assert denormalize_coordinates([0, 0], width=1280, height=800) == (0, 0)

    def test_clamps_out_of_bounds_values_by_default(self):
        assert denormalize_coordinates([1000, 1000], width=1280, height=800) == (1279, 799)
        assert denormalize_coordinates([-100, -50], width=1280, height=800) == (0, 0)

    def test_can_return_unclamped_coordinates(self):
        assert denormalize_coordinates([1000, 1000], width=1280, height=800, clamp=False) == (1280, 800)

    def test_accepts_float_coordinates(self):
        x, y = denormalize_coordinates([500.7, 250.3], width=1280, height=800)
        assert isinstance(x, int) and isinstance(y, int)
        assert (x, y) == (int(500.7 / 1000 * 1280), int(250.3 / 1000 * 800))

    def test_accepts_tuple_input(self):
        assert denormalize_coordinates((500, 250), width=1280, height=800) == (640, 200)

    def test_custom_scale(self):
        assert denormalize_coordinates([250, 250], width=1000, height=1000, scale=500) == (500, 500)


class TestNormalizeCoordinates:
    def test_scales_viewport_pixels_to_n1_coordinates(self):
        assert normalize_coordinates([640, 200], width=1280, height=800) == (500, 250)

    def test_origin_returns_zero(self):
        assert normalize_coordinates([0, 0], width=1280, height=800) == (0, 0)

    def test_clamps_out_of_bounds_values_by_default(self):
        assert normalize_coordinates([1400, 900], width=1280, height=800) == (
            N1_COORDINATE_SCALE,
            N1_COORDINATE_SCALE,
        )
        assert normalize_coordinates([-1, -5], width=1280, height=800) == (0, 0)

    def test_can_return_unclamped_coordinates(self):
        assert normalize_coordinates([1400, 900], width=1280, height=800, clamp=False) == (1094, 1125)

    def test_custom_scale(self):
        assert normalize_coordinates([500, 500], width=1000, height=1000, scale=500) == (250, 250)


class TestRoundtrip:
    @pytest.mark.parametrize(
        "coords",
        [[0, 0], [500, 250], [999, 999], [100, 900], [1, 1]],
    )
    def test_normalize_denormalize_roundtrip(self, coords):
        w, h = 1280, 800
        abs_x, abs_y = denormalize_coordinates(coords, width=w, height=h, clamp=False)
        norm_x, norm_y = normalize_coordinates([abs_x, abs_y], width=w, height=h, clamp=False)
        assert abs(norm_x - coords[0]) <= 1
        assert abs(norm_y - coords[1]) <= 1


class TestCoordinateValidation:
    def test_rejects_wrong_coordinate_length(self):
        with pytest.raises(ValueError, match="exactly 2 items"):
            denormalize_coordinates([500], width=1280, height=800)

    def test_rejects_too_many_coordinates(self):
        with pytest.raises(ValueError, match="exactly 2 items"):
            normalize_coordinates([500, 500, 500], width=1280, height=800)

    def test_rejects_none_coordinates(self):
        with pytest.raises(ValueError, match="must not be None"):
            denormalize_coordinates(None, width=1280, height=800)

    def test_rejects_inf_coordinates(self):
        with pytest.raises(ValueError, match="finite numbers"):
            denormalize_coordinates([float("inf"), 500], width=1280, height=800)

    def test_rejects_nan_coordinates(self):
        with pytest.raises(ValueError, match="finite numbers"):
            normalize_coordinates([500, float("nan")], width=1280, height=800)

    @pytest.mark.parametrize(
        "name, kwargs",
        [("width", {"width": 0, "height": 800}), ("height", {"width": 1280, "height": 0})],
    )
    def test_rejects_non_positive_dimensions(self, name, kwargs):
        with pytest.raises(ValueError, match=rf"{name} must be positive"):
            denormalize_coordinates([500, 500], **kwargs)

    def test_rejects_non_positive_scale(self):
        with pytest.raises(ValueError, match="scale must be positive"):
            normalize_coordinates([500, 500], width=1280, height=800, scale=0)
