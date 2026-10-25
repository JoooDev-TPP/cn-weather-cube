# cn-weather-cube

[![tests](https://github.com/JoooDev-TPP/cn-weather-cube/actions/workflows/test.yml/badge.svg)](https://github.com/JoooDev-TPP/cn-weather-cube/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/github/license/JoooDev-TPP/cn-weather-cube)](LICENSE)

Point weather for renewable-energy work, without downloading anything — with the
unit attached to every number and the source named on every series. When a
source returns a unit you did not ask for, it refuses instead of handing you a
plausible wrong number.

```bash
pip install "git+https://github.com/JoooDev-TPP/cn-weather-cube"

cube fetch --lat 22.54 --lon 114.06 --start 2024-01-01 --end 2024-12-31 \
           --vars ghi,t2m,ws100 -o shenzhen.parquet
cube sources     # which source answers which question
cube vars        # what can be asked for, and in what units
```

```python
>>> from cube import fetch
>>> df = fetch(lat=22.54, lon=114.06, start="2024-01-01", end="2024-12-31",
...            vars="ghi,t2m,ws100")

>>> from cube.schema import check_unit
>>> check_unit("ws10", "km/h")
UnitMismatch: ws10: asked for m/s, source returned 'km/h'. Refusing to
continue — a silent unit mismatch here is a factor-of-3.6 error that still
looks like a plausible number.
```

<!-- TODO: optional terminal screenshot or short GIF of `cube fetch` + `cube sources` (ripgrep/bat pattern); none exists yet -->

Extras: `.[features]` adds pvlib for the renewable-energy features, `.[area]`
adds xarray/zarr/gcsfs for ARCO-ERA5, `.[opendata]` adds a GRIB decoder for raw
forecast files. From a clone: `pip install -e .`.

## Why this exists

Getting weather data is no longer the hard part; four public services will hand
you a point series in a second. What is still hard is getting one that means
what you think it means.

This package was extracted from two research projects —
pv-wind-power-forecast and
[aiwp-china-verification](https://github.com/JoooDev-TPP/aiwp-china-verification) — and every bug that
cost either of them a day was a unit or a name that looked right:

- Open-Meteo returns wind in **km/h** unless told otherwise and METAR reports it
  in **knots**. Compare them and the forecast is 3.6 or 1.9 times too fast, and
  the scorecard still looks like a scorecard.
- SMARD serves **energy per quarter hour**, not power. German midday
  photovoltaics reads 12 GW instead of 50 GW, and nothing about the series looks
  wrong.
- The **"seamless" satellite product over Europe begins in February 2026**. A
  study starting in 2024 loses nineteen months of solar truth and is never told.
- A column named `rmse_c`, for celsius, ends up holding Wh/m² the day a third
  variable arrives.

None of those raise. They produce plausible numbers. So here a variable is not a
string — it is a name, a unit, and what to do when a source offers something
else.

## Which source answers which question

| The question | Source | Why not another |
|---|---|---|
| What was the resource here? | Open-Meteo ERA5 archive | ~1 s for a point. |
| What will the weather be? | Open-Meteo forecast | — |
| **How good is this forecast?** | **previous-runs, at a stated lead** | The ordinary historical-forecast archive returns *the best forecast available for each hour* — whatever the latest run said, at whatever lead that was. A score averaged over that describes no product anyone can buy. |
| Truth for irradiance | Satellite retrieval, product by region | A reanalysis is produced by the same system as the forecast it would judge. An instrument that looked at the sky is not. |
| An area, or pressure levels | ARCO-ERA5 over Zarr | One global chunk per hour, so a single point still reads 48 chunks and takes ~30 s against 1.5 s for the point API. Use it for the shapes a point API cannot do, not for points. |

`choose_source` implements this, and `purpose=` is the argument that selects it.
"What was the resource" and "how good is this forecast" are different questions,
and the reanalysis will answer both — wrongly, for the second.

## Renewable-energy features

```python
from cube import features

poa = features.plane_of_array(df, lat=22.54, lon=114.06, tilt=22)   # W/m²
hub = features.hub_wind(df, hub_height=90)                          # m/s
rho = features.air_density(df)                                      # kg/m³
kt  = features.clearsky_index(df, lat=22.54, lon=114.06)            # 0–1
```

Each states what it assumed where it had to assume:

- `plane_of_array` separates beam from diffuse with the **Erbs correlation**
  when only global horizontal is available — a correlation fitted to
  mid-latitude data, not a measurement. Pass `dni` and `dhi` if the source has
  them and it will use those instead.
- `hub_wind` fits the **shear exponent per time step** when two levels are
  available, because shear varies by a factor of three between a stable night
  and a convective afternoon. With one level it falls back to the one-seventh
  power law and **says so in the returned series' name**.
- `air_density` returns `rho` with pressure and `rho_no_pressure` without.

A worked example, because the answer is not the one people expect. Shenzhen
(22.5°N), first week of June, panels tilted 22° and facing south:

```
倾斜面/水平面 总量比 0.954      # tilted / horizontal total
```

**The tilted plane collects four and a half per cent less than a horizontal
one.** In June at this latitude the sun passes nearly overhead, so tilting south
tilts the panel away from it. The same call for the first week of December
returns **1.250** — the same panel, the same site, a thirty-point swing.

## Raw forecast files, one message at a time

ECMWF and NOAA publish every run to S3 with a sidecar index giving the byte
offset of every message. One 100 m wind field is **1.4 MB inside a 146 MB
file**, so a range request fetches one per cent of the bytes, with no quota.

```python
from cube.sources import opendata

opendata.series("ifs", "2026-09-20 00:00", "ws100", lat=22.54, lon=114.06,
                steps=(24, 48))
```

```
 run                  step_hours  value  unit  grid_distance_km  model
 2026-09-20 00:00:00          24   2.30   m/s               7.6  ECMWF IFS 0.25°
 2026-09-20 00:00:00          48   3.28   m/s               7.6  ECMWF IFS 0.25°
```

Three models: `ifs`, `aifs` (ECMWF's machine-learned model) and `gfs`. **The lead
is in the file's name**, so it cannot drift. What the raw files ask in return:

- Units are normalised and converted explicitly (`m s**-1`, `K`); a unit with no
  conversion defined is refused.
- **ECMWF's irradiance is joules accumulated since the start of the forecast**;
  here `ghi` is differenced between steps and divided by the seconds between
  them. GFS irradiance is **refused** — its averaging window is a different
  question and is not implemented.
- The distance to the grid point used travels with every row; a nearest cell
  more than 40 km away is refused.
- NOAA's index gives only where each message starts, so its length is the next
  message's start — off by one and the bytes still begin with `GRIB`.

**Where this does not help.** A message is one field over the whole globe, so
cost scales with the number of *times*, not sites. One model, one variable, one
lead, hourly over eighteen months is 13,176 messages — **18 GB**; five leads is
90 GB; four models and two variables is 721 GB. It is not a way around the
quota for a long backfill; it is the way to get a model, level or field the
point archive does not carry.

## Missing values, substitutes and fallbacks

`cube fetch ... -o series.json` (or `cube.contract.document(frame)`) writes every
value as

```json
{"name": "precip", "value": null, "unit": "mm", "observed_at": null,
 "nominal_at": "2025-01-01T01:00:00Z", "interval": "preceding_sum", "window": "PT1H",
 "source": "openmeteo.archive", "qc_flag": "missing"}
```

- **A gap is `null`, never `0`.** Missing precipitation is not "no rain".
  Nothing is interpolated or carried forward. csv and parquet keep it empty / NaN.
- **One quantity never stands in for another.** `sp` (station pressure) is read
  only from `surface_pressure`, `msl` (QNH-like) only from `pressure_msl`, and a
  column that merely starts with a field's name (`precipitation_probability`) is
  never taken. A field the source does not publish is an error, not a borrow.
- **A fallback is written on every value.** `--fallback forecast` takes the whole
  series from the other endpoint when the first fails and marks it
  `"openmeteo.forecast;fallback_from=openmeteo.archive"`. A unit refusal is never
  routed around, and gaps are never patched from a second source.
- **`nominal_at` and `observed_at` are separate, and `interval` says why.**
  `interval` is `instant`, `preceding_mean`, `following_mean`, `preceding_sum` or
  `unknown`, with `window` (e.g. `PT1H`). Open-Meteo's plain radiation fields are
  the mean of the hour *before* the label, so the 12:00 value is observed at
  11:30; read as the 12:00 value it puts sunrise half an hour late. `ghi`/`dni`/`dhi`
  are therefore fetched as Open-Meteo's `_instant` fields by default;
  `--irradiance preceding_mean` asks for the hourly means and labels them so.
  Contract `point-weather/2` (`/1` lacked `interval`/`window` and claimed gridded
  radiation was observed at its nominal hour).
- `qc_flag` is `ok`, `missing` or `out_of_range` (kept as reported, not clipped).

## Caching

Keyed on a hash of the whole request — source, coordinates, dates, variables,
model list and lead — so adding a model cannot serve you the file from before you
added it. The request is written as JSON beside each file. Location:
`$CN_WEATHER_CUBE_CACHE`, default `~/.cache/cn-weather-cube`.

## Limits, stated rather than discovered

- **Open-Meteo is a free shared service with a daily quota.** A broad search
  will exhaust it, and the failure is an HTTP 429 halfway through. Requests back
  off hard on 429, but ask for the shortest window that answers the question.
- **The Chinese operational models are not in here.** CMA-GFS and CMA-MESO grids
  are not open to individuals; what can be used commercially is ECMWF Open Data,
  GFS, AIFS and ERA5 under CC BY 4.0 / Copernicus terms. CMA station
  observations need a licence.
- **Providing weather services commercially in China requires filing with the
  provincial meteorological bureau**, and reselling CMA data has separate
  restrictions. This package fetches public data for your own use.
- **A reanalysis is not an observation.** ERA5 at 100 m is a model field, and
  scoring a forecast against it mostly measures how close that forecast is to
  ECMWF's own analysis. The package labels the source rather than leaving the
  word "truth" to do the work.

## Layout

```
cube/
  schema.py      variables, units, and the refusal when they do not match
  api.py         fetch(), and which source answers which question
  contract.py    value|null, unit, observed_at, nominal_at, interval, window, source, qc_flag
  cache.py       request-hashed local cache
  features.py    POA irradiance, hub wind, air density, clear-sky index
  cli.py         cube fetch / verify / sources / vars
  sources/
    openmeteo.py   archive, forecast, fixed-lead, satellite, elevation
    opendata.py    ECMWF / NOAA raw files by byte range (optional extra)
    arco_era5.py   Zarr, for areas and pressure levels (optional extra)
tests/           no test touches the network — `pytest`
```

## Related repositories

- [aiwp-china-verification](https://github.com/JoooDev-TPP/aiwp-china-verification) — fixed-lead verification of physics and AI weather models at Chinese stations
- [green-ai-ledger](https://github.com/JoooDev-TPP/green-ai-ledger) — compute energy and carbon, with the grid factor pinned rather than guessed

## License

Code: MIT. Data fetched through this package carries its source's terms
(CC BY 4.0 / Copernicus terms — see Limits).
