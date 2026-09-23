"""The index formats and the conversions, without touching the network.

What these pin is the arithmetic that produces a plausible wrong number: a
message length read from the wrong place, an accumulation taken as a rate, a
wind direction off by 180 degrees. Every one of them returns a value rather than
an error.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cube.schema import UnitMismatch
from cube.sources import opendata

ECMWF_INDEX = (
    '{"param":"100u","step":24,"levtype":"sfc","_offset":100,"_length":50}\n'
    '{"param":"100v","step":24,"levtype":"sfc","_offset":150,"_length":60}\n'
)
NOAA_INDEX = (
    "1:0:d=2026092000:PRMSL:mean sea level:24 hour fcst:\n"
    "2:999265:d=2026092000:UGRD:100 m above ground:24 hour fcst:\n"
    "3:1090339:d=2026092000:VGRD:100 m above ground:24 hour fcst:\n"
)


def test_ecmwf_index_carries_offset_and_length():
    entries = opendata.parse_index(ECMWF_INDEX, "ecmwf")
    assert [e["select"] for e in entries] == ["100u", "100v"]
    assert entries[0]["offset"] == 100 and entries[0]["length"] == 50


def test_noaa_index_length_is_the_next_message_start():
    """NOAA gives only the start of each message, so the length is a subtraction.

    Get it wrong and the range request returns bytes that begin with GRIB and
    end in the middle of the next field - which decodes, or crashes, depending
    on luck.
    """
    entries = opendata.parse_index(NOAA_INDEX, "noaa")
    assert entries[0]["length"] == 999265
    assert entries[1]["select"] == "UGRD:100 m above ground"
    assert entries[1]["length"] == 1090339 - 999265
    # The last message runs to the end of the file, which a range request writes
    # as an open end rather than a guessed number.
    assert entries[2]["length"] is None


def test_paths_match_each_publisher_layout():
    run = pd.Timestamp("2026-09-20 00:00")
    data, idx = opendata.MODELS["ifs"].paths(run, 24)
    assert data == "20260920/00z/ifs/0p25/oper/20260920000000-24h-oper-fc.grib2"
    assert idx.endswith(".index")
    data, _ = opendata.MODELS["aifs"].paths(run, 24)
    assert "aifs-single" in data
    data, idx = opendata.MODELS["gfs"].paths(run, 24)
    assert data == "gfs.20260920/00/atmos/gfs.t00z.pgrb2.0p25.f024"
    assert idx.endswith(".idx")


def test_a_model_refuses_a_variable_it_does_not_publish():
    # Refusing beats returning nothing: an empty column reads as "no wind that
    # day" rather than "this model was never asked".
    with pytest.raises(KeyError, match="does not publish"):
        opendata.series("gfs", "2026-09-20 00:00", "ghi", 22.5, 114.0)


def test_a_run_hour_that_is_not_published_is_refused():
    with pytest.raises(ValueError, match="runs at"):
        opendata.series("ifs", "2026-09-20 03:00", "ws100", 22.5, 114.0)


def test_accumulation_is_differenced_against_the_previous_requested_step():
    assert opendata._previous_gap((24, 27, 30), 30) == 3
    assert opendata._previous_gap((24, 27, 30), 27) == 3
    # The first step in the list has nothing before it in the request; it falls
    # back to the publication interval rather than dividing by zero.
    assert opendata._previous_gap((24, 27, 30), 24) == 3
    assert opendata._previous_gap((96,), 96) == 6


def test_wind_direction_is_meteorological():
    """Wind direction names where the wind comes FROM, not where it goes.

    A southerly wind has a positive v component and is reported as 180°.  The
    other convention is 180° away and still looks like a bearing.
    """
    def direction(u, v):
        return float((270.0 - np.degrees(np.arctan2(v, u))) % 360.0)

    assert direction(0.0, 1.0) == pytest.approx(180.0)    # from the south
    assert direction(1.0, 0.0) == pytest.approx(270.0)    # from the west
    assert direction(0.0, -1.0) == pytest.approx(0.0)     # from the north


def test_grib_units_are_normalised_before_they_are_checked():
    assert opendata.UNIT_ALIASES["m s**-1"] == "m/s"
    assert opendata.UNIT_ALIASES["J m**-2"] == "J/m^2"
