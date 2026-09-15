"""ARCO-ERA5 over Zarr, for the requests a point API is the wrong shape for.

Two cases need this and nothing else does:

* **an area**, where pulling ten thousand grid points one at a time through a
  point API is the slow way to do the wrong thing;
* **pressure levels**, which the point APIs do not serve at all — anything
  involving a sounding, a boundary-layer profile or hub heights above 120 m.

For a single point over a past period this is the worse choice by a wide
margin: the store holds one global chunk per hour, so a single point still reads
forty-eight chunks and takes about thirty seconds, against a second and a half
for the point API. The decision table in the README says so, and
``cube.api.choose_source`` never routes here on its own.

The dependencies are heavy (xarray, zarr, gcsfs) and are an optional extra:
``pip install cn-weather-cube[area]``.
"""
from __future__ import annotations

import pandas as pd

# Google's analysis-ready, cloud-optimised ERA5, public and requester-pays-free.
STORE = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"

# ERA5 short names for the quantities this package exposes.
FIELDS = {
    "ghi": "surface_solar_radiation_downwards",
    "t2m": "2m_temperature",
    "d2m": "2m_dewpoint_temperature",
    "sp": "surface_pressure",
    "u10": "10m_u_component_of_wind",
    "v10": "10m_v_component_of_wind",
    "u100": "100m_u_component_of_wind",
    "v100": "100m_v_component_of_wind",
}


def _require():
    try:
        import xarray  # noqa: F401
    except ImportError as error:  # pragma: no cover - import guard
        raise ImportError(
            "ARCO-ERA5 needs the optional extras: pip install cn-weather-cube[area]"
        ) from error


def open_store(chunks: str | dict = "auto"):
    """Open the store lazily. Nothing is downloaded until something is sliced."""
    _require()
    import xarray as xr

    return xr.open_zarr(STORE, chunks=chunks, storage_options={"token": "anon"})


def area(
    fields: list[str],
    start: str,
    end: str,
    lat_range: tuple[float, float],
    lon_range: tuple[float, float],
):
    """A rectangle of ERA5, as an xarray Dataset, still lazy.

    ERA5 longitudes run 0 to 360 and its latitudes run north to south. Passing a
    western longitude or an ascending latitude slice returns an empty selection
    rather than an error, so both are normalised here.
    """
    dataset = open_store()
    lon_lo, lon_hi = (x % 360 for x in lon_range)
    lat_hi, lat_lo = max(lat_range), min(lat_range)
    return dataset[[FIELDS.get(f, f) for f in fields]].sel(
        time=slice(start, end),
        latitude=slice(lat_hi, lat_lo),
        longitude=slice(lon_lo, lon_hi),
    )


def point(fields: list[str], start: str, end: str, lat: float, lon: float) -> pd.DataFrame:
    """One point, for completeness. The point API is about twenty times faster."""
    dataset = open_store()
    selected = dataset[[FIELDS.get(f, f) for f in fields]].sel(
        time=slice(start, end),
        latitude=lat,
        longitude=lon % 360,
        method="nearest",
    )
    return selected.to_dataframe()
