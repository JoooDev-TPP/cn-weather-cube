"""The shape every value leaves this package in, and the three rules behind it.

Each quantity at each time is

    {"value": float | None, "unit": str, "observed_at": str | None,
     "nominal_at": str, "source": str, "qc_flag": str}

and three rules hold, each tested in tests/test_contract.py:

**Missing is null, never zero.** A gap upstream is ``value: null`` with
``qc_flag: "missing"``. Nothing here fills it — not with zero, not by
interpolation, not with the previous hour. Missing precipitation is not "no
rain": summing a month with the gaps read as zero under-reports it, and nothing
about the total looks wrong.

**One quantity never stands in for another.** Surface (station) pressure is not
sea-level pressure (QNH); they differ by about 120 hPa at 1000 m. Dew point is not
air temperature. A column is taken only when its upstream name is exactly the
one declared for the variable (``openmeteo.columns_for``); when the declared
field is absent the request fails instead of borrowing a neighbour.

**A fallback source is written down.** When the first source fails and another
answers, ``source`` says so on every value (``"openmeteo.forecast;
fallback_from=openmeteo.archive"``). Values are never mixed row by row between
sources to patch gaps: a gap in the source that answered stays a gap.

``nominal_at`` is the regular slot a value is filed under (the hour on the
index). ``observed_at`` is when the value actually describes, when the source
says; for gridded products (reanalysis, forecast, satellite retrieval) the two
coincide. A frame may carry ``<column>__observed_at`` to say otherwise (a station
report at 10:47 filed under 11:00). A missing value has no ``observed_at``.
Downstream freshness checks read the two separately.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from .schema import VARIABLES

CONTRACT = "point-weather/1"
FIELDS = ("value", "unit", "observed_at", "nominal_at", "source", "qc_flag")
QC_FLAGS = ("ok", "missing", "out_of_range")
OBSERVED_SUFFIX = "__observed_at"

#: Physically possible range per variable. A value outside is kept as reported
#: and flagged, not clipped and not dropped: the flag is the information.
PLAUSIBLE: dict[str, tuple[float, float]] = {
    "ghi": (0.0, 1500.0), "dni": (0.0, 1400.0), "dhi": (0.0, 900.0),
    "t2m": (-90.0, 60.0), "d2m": (-100.0, 40.0), "rh": (0.0, 100.0),
    "sp": (300.0, 1100.0), "msl": (850.0, 1090.0),
    "ws10": (0.0, 115.0), "ws100": (0.0, 150.0),
    "wd10": (0.0, 360.0), "wd100": (0.0, 360.0),
    "cloud": (0.0, 100.0), "precip": (0.0, 500.0),
}


def variable_of(column: str) -> str:
    """The variable a frame column holds: ``ghi`` for ``ghi`` or ``ghi_ecmwf_ifs025``."""
    if column in VARIABLES:
        return column
    for name in sorted(VARIABLES, key=len, reverse=True):
        if column.startswith(name + "_"):
            return name
    raise KeyError(f"column {column!r} is not one of this package's variables")


def _iso(ts: Any) -> str | None:
    if ts is None or (isinstance(ts, float) and math.isnan(ts)):
        return None
    stamp = pd.Timestamp(ts)
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def quantity(variable: str, value: Any, *, nominal_at: Any, source: str,
             observed_at: Any = None) -> dict:
    """One value under the contract. ``observed_at`` defaults to ``nominal_at``."""
    spec = VARIABLES[variable]
    if not source:
        raise ValueError("every value names its source")
    nominal = _iso(nominal_at)
    if nominal is None:
        raise ValueError("every value has a nominal time")
    if _missing(value):
        return {"value": None, "unit": spec.unit, "observed_at": None,
                "nominal_at": nominal, "source": source, "qc_flag": "missing"}
    number = float(value)
    low, high = PLAUSIBLE.get(variable, (-math.inf, math.inf))
    observed = _iso(observed_at) if observed_at is not None else nominal
    return {"value": number, "unit": spec.unit, "observed_at": observed,
            "nominal_at": nominal, "source": source,
            "qc_flag": "ok" if low <= number <= high else "out_of_range"}


def source_of(frame: pd.DataFrame) -> str:
    """The source string every value of this frame carries, fallback included."""
    source = str(frame.attrs.get("source") or "")
    if not source:
        raise ValueError("the frame does not say which source produced it")
    primary = frame.attrs.get("fallback_from")
    return f"{source};fallback_from={primary}" if primary else source


def records(frame: pd.DataFrame) -> list[dict]:
    """Every (time, column) of a fetched frame as a contract value, plus its name."""
    source = source_of(frame)
    columns = [c for c in frame.columns if not str(c).endswith(OBSERVED_SUFFIX)]
    out: list[dict] = []
    for nominal, row in frame.iterrows():
        for column in columns:
            observed = row.get(f"{column}{OBSERVED_SUFFIX}") if f"{column}{OBSERVED_SUFFIX}" in frame else None
            value = quantity(variable_of(column), row[column], nominal_at=nominal,
                             source=source, observed_at=observed)
            out.append({"name": column, **value})
    return out


def document(frame: pd.DataFrame) -> dict:
    """The whole series as one JSON-ready object: provenance, gap counts, values."""
    values = records(frame)
    missing: dict[str, int] = {}
    for v in values:
        if v["qc_flag"] == "missing":
            missing[v["name"]] = missing.get(v["name"], 0) + 1
    attrs = frame.attrs
    return {
        "contract": CONTRACT,
        "source": source_of(frame),
        "fallback_from": attrs.get("fallback_from"),
        "fallback_reason": attrs.get("fallback_reason"),
        "purpose": attrs.get("purpose"),
        "lat": attrs.get("lat"),
        "lon": attrs.get("lon"),
        "lead_days": attrs.get("lead_days"),
        "missing": missing,
        "values": values,
    }


def dumps(frame: pd.DataFrame) -> str:
    # allow_nan=False: a NaN that slipped past quantity() fails here instead of
    # leaving as the non-JSON token NaN that some readers turn into zero.
    return json.dumps(document(frame), ensure_ascii=False, allow_nan=False)
