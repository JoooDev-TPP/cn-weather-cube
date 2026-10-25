<img src=".github/banner.svg" width="100%" alt="cn-weather-cube: point weather for renewable-energy work, with units and sources attached">

[![tests](https://github.com/JoooDev-TPP/cn-weather-cube/actions/workflows/test.yml/badge.svg)](https://github.com/JoooDev-TPP/cn-weather-cube/actions/workflows/test.yml) [![License: MIT](https://img.shields.io/github/license/JoooDev-TPP/cn-weather-cube)](LICENSE)

Point weather for renewable-energy work, with nothing to download first. Every number carries its unit and every series names its source. When a source returns a unit you did not ask for, the call fails instead of handing you a plausible wrong number.

## Quick start

```bash
git clone https://github.com/JoooDev-TPP/cn-weather-cube
cd cn-weather-cube
pip install -e .

cube fetch --lat 22.54 --lon 114.06 \
  --start 2024-01-01 --end 2024-12-31 \
  --vars ghi,t2m,ws100 -o shenzhen.parquet
cube sources
cube vars
```

`cube fetch` writes a year of hourly irradiance, temperature and 100 m wind for Shenzhen. `cube sources` shows which source answers which question, and `cube vars` lists every variable with its unit. Python 3.10 or later; fetching needs a network connection.

The same from Python, and what a unit mismatch looks like:

```python
>>> from cube import fetch
>>> df = fetch(lat=22.54, lon=114.06, start="2024-01-01",
...            end="2024-12-31", vars="ghi,t2m,ws100")

>>> from cube.schema import check_unit
>>> check_unit("ws10", "km/h")
UnitMismatch: ws10: asked for m/s, source returned 'km/h'.
Refusing to continue — a silent unit mismatch here is a
factor-of-3.6 error that still looks like a plausible number.
```

Optional extras: `.[features]` adds pvlib for the renewable-energy features, `.[area]` adds xarray, zarr and gcsfs for ARCO-ERA5, and `.[opendata]` adds a GRIB decoder for raw forecast files. Run the tests with `pip install -e ".[dev]"` and `pytest`; none of them touch the network.

## Why

Four public services will hand you a point series in a second. The hard part is getting one that means what you think it means.

This package was extracted from two research projects, pv-wind-power-forecast and [aiwp-china-verification](https://github.com/JoooDev-TPP/aiwp-china-verification). Every bug that cost either of them a day was a unit or a name that looked right:

- Open-Meteo returns wind in km/h unless told otherwise, and METAR reports it in knots. Compare them and the forecast is 3.6 or 1.9 times too fast. The scorecard still looks like a scorecard.
- SMARD serves energy per quarter hour, not power. German midday photovoltaics reads 12 GW instead of 50 GW, and nothing about the series looks wrong.
- The "seamless" satellite product over Europe begins in February 2026. A study starting in 2024 loses nineteen months of solar truth and is never told.
- A column named `rmse_c`, for celsius, ends up holding Wh/m² the day a third variable arrives.

None of those raise an error. They produce plausible numbers. So in this package a variable has a name, a unit, and a rule for what to do when a source offers something else.

## Sources

| Question | Source | Why this one |
|---|---|---|
| What was the resource at this point? | Open-Meteo ERA5 archive | About 1 s for a point. |
| What will the weather be? | Open-Meteo forecast | The current run. |
| How good is this forecast? | Previous-runs archive, at a stated lead | The ordinary historical-forecast archive returns the best forecast available for each hour, at whatever lead the latest run had. A score averaged over that describes no product anyone can buy. |
| Truth for irradiance | Satellite retrieval, product chosen by region | A reanalysis comes from the same system as the forecast it would judge. An instrument that looked at the sky does not. |
| An area, or pressure levels | ARCO-ERA5 over Zarr | One global chunk per hour, so a single point still reads 48 chunks and takes about 30 s, against 1.5 s for the point API. Use it for shapes a point API cannot serve. |

`choose_source` implements this table, and its `purpose=` argument selects the row. A reanalysis will answer both of the first two questions, and for the second one it answers wrongly.

## Features

```python
from cube import features

# W/m², m/s, kg/m³, and an index from 0 to 1
poa = features.plane_of_array(df, lat=22.54, lon=114.06, tilt=22)
hub = features.hub_wind(df, hub_height=90)
rho = features.air_density(df)
kt = features.clearsky_index(df, lat=22.54, lon=114.06)
```

Each one states what it assumed:

- `plane_of_array` separates beam from diffuse with the Erbs correlation when only global horizontal irradiance is available. Erbs is a correlation fitted to mid-latitude data, not a measurement. Pass `dni` and `dhi` if the source has them and they are used instead.
- `hub_wind` fits the shear exponent per time step when two levels are available, because shear varies by a factor of three between a stable night and a convective afternoon. With one level it falls back to the one-seventh power law and says so in the returned series' name.
- `air_density` returns `rho` with pressure and `rho_no_pressure` without.

A worked example. In Shenzhen (22.5°N), in the first week of June, with panels tilted 22° and facing south:

```
倾斜面/水平面 总量比 0.954      # tilted / horizontal total
```

The tilted plane collects four and a half per cent less than a horizontal one. In June at this latitude the sun passes nearly overhead, so tilting south tilts the panel away from it. The same call for the first week of December returns 1.250: same panel, same site, a thirty-point swing.

## Raw forecast files

ECMWF and NOAA publish every run to S3 with a sidecar index that gives the byte offset of every message. One 100 m wind field is 1.4 MB inside a 146 MB file, so a range request fetches one per cent of the bytes, with no quota.

```python
from cube.sources import opendata

opendata.series("ifs", "2026-09-20 00:00", "ws100",
                lat=22.54, lon=114.06, steps=(24, 48))
```

| run | step_hours | value | unit | grid_distance_km | model |
|---|---:|---:|---|---:|---|
| 2026-09-20 00:00:00 | 24 | 2.30 | m/s | 7.6 | ECMWF IFS 0.25° |
| 2026-09-20 00:00:00 | 48 | 3.28 | m/s | 7.6 | ECMWF IFS 0.25° |

Three models are supported: `ifs`, `aifs` (ECMWF's machine-learned model) and `gfs`. The lead is in the file's name, so it cannot drift.

- Units are normalised and converted explicitly (`m s**-1`, `K`). A unit with no defined conversion is refused.
- ECMWF's irradiance is joules accumulated since the start of the forecast. `ghi` is differenced between steps and divided by the seconds between them. GFS irradiance is refused: its averaging window is a different question and is not implemented.
- Every row carries the distance to the grid point used. A nearest cell more than 40 km away is refused.
- NOAA's index gives only where each message starts, so its length is the next message's start. Get that off by one and the bytes still begin with `GRIB`.

This does not help with long backfills. A message is one field over the whole globe, so cost scales with the number of times, not sites. One model, one variable and one lead, hourly over eighteen months, is 13,176 messages and 18 GB. Five leads is 90 GB; four models and two variables is 721 GB. Use raw files for a model, level or field the point archive does not carry.

## Missing values and fallbacks

`cube fetch ... -o series.json` (or `cube.contract.document(frame)`) writes every value like this:

```json
{
  "name": "precip",
  "value": null,
  "unit": "mm",
  "observed_at": null,
  "nominal_at": "2025-01-01T01:00:00Z",
  "interval": "preceding_sum",
  "window": "PT1H",
  "source": "openmeteo.archive",
  "qc_flag": "missing"
}
```

- A gap is `null`, never `0`. Missing precipitation is not "no rain". Nothing is interpolated or carried forward. CSV and Parquet leave the cell empty or NaN.
- One quantity never stands in for another. `sp` (station pressure) is read only from `surface_pressure`, and `msl` (QNH-like) only from `pressure_msl`. A column that merely starts with a field's name, such as `precipitation_probability`, is never taken. A field the source does not publish is an error.
- Every value records its fallback. `--fallback forecast` takes the whole series from the other endpoint when the first fails and marks it `"openmeteo.forecast;fallback_from=openmeteo.archive"`. A unit refusal is never routed around, and gaps are never patched from a second source.
- `nominal_at` and `observed_at` are separate fields, and `interval` says why. It is `instant`, `preceding_mean`, `following_mean`, `preceding_sum` or `unknown`, with `window` (for example `PT1H`). Open-Meteo's plain radiation fields are the mean of the hour before the label, so the 12:00 value is observed at 11:30; read as the 12:00 value, it puts sunrise half an hour late. `ghi`, `dni` and `dhi` are therefore fetched as Open-Meteo's `_instant` fields by default. `--irradiance preceding_mean` asks for the hourly means and labels them so. This is contract `point-weather/2`; version 1 lacked `interval` and `window` and claimed gridded radiation was observed at its nominal hour.
- `qc_flag` is `ok`, `missing` or `out_of_range`. Out-of-range values are kept as reported, not clipped.

## Caching

The cache key is a hash of the whole request: source, coordinates, dates, variables, model list and lead. Adding a model cannot serve you the file from before you added it. The request is saved as JSON beside each file. The location is `$CN_WEATHER_CUBE_CACHE`, by default `~/.cache/cn-weather-cube`.

## Limits

- Open-Meteo is a free shared service with a daily quota. A broad search will exhaust it, and the failure is an HTTP 429 halfway through. Requests back off hard on 429, but ask for the shortest window that answers the question.
- The Chinese operational models are not included. CMA-GFS and CMA-MESO grids are not open to individuals. What can be used commercially is ECMWF Open Data, GFS, AIFS and ERA5 under CC BY 4.0 or Copernicus terms. CMA station observations need a licence.
- Providing weather services commercially in China requires filing with the provincial meteorological bureau, and reselling CMA data has separate restrictions. This package fetches public data for your own use.
- A reanalysis is not an observation. ERA5 at 100 m is a model field, and scoring a forecast against it mostly measures how close that forecast is to ECMWF's own analysis. The package labels the source instead of calling it "truth".

## Layout

```
cube/
  schema.py      variables, units, and the refusal on mismatch
  api.py         fetch(), and which source answers which question
  contract.py    the point-weather/2 record written for each value
  cache.py       request-hashed local cache
  features.py    POA irradiance, hub wind, air density, clear-sky
  cli.py         cube fetch / verify / sources / vars
  sources/
    openmeteo.py   archive, forecast, fixed-lead, satellite
    opendata.py    ECMWF / NOAA raw files by byte range
    arco_era5.py   Zarr, for areas and pressure levels
tests/           no test touches the network
```

## License

Code is MIT. Data fetched through this package carries its source's terms (CC BY 4.0 or Copernicus terms; see Limits).

See also [aiwp-china-verification](https://github.com/JoooDev-TPP/aiwp-china-verification), which this package was extracted from, and [green-ai-ledger](https://github.com/JoooDev-TPP/green-ai-ledger).
