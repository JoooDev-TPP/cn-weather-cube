"""The one call this package exists for.

    from cube import fetch
    df = fetch(lat=22.54, lon=114.06, start="2024-01-01", end="2024-12-31",
               vars="ghi,t2m,ws100")

Source selection is the part worth automating, because the right source depends
on the question and the wrong one fails quietly:

* a **point** over a **past** period → Open-Meteo's ERA5 archive, one request,
  about a second;
* a point over a **future** period → the forecast endpoint;
* a forecast to be **scored** → the previous-runs endpoint at a stated lead,
  never the plain archive, which returns "whatever was latest" and produces a
  score describing no product anyone can buy;
* **truth for irradiance** → the satellite retrieval for the right region, since
  a reanalysis is not independent of the forecasts it would be used to judge;
* an **area** or **pressure levels** → ARCO-ERA5 over Zarr, because pulling
  thousands of points one at a time through a point API is the slow way to do
  the wrong thing.

The last of those is a separate module and an optional dependency; everything
else needs nothing but the standard library and pandas.
"""
from __future__ import annotations

import pandas as pd

from .schema import Variable, resolve
from .sources import openmeteo


def choose_source(start: str, end: str, purpose: str) -> str:
    """Which endpoint answers this, and why.

    ``purpose`` is not a convenience: it is the difference between a number that
    means something and one that does not. "What was the resource here" and
    "how good is this forecast" are answered by different endpoints, and the
    reanalysis will happily answer both — wrongly, for the second.
    """
    if purpose == "verify":
        return "previous_runs"
    if purpose == "truth":
        return "satellite"
    today = pd.Timestamp.now(tz="UTC").normalize()
    return "forecast" if pd.Timestamp(start, tz="UTC") > today else "archive"


def fetch(
    lat: float,
    lon: float,
    start: str,
    end: str,
    vars: str | list[str] = "ghi,t2m,ws10",
    purpose: str = "resource",
    models: list[str] | None = None,
    lead_days: int | None = None,
    source: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Hourly weather at one point, in this package's units.

    ``purpose`` is one of:

    ``resource``
        What the weather was or will be. Reanalysis for the past, forecast for
        the future.
    ``verify``
        What a model said at a fixed lead, for scoring. Requires ``lead_days``.
    ``truth``
        An observation to score against — the satellite retrieval, chosen by
        region.
    """
    variables: list[Variable] = resolve(vars)
    endpoint = source or choose_source(start, end, purpose)
    frame = openmeteo.fetch(
        lat, lon, start, end, variables,
        endpoint=endpoint, models=models, lead_days=lead_days, refresh=refresh,
    )
    # Recorded on the frame rather than in a docstring: six months later the
    # question about any saved series is which endpoint produced it.
    frame.attrs.update(
        {"source": f"openmeteo.{endpoint}", "purpose": purpose,
         "lat": lat, "lon": lon, "lead_days": lead_days,
         "units": {v.name: v.unit for v in variables}}
    )
    return frame
