"""The missing-value and time contract (cube/contract.py), against a fake upstream.

No network: openmeteo._get is replaced by a function that returns a payload
shaped like Open-Meteo's, so each rule is tested on the path real data takes.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from cube import api, cli, contract, schema
from cube.sources import openmeteo

HOURS = ["2025-01-01T00:00", "2025-01-01T01:00", "2025-01-01T02:00"]


def _payload(hourly: dict, units: dict) -> dict:
    return {"hourly": {"time": HOURS, **hourly}, "hourly_units": {"time": "iso8601", **units}}


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    """Install a fake Open-Meteo. ``answers[endpoint]`` is a payload or an exception."""
    answers: dict = {}
    calls: list[str] = []

    def fake_get(url, params, **_kw):
        endpoint = next(k for k, v in openmeteo.ENDPOINTS.items() if v == url)
        calls.append(endpoint)
        answer = answers[endpoint]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(openmeteo, "_get", fake_get)
    monkeypatch.setattr(openmeteo.cache, "DEFAULT_ROOT", tmp_path / "cache")
    return answers, calls


def _fetch(**kw):
    base = dict(lat=40.0, lon=116.0, start="2025-01-01", end="2025-01-01", refresh=True,
                source="archive")
    base.update(kw)
    return api.fetch(**base)


class TestMissingIsNull:
    def test_a_gap_upstream_is_null_not_zero(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload(
            {"precipitation": [0.4, None, 1.2], "temperature_2m": [None, -3.1, -2.0]},
            {"precipitation": "mm", "temperature_2m": "°C"},
        )
        frame = _fetch(vars="precip,t2m")
        assert frame["precip"].isna().sum() == 1 and frame["t2m"].isna().sum() == 1
        values = {(v["name"], v["nominal_at"]): v for v in contract.records(frame)}
        gap = values[("precip", "2025-01-01T01:00:00Z")]
        assert gap["value"] is None and gap["value"] != 0
        assert gap["qc_flag"] == "missing" and gap["observed_at"] is None
        assert gap["nominal_at"] == "2025-01-01T01:00:00Z"
        assert values[("t2m", "2025-01-01T00:00:00Z")]["value"] is None
        assert values[("precip", "2025-01-01T02:00:00Z")]["value"] == 1.2

    def test_an_all_null_column_is_still_null(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload({"precipitation": [None, None, None]}, {"precipitation": "mm"})
        frame = _fetch(vars="precip")
        doc = contract.document(frame)
        assert doc["missing"] == {"precip": 3}
        assert all(v["value"] is None for v in doc["values"])

    def test_the_json_has_null_and_never_nan(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload({"precipitation": [None, 0.0, 2.0]}, {"precipitation": "mm"})
        text = contract.dumps(_fetch(vars="precip"))
        assert "NaN" not in text
        first = json.loads(text)["values"][0]
        assert first["value"] is None
        # A real zero stays a real zero, distinct from a gap.
        assert json.loads(text)["values"][1]["value"] == 0.0

    def test_every_value_has_all_six_fields(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload({"shortwave_radiation": [0.0, None, 310.0]},
                                      {"shortwave_radiation": "W/m²"})
        for value in contract.records(_fetch(vars="ghi")):
            assert set(contract.FIELDS) <= set(value)
            assert value["unit"] == "W/m^2" and value["source"] == "openmeteo.archive"
            assert value["qc_flag"] in contract.QC_FLAGS


class TestNoSubstitution:
    def test_sea_level_pressure_never_fills_station_pressure(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload({"pressure_msl": [1020.0, 1021.0, 1019.0]},
                                      {"pressure_msl": "hPa"})
        with pytest.raises(RuntimeError, match="surface_pressure"):
            _fetch(vars="sp")

    def test_both_pressures_stay_in_their_own_columns(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload(
            {"surface_pressure": [900.0, None, 901.0], "pressure_msl": [1020.0, 1021.0, 1019.0]},
            {"surface_pressure": "hPa", "pressure_msl": "hPa"},
        )
        frame = _fetch(vars="sp,msl")
        assert list(frame["sp"].iloc[[0, 2]]) == [900.0, 901.0]
        # The station-pressure gap is not filled from QNH.
        assert pd.isna(frame["sp"].iloc[1])
        assert not set(frame["sp"].dropna()) & set(frame["msl"])

    def test_a_column_that_only_starts_with_the_name_is_not_taken(self):
        cols = ["precipitation_probability", "temperature_2m_max"]
        assert openmeteo.columns_for("precipitation", cols) == []
        assert openmeteo.columns_for("temperature_2m", cols) == []
        assert openmeteo.columns_for("temperature_2m", ["temperature_2m_icon_seamless"],
                                     ["icon_seamless"]) == ["temperature_2m_icon_seamless"]

    def test_probability_of_precipitation_is_not_precipitation(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload({"precipitation_probability": [80, 90, 100]},
                                      {"precipitation_probability": "%"})
        with pytest.raises(RuntimeError, match="does not publish"):
            _fetch(vars="precip")


class TestFallbackIsRecorded:
    def test_a_fallback_changes_the_source_on_every_value(self, upstream):
        answers, calls = upstream
        answers["archive"] = RuntimeError("open-meteo archive failed after 6 tries: 503")
        answers["forecast"] = _payload({"temperature_2m": [1.0, 2.0, None]},
                                       {"temperature_2m": "°C"})
        frame = _fetch(vars="t2m", fallback="forecast")
        assert calls == ["archive", "forecast"]
        assert frame.attrs["source"] == "openmeteo.forecast"
        assert frame.attrs["fallback_from"] == "openmeteo.archive"
        sources = {v["source"] for v in contract.records(frame)}
        assert sources == {"openmeteo.forecast;fallback_from=openmeteo.archive"}
        # The fallback's own gap stays a gap; nothing is borrowed back.
        assert contract.records(frame)[-1]["value"] is None

    def test_no_fallback_means_the_failure_surfaces(self, upstream):
        answers, _ = upstream
        answers["archive"] = RuntimeError("down")
        with pytest.raises(RuntimeError, match="down"):
            _fetch(vars="t2m")

    def test_a_unit_refusal_is_not_routed_around(self, upstream):
        answers, calls = upstream
        answers["archive"] = _payload({"wind_speed_10m": [3.6, 7.2, 10.8]}, {"wind_speed_10m": "km/h"})
        answers["forecast"] = _payload({"wind_speed_10m": [1.0, 2.0, 3.0]}, {"wind_speed_10m": "m/s"})
        with pytest.raises(schema.UnitMismatch):
            _fetch(vars="ws10", fallback="forecast")
        assert calls == ["archive"]

    def test_the_primary_source_has_no_fallback_tag(self, upstream):
        answers, _ = upstream
        answers["archive"] = _payload({"temperature_2m": [1.0, 2.0, 3.0]}, {"temperature_2m": "°C"})
        frame = _fetch(vars="t2m", fallback="forecast")
        assert contract.source_of(frame) == "openmeteo.archive"
        assert contract.document(frame)["fallback_from"] is None


class TestTimes:
    def test_nominal_and_observed_are_separate(self):
        frame = pd.DataFrame(
            {"t2m": [5.0, None], "t2m__observed_at": [pd.Timestamp("2025-01-01T10:47Z"), pd.NaT]},
            index=pd.DatetimeIndex(["2025-01-01T11:00Z", "2025-01-01T12:00Z"]),
        )
        frame.attrs["source"] = "station.metar"
        first, second = contract.records(frame)
        assert first["nominal_at"] == "2025-01-01T11:00:00Z"
        assert first["observed_at"] == "2025-01-01T10:47:00Z"
        assert second["value"] is None and second["observed_at"] is None
        assert second["nominal_at"] == "2025-01-01T12:00:00Z"

    def test_gridded_values_are_observed_at_their_nominal_hour(self):
        frame = pd.DataFrame({"ghi": [120.0]}, index=pd.DatetimeIndex(["2025-06-01T04:00Z"]))
        frame.attrs["source"] = "openmeteo.archive"
        (only,) = contract.records(frame)
        assert only["observed_at"] == only["nominal_at"] == "2025-06-01T04:00:00Z"

    def test_out_of_range_is_flagged_not_clipped(self):
        value = contract.quantity("rh", 140.0, nominal_at="2025-01-01T00:00Z", source="x")
        assert value["value"] == 140.0 and value["qc_flag"] == "out_of_range"

    def test_a_frame_without_a_source_is_refused(self):
        frame = pd.DataFrame({"ghi": [1.0]}, index=pd.DatetimeIndex(["2025-01-01T00:00Z"]))
        with pytest.raises(ValueError, match="source"):
            contract.records(frame)

    def test_every_variable_has_a_plausible_range(self):
        assert set(contract.PLAUSIBLE) == set(schema.VARIABLES)


class TestCli:
    def test_json_out_writes_the_contract(self, upstream, tmp_path, capsys):
        answers, _ = upstream
        answers["archive"] = _payload({"precipitation": [None, 0.2, 0.0]}, {"precipitation": "mm"})
        out = tmp_path / "p.json"
        code = cli.main(["fetch", "--lat", "40", "--lon", "116", "--start", "2020-01-01",
                         "--end", "2020-01-01", "--vars", "precip", "--refresh", "-o", str(out)])
        assert code == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["contract"] == contract.CONTRACT and doc["missing"] == {"precip": 1}
        assert "没有填 0" in capsys.readouterr().out
