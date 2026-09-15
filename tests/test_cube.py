"""Tests for the parts of this package that exist to stop quiet wrong answers.

Nothing here touches the network. What is tested is the refusal behaviour: the
unit guard, the source choice, the satellite product by region, and the cache
key. Each of those, if it failed silently, would return a DataFrame full of
plausible numbers.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from cube import api, cache, schema
from cube.schema import UnitMismatch
from cube.sources import openmeteo


class TestUnitGuard:
    def test_the_expected_unit_passes(self):
        schema.check_unit("ws10", "m/s")
        schema.check_unit("t2m", "°C")

    def test_kmh_for_wind_is_refused(self):
        # The default Open-Meteo unit.  Taken at face value it inflates every
        # wind speed by 3.6 and still looks like a wind speed.
        with pytest.raises(UnitMismatch, match="km/h"):
            schema.check_unit("ws10", "km/h")

    def test_knots_are_refused(self):
        with pytest.raises(UnitMismatch):
            schema.check_unit("ws100", "kn")

    def test_fahrenheit_is_refused(self):
        with pytest.raises(UnitMismatch):
            schema.check_unit("t2m", "°F")

    def test_the_message_names_both_units(self):
        with pytest.raises(UnitMismatch) as caught:
            schema.check_unit("ws10", "km/h")
        assert "m/s" in str(caught.value) and "km/h" in str(caught.value)


class TestVariables:
    def test_unknown_names_are_refused_with_the_known_list(self):
        with pytest.raises(KeyError, match="temperature"):
            schema.resolve("temperature,ghi")

    def test_a_comma_string_and_a_list_agree(self):
        assert schema.resolve("ghi,t2m") == schema.resolve(["ghi", "t2m"])

    def test_precipitation_is_the_only_accumulating_quantity(self):
        accumulating = {n for n, v in schema.VARIABLES.items() if v.accumulates}
        assert accumulating == {"precip"}

    def test_every_variable_accepts_its_own_canonical_unit(self):
        for name, spec in schema.VARIABLES.items():
            # A variable whose canonical unit is not in its own accepted list
            # would refuse a correct response.
            assert any(
                u.replace("²", "^2") == spec.unit or u == spec.unit
                for u in spec.accepted_units
            ), name


class TestSourceChoice:
    def test_scoring_a_forecast_always_uses_the_fixed_lead_archive(self):
        # The plain archive returns "whatever was latest", at no stated lead.
        assert api.choose_source("2024-01-01", "2024-12-31", "verify") == "previous_runs"

    def test_truth_uses_the_satellite(self):
        assert api.choose_source("2024-01-01", "2024-12-31", "truth") == "satellite"

    def test_the_past_uses_the_reanalysis(self):
        assert api.choose_source("2020-01-01", "2020-12-31", "resource") == "archive"

    def test_the_future_uses_the_forecast(self):
        ahead = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        assert api.choose_source(ahead, ahead, "resource") == "forecast"


class TestSatelliteRegion:
    @pytest.mark.parametrize(
        "lat,lon,expected",
        [
            (39.9, 116.4, "east_asia"),   # Beijing
            (22.5, 114.1, "east_asia"),   # Shenzhen
            (51.3, 10.0, "europe"),       # Germany
            (-1.3, 36.8, "africa"),       # Nairobi
            (40.7, -74.0, "auto"),        # New York, no named product
        ],
    )
    def test_the_right_satellite_sees_the_point(self, lat, lon, expected):
        assert openmeteo.satellite_region(lat, lon) == expected

    def test_europe_does_not_get_the_seamless_product(self):
        # The seamless selector over Europe begins in February 2026.  A study
        # starting earlier silently loses its solar truth.
        product = openmeteo.SATELLITE_BY_REGION[openmeteo.satellite_region(51.3, 10.0)]
        assert product == "eumetsat_sarah3"


class TestFixedLead:
    def test_previous_runs_without_a_lead_is_refused(self):
        with pytest.raises(ValueError, match="lead_days"):
            openmeteo.fetch(
                40.0, 116.0, "2025-01-01", "2025-01-02",
                schema.resolve("ghi"), endpoint="previous_runs",
            )

    def test_an_unknown_endpoint_is_refused(self):
        with pytest.raises(ValueError, match="unknown endpoint"):
            openmeteo.fetch(
                40.0, 116.0, "2025-01-01", "2025-01-02",
                schema.resolve("ghi"), endpoint="whatever",
            )


class TestCacheKey:
    def _request(self, **overrides):
        base = {
            "source": "openmeteo.archive", "latitude": 40.0, "longitude": 116.0,
            "start": "2025-01-01", "end": "2025-06-30",
            "variables": ["ghi", "t2m"], "models": None, "lead_days": None,
        }
        base.update(overrides)
        return base

    def test_the_same_request_gives_the_same_key(self):
        assert cache.key(self._request()) == cache.key(self._request())

    def test_dict_order_does_not_change_the_key(self):
        forward = self._request()
        backward = {k: forward[k] for k in reversed(list(forward))}
        assert cache.key(forward) == cache.key(backward)

    @pytest.mark.parametrize(
        "change",
        [
            {"models": ["icon_seamless"]},
            {"lead_days": 1},
            {"variables": ["ghi"]},
            {"source": "openmeteo.previous_runs"},
            {"latitude": 40.25},
        ],
    )
    def test_anything_that_changes_the_data_changes_the_key(self, change):
        # The failure this prevents: adding a model, then being served the old
        # file, so the change appears to have had no effect.
        assert cache.key(self._request()) != cache.key(self._request(**change))

    def test_the_request_is_written_beside_the_data(self, tmp_path):
        request = self._request()
        frame = pd.DataFrame({"ghi": [1.0, 2.0]}, index=pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC"))
        target = cache.store(request, frame, tmp_path)
        assert target.exists()
        # A cache directory has to be readable by a human without reversing a
        # hash.
        saved = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        assert saved["variables"] == ["ghi", "t2m"]
        assert cache.load(request, tmp_path) is not None
