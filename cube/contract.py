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
index). ``interval`` says what window the value describes, relative to that
slot (``cube.schema.INTERVALS``): ``instant`` is the state at ``nominal_at``;
``preceding_mean`` is the mean over ``[nominal_at - window, nominal_at]``;
``following_mean`` the mean over ``[nominal_at, nominal_at + window]``;
``preceding_sum`` a total over the preceding window. ``window`` is an ISO
duration, ``null`` for an instant.

``observed_at`` is the single time the value is best read as describing: for an
instant, ``nominal_at``; for a mean or sum, the midpoint of its window. Gridded
irradiance from Open-Meteo's plain fields is therefore *not* observed at its
nominal hour: the 12:00 value is the 11:00-12:00 mean, observed at 11:30. An
earlier version of this contract said gridded values were observed at their
nominal hour; for radiation that put every sunrise half an hour late, and a
join to SMARD (hourly means labelled at the start) a full hour late. A frame
may also carry ``<column>__observed_at`` to say otherwise (a station report at
10:47 filed under 11:00). A missing value has no ``observed_at``. A column whose
interval nobody declared is ``unknown`` and keeps ``observed_at = nominal_at``;
consumers must not align it with anything until it is declared.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from .schema import INTERVALS, VARIABLES

CONTRACT = "point-weather/2"
FIELDS = ("value", "unit", "observed_at", "nominal_at", "interval", "window", "source",
          "qc_flag")
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


def interval_of(variable: str, declared: Any = None) -> tuple[str, str | None]:
    """(interval, window) for a value: the source's declaration, else the
    variable's own default, else ``unknown``."""
    if declared is not None:
        interval, window = (declared, None) if isinstance(declared, str) else tuple(declared)
    else:
        interval, window = VARIABLES[variable].default_interval or "unknown", None
    if interval not in INTERVALS:
        raise ValueError(f"interval {interval!r} is not one of {INTERVALS}")
    if interval in ("instant", "unknown"):
        return interval, None
    if not window:
        raise ValueError(f"a {interval} value needs a window (an ISO duration such as PT1H)")
    return interval, str(window)


def _midpoint(nominal: Any, interval: str, window: str | None) -> Any:
    if window is None:
        return nominal
    half = pd.Timedelta(window) / 2
    stamp = pd.Timestamp(nominal)
    return stamp - half if interval.startswith("preceding") else stamp + half


def quantity(variable: str, value: Any, *, nominal_at: Any, source: str,
             observed_at: Any = None, interval: Any = None) -> dict:
    """One value under the contract.

    ``observed_at`` defaults to ``nominal_at`` for an instant and to the middle
    of the window for a mean or a sum. ``interval`` is a name from
    ``cube.schema.INTERVALS`` or an ``(interval, window)`` pair.
    """
    spec = VARIABLES[variable]
    if not source:
        raise ValueError("every value names its source")
    nominal = _iso(nominal_at)
    if nominal is None:
        raise ValueError("every value has a nominal time")
    kind, window = interval_of(variable, interval)
    if _missing(value):
        return {"value": None, "unit": spec.unit, "observed_at": None,
                "nominal_at": nominal, "interval": kind, "window": window,
                "source": source, "qc_flag": "missing"}
    number = float(value)
    low, high = PLAUSIBLE.get(variable, (-math.inf, math.inf))
    observed = (_iso(observed_at) if observed_at is not None
                else _iso(_midpoint(nominal_at, kind, window)))
    return {"value": number, "unit": spec.unit, "observed_at": observed,
            "nominal_at": nominal, "interval": kind, "window": window,
            "source": source,
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
    intervals = frame.attrs.get("intervals") or {}
    out: list[dict] = []
    for nominal, row in frame.iterrows():
        for column in columns:
            observed = row.get(f"{column}{OBSERVED_SUFFIX}") if f"{column}{OBSERVED_SUFFIX}" in frame else None
            value = quantity(variable_of(column), row[column], nominal_at=nominal,
                             source=source, observed_at=observed,
                             interval=intervals.get(column))
            out.append({"name": column, **value})
    return out


def document(frame: pd.DataFrame) -> dict:
    """The whole series as one JSON-ready object: provenance, gap counts, values."""
    values = records(frame)
    intervals = frame.attrs.get("intervals") or {}
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
        "intervals": {name: dict(zip(("interval", "window"), interval_of(variable_of(name),
                                                                          intervals.get(name))))
                      for name in [c for c in frame.columns
                                   if not str(c).endswith(OBSERVED_SUFFIX)]},
        "values": values,
    }


def dumps(frame: pd.DataFrame) -> str:
    # allow_nan=False: a NaN that slipped past quantity() fails here instead of
    # leaving as the non-JSON token NaN that some readers turn into zero.
    return json.dumps(document(frame), ensure_ascii=False, allow_nan=False)
