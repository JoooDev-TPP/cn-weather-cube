"""Open-Meteo: reanalysis, forecast, fixed-lead archive and satellite retrieval.

Four endpoints behind one function, because the right one depends on what is
being asked for and getting it wrong is quiet:

``archive``
    ERA5 reanalysis. The weather that happened, as far as a reanalysis knows.
    Use it for resource assessment and for training. Do not use it to score a
    forecast: it is not a forecast, and a benchmark driven by it is an
    optimistic bound rather than a result.

``forecast``
    The current run, going forward.

``previous_runs``
    What a model said *n* days before, per model. This is the only endpoint that
    supports honest verification, because "the best forecast available for each
    hour" — which is what the plain historical-forecast archive returns — is
    whatever the most recent run said, at whatever lead that happened to be. A
    score averaged over that describes no product anyone can buy.

``satellite``
    Retrieved irradiance. An instrument looked at the sky, so unlike a
    reanalysis it is independent of any forecast model and can serve as truth.
    The product must be chosen by region: over Europe the "seamless" selector
    only begins in February 2026, and a study starting earlier loses its solar
    truth without being told.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

from .. import cache
from ..schema import Variable, check_unit

ENDPOINTS = {
    "archive": "https://archive-api.open-meteo.com/v1/archive",
    "forecast": "https://api.open-meteo.com/v1/forecast",
    "previous_runs": "https://previous-runs-api.open-meteo.com/v1/forecast",
    "satellite": "https://satellite-api.open-meteo.com/v1/archive",
    "elevation": "https://api.open-meteo.com/v1/elevation",
}

USER_AGENT = "cn-weather-cube/0.1"

# How this package's names map onto Open-Meteo's.
FIELDS = {
    "ghi": "shortwave_radiation",
    "dni": "direct_normal_irradiance",
    "dhi": "diffuse_radiation",
    "t2m": "temperature_2m",
    "d2m": "dew_point_2m",
    "rh": "relative_humidity_2m",
    "sp": "surface_pressure",
    "ws10": "wind_speed_10m",
    "wd10": "wind_direction_10m",
    "ws100": "wind_speed_100m",
    "wd100": "wind_direction_100m",
    "cloud": "cloud_cover",
    "precip": "precipitation",
}

# Satellite products by region.  "Seamless" picks for you and picks wrong often
# enough — over Europe it starts in 2026 — that the region is asked for instead.
SATELLITE_BY_REGION = {
    "east_asia": "jma_jaxa_himawari",
    "europe": "eumetsat_sarah3",
    "africa": "eumetsat_lsa_saf_msg",
    "indian_ocean": "eumetsat_lsa_saf_iodc",
    "auto": "satellite_radiation_seamless",
}


def satellite_region(latitude: float, longitude: float) -> str:
    """Which geostationary satellite actually sees this point."""
    if 60.0 <= longitude <= 180.0:
        return "east_asia"
    if -30.0 <= longitude < 40.0 and latitude >= 30.0:
        return "europe"
    if -30.0 <= longitude < 60.0:
        return "africa"
    return "auto"


def _get(url: str, params: dict, retries: int = 6, timeout: int = 240) -> dict | list:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params, doseq=True)}",
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - network, retried
            last = error
            # A 429 is a shared quota running out, not a bad request.  Backing
            # off hard is the only correct response to it.
            time.sleep(min(90, 5 * 2**attempt))
    raise RuntimeError(f"open-meteo {url} failed after {retries} tries: {last}")


def fetch(
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    variables: list[Variable],
    endpoint: str = "archive",
    models: list[str] | None = None,
    lead_days: int | None = None,
    region: str | None = None,
    refresh: bool = False,
    cache_root=None,
) -> pd.DataFrame:
    """One point, hourly, in this package's units, cached."""
    if endpoint not in ENDPOINTS:
        raise ValueError(f"unknown endpoint {endpoint!r}; expected {sorted(ENDPOINTS)}")

    names = [v.name for v in variables]
    fields = [FIELDS[n] for n in names]
    if endpoint == "previous_runs":
        if lead_days is None:
            raise ValueError(
                "previous_runs needs lead_days: the whole point of this endpoint "
                "is that the lead is stated rather than whatever was latest."
            )
        fields = [f"{f}_previous_day{lead_days}" for f in fields]

    request = {
        "source": f"openmeteo.{endpoint}",
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "start": start,
        "end": end,
        "variables": names,
        "models": models,
        "lead_days": lead_days,
    }
    if endpoint == "satellite":
        product = SATELLITE_BY_REGION[
            region or satellite_region(latitude, longitude)
        ]
        request["product"] = product

    if not refresh:
        cached = cache.load(request, cache_root)
        if cached is not None:
            return cached

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(fields),
        "timezone": "UTC",
        # Asked for explicitly every time.  The default is km/h, and the
        # difference between that and m/s is a factor of 3.6 applied to a
        # number that still looks like a wind speed.
        "wind_speed_unit": "ms",
    }
    if endpoint != "forecast":
        params["start_date"], params["end_date"] = start, end
    if endpoint == "satellite":
        params["models"] = request["product"]
    elif models:
        params["models"] = ",".join(models)

    payload = _get(ENDPOINTS[endpoint], params)
    units = payload.get("hourly_units", {})
    frame = pd.DataFrame(payload["hourly"])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.set_index("time").sort_index()

    out = pd.DataFrame(index=frame.index)
    for variable, field in zip(variables, fields):
        matching = [c for c in frame.columns if c.startswith(field)]
        if not matching:
            raise RuntimeError(
                f"{variable.name}: asked for {field}, the response has "
                f"{sorted(frame.columns)}. This source does not publish it here."
            )
        for column in matching:
            check_unit(variable.name, units.get(column, ""))
            # Multi-model responses suffix the model onto the field name; keep
            # that suffix so two models never collapse into one column.
            suffix = column[len(field):].lstrip("_")
            out[f"{variable.name}_{suffix}" if suffix else variable.name] = frame[column]

    cache.store(request, out, cache_root)
    return out


def elevation(points: list[tuple[float, float]]) -> list[float]:
    """Terrain height at each point. Cheap, and it rules out most of a map."""
    payload = _get(
        ENDPOINTS["elevation"],
        {
            "latitude": ",".join(str(a) for a, _ in points),
            "longitude": ",".join(str(o) for _, o in points),
        },
    )
    return payload["elevation"]
