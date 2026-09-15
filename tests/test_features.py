"""Tests for the conversions that turn weather into a power input.

These are where a series stops being weather and starts being an input to a
revenue number, and each one has a way of being wrong that returns a plausible
figure: a shear exponent fitted on a calm hour, a tilted-plane total that is
somehow below the horizontal one, a density that ignores altitude.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cube import features

LAT, LON = 22.54, 114.06  # Shenzhen


def _day(hours: int = 48) -> pd.DataFrame:
    index = pd.date_range("2024-06-01", periods=hours, freq="h", tz="UTC")
    # A crude diurnal cycle, enough to exercise the geometry.
    hour = index.hour + 8  # Beijing time
    ghi = np.clip(900 * np.sin(np.pi * (hour - 6) / 12), 0, None)
    return pd.DataFrame(
        {
            "ghi": ghi,
            "t2m": 25 + 5 * np.sin(np.pi * (hour - 9) / 12),
            "sp": 1005.0,
            "ws10": 4.0,
            "ws100": 6.5,
        },
        index=index,
    )


class TestPlaneOfArray:
    def test_a_tilted_plane_collects_more_than_a_horizontal_one_in_winter(self):
        frame = _day()
        flat = features.plane_of_array(frame, LAT, LON, tilt=0.0)
        tilted = features.plane_of_array(frame, LAT, LON, tilt=22.0)
        # Both are finite and the tilt does something.
        assert np.isfinite(tilted).all()
        assert not np.allclose(flat, tilted)

    def test_nothing_is_collected_at_night(self):
        frame = _day()
        poa = features.plane_of_array(frame, LAT, LON, tilt=22.0)
        assert (poa[frame["ghi"] == 0] < 1.0).all()

    def test_a_zero_tilt_plane_is_close_to_the_horizontal_input(self):
        frame = _day()
        flat = features.plane_of_array(frame, LAT, LON, tilt=0.0)
        daylight = frame["ghi"] > 50
        ratio = flat[daylight].sum() / frame.loc[daylight, "ghi"].sum()
        assert 0.9 < ratio < 1.1


class TestHubWind:
    def test_two_levels_fit_the_shear_rather_than_assuming_it(self):
        frame = _day()
        hub = features.hub_wind(frame, hub_height=90.0)
        assert hub.name == "ws_hub"
        # 4.0 at 10 m and 6.5 at 100 m is alpha ~0.21; at 90 m the answer sits
        # just below the 100 m reading.
        assert (hub < frame["ws100"]).all()
        assert (hub > frame["ws10"]).all()

    def test_one_level_says_so_in_the_name(self):
        frame = _day().drop(columns="ws100")
        hub = features.hub_wind(frame, hub_height=90.0)
        # The assumption has to travel with the numbers.
        assert "assumed_alpha" in hub.name

    def test_a_calm_hour_cannot_drive_the_exponent_out_of_range(self):
        frame = _day()
        frame.loc[frame.index[:6], "ws10"] = 0.0
        frame.loc[frame.index[:6], "ws100"] = 0.0
        hub = features.hub_wind(frame, hub_height=90.0)
        assert np.isfinite(hub).all()
        assert (hub >= 0).all()

    def test_higher_hubs_see_more_wind(self):
        frame = _day()
        low = features.hub_wind(frame, hub_height=50.0)
        high = features.hub_wind(frame, hub_height=120.0)
        assert (high > low).all()


class TestAirDensity:
    def test_sea_level_and_fifteen_degrees_is_the_standard_value(self):
        frame = pd.DataFrame(
            {"t2m": [15.0], "sp": [1013.25]},
            index=pd.date_range("2024-01-01", periods=1, freq="h", tz="UTC"),
        )
        assert features.air_density(frame).iloc[0] == pytest.approx(1.225, abs=0.005)

    def test_altitude_lowers_it(self):
        index = pd.date_range("2024-01-01", periods=1, freq="h", tz="UTC")
        sea = pd.DataFrame({"t2m": [15.0], "sp": [1013.25]}, index=index)
        plateau = pd.DataFrame({"t2m": [15.0], "sp": [880.0]}, index=index)
        assert features.air_density(plateau).iloc[0] < features.air_density(sea).iloc[0]

    def test_cold_air_is_denser(self):
        index = pd.date_range("2024-01-01", periods=1, freq="h", tz="UTC")
        cold = pd.DataFrame({"t2m": [-20.0], "sp": [1013.25]}, index=index)
        warm = pd.DataFrame({"t2m": [35.0], "sp": [1013.25]}, index=index)
        # Around twenty per cent between an Inner Mongolian winter and summer,
        # which is twenty per cent of output at the same wind speed.
        ratio = features.air_density(cold).iloc[0] / features.air_density(warm).iloc[0]
        assert 1.15 < ratio < 1.30

    def test_without_pressure_the_name_says_so(self):
        frame = _day().drop(columns="sp")
        assert features.air_density(frame).name == "rho_no_pressure"


class TestClearskyIndex:
    def test_it_is_zero_at_night_not_infinite(self):
        frame = _day()
        kt = features.clearsky_index(frame, LAT, LON)
        assert np.isfinite(kt).all()
        assert (kt >= 0).all()

    def test_a_clear_day_lands_near_one(self):
        import pvlib

        frame = _day()
        site = pvlib.location.Location(LAT, LON, tz="UTC")
        frame["ghi"] = site.get_clearsky(frame.index, model="haurwitz")["ghi"]
        kt = features.clearsky_index(frame, LAT, LON)
        daylight = frame["ghi"] > 50
        assert kt[daylight].mean() == pytest.approx(1.0, abs=0.05)
