# cn-weather-cube

Point weather for renewable-energy work, without downloading anything — with the
unit attached to every number and the source named on every series.

```python
from cube import fetch

df = fetch(lat=22.54, lon=114.06, start="2024-01-01", end="2024-12-31",
           vars="ghi,t2m,ws100")
```

```bash
cube fetch --lat 22.54 --lon 114.06 --start 2024-01-01 --end 2024-12-31 \
           --vars ghi,t2m,ws100 -o shenzhen.parquet
cube sources     # which source answers which question
cube vars        # what can be asked for, and in what units
```

## Why this exists

Getting weather data is no longer the hard part; four public services will hand
you a point series in a second. What is still hard is getting one that means
what you think it means.

This package was extracted from two research projects —
pv-wind-power-forecast and
[aiwp-china-verification](https://github.com/Zhaohh0706/aiwp-china-verification) — and every bug that
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
else:

```python
>>> fetch(lat=40.0, lon=116.0, start="2025-01-01", end="2025-01-07", vars="ws10")
# asked for m/s explicitly, checked that m/s came back

>>> from cube.schema import check_unit
>>> check_unit("ws10", "km/h")
UnitMismatch: ws10: asked for m/s, source returned 'km/h'. Refusing to
continue — a silent unit mismatch here is a factor-of-3.6 error that still
looks like a plausible number.
```

## Which source answers which question

| The question | Source | Why not another |
|---|---|---|
| What was the resource here? | Open-Meteo ERA5 archive | ~1 s for a point. |
| What will the weather be? | Open-Meteo forecast | — |
| **How good is this forecast?** | **previous-runs, at a stated lead** | The ordinary historical-forecast archive returns *the best forecast available for each hour* — whatever the latest run said, at whatever lead that was. A score averaged over that describes no product anyone can buy. |
| Truth for irradiance | Satellite retrieval, product by region | A reanalysis is produced by the same system as the forecast it would judge. An instrument that looked at the sky is not. |
| An area, or pressure levels | ARCO-ERA5 over Zarr | One global chunk per hour, so a single point still reads 48 chunks and takes ~30 s against 1.5 s for the point API. Use it for the shapes a point API cannot do, not for points. |

`choose_source` implements this, and `purpose=` is the argument that selects it.
It is not a convenience: "what was the resource" and "how good is this forecast"
are different questions, and the reanalysis will answer both — wrongly, for the
second.

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
  when only global horizontal is available. That is a correlation fitted to
  mid-latitude data, not a measurement. Pass `dni` and `dhi` if the source has
  them and it will use those instead.
- `hub_wind` fits the **shear exponent per time step** when two levels are
  available, because shear varies by a factor of three between a stable night
  and a convective afternoon. With one level it falls back to the one-seventh
  power law and **says so in the returned series' name**, so the assumption
  travels with the numbers.
- `air_density` returns `rho` with pressure and `rho_no_pressure` without.

A worked example, because the answer is not the one people expect. Shenzhen
(22.5°N), first week of June, panels tilted 22° and facing south:

```
倾斜面/水平面 总量比 0.954
```

**The tilted plane collects four and a half per cent less than a horizontal
one.** In June at this latitude the sun passes nearly overhead, so tilting south
tilts the panel away from it. A tilt chosen for the annual total is the wrong
tilt for the summer months, and a yield estimate that assumes tilting always
helps is wrong in the direction that flatters the project. The same call for the
first week of December returns **1.250** — the same panel, the same site, a
thirty-point swing.

## When the point API runs out: raw files, one message at a time

Point APIs have two ceilings. The archives that serve forecasts **at a stated
lead** let a few stations through an hour, so a twelve-station study waits days
for data that is already public. And a point API serves the variables it chose
to serve — ask for a level or a model it does not carry and there is no request
to make.

ECMWF and NOAA both publish every run to S3 with a sidecar index giving the byte
offset of every message inside the file. One 100 m wind field is **1.4 MB inside
a 146 MB file**, so a range request fetches one per cent of the bytes, with no
quota at all.

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
is in the file's name**, so it cannot drift — which is the property a
verification study needs and the one a "latest available" archive cannot give.

What the raw files ask in return is that nothing is done for you, and each of
those things is a number that comes out plausible and wrong:

- The file says `m s**-1` and `K`. Both are normalised and converted explicitly,
  and a unit with no conversion defined is refused rather than passed through.
- **ECMWF's irradiance is joules accumulated since the start of the forecast**;
  GFS's is watts averaged over a window. Taking either as an instantaneous flux
  is wrong by a factor of thousands, and it looks merely large at step 1. Here
  `ghi` is differenced between steps and divided by the seconds between them;
  GFS irradiance is **refused** rather than guessed at, because its averaging
  window is a different question and is not implemented.
- A 0.25° grid has no point at your site. The distance to the one it used
  travels with every row, and a request whose nearest cell is more than 40 km
  away is refused instead of being passed off as the site.
- NOAA's index gives only where each message starts, so its length is the next
  message's start. Off by one entry and the range returns bytes that begin with
  `GRIB` and end mid-field — which sometimes decodes.

Needs a GRIB decoder, which is why it is an extra: `pip install
"cn-weather-cube[opendata]"`.

## Caching

Keyed on a hash of the whole request — source, coordinates, dates, variables,
model list and lead. Anything that would change the data changes the key, so
adding a model cannot serve you the file from before you added it. The request
is written as JSON beside each file, so a cache directory is readable six months
later without reversing a hash.

Location: `$CN_WEATHER_CUBE_CACHE`, default `~/.cache/cn-weather-cube`.

## Install

```bash
pip install -e .              # core: pandas, pyarrow, numpy
pip install -e '.[features]'  # + pvlib, for the renewable-energy features
pip install -e '.[area]'      # + xarray/zarr/gcsfs, for ARCO-ERA5
```

## Limits, stated rather than discovered

- **Open-Meteo is a free shared service with a daily quota.** A broad search —
  hundreds of candidate points over a multi-year window — will exhaust it, and
  the failure is an HTTP 429 halfway through. Requests here back off hard on
  429 rather than retrying into the wall, but the quota is still the binding
  constraint on anything wide. Ask for the shortest window that answers the
  question.
- **The Chinese operational models are not in here.** CMA-GFS and CMA-MESO grids
  are not open to individuals; what can be used commercially is ECMWF Open Data,
  GFS, AIFS and ERA5 under CC BY 4.0 / Copernicus terms. CMA station
  observations need a licence.
- **Providing weather services commercially in China requires filing with the
  provincial meteorological bureau**, and reselling CMA data has separate
  restrictions. This package fetches public data for your own use; it does not
  make you a licensed provider.
- **A reanalysis is not an observation.** ERA5 at 100 m is a model field, and
  scoring a forecast against it mostly measures how close that forecast is to
  ECMWF's own analysis. Where this matters the package labels the source rather
  than leaving the word "truth" to do the work.

## Layout

```
cube/
  schema.py            variables, units, and the refusal when they do not match
  api.py               fetch(), and which source answers which question
  cache.py             request-hashed local cache
  features.py          POA irradiance, hub wind, air density, clear-sky index
  cli.py               cube fetch / verify / sources / vars
  sources/
    openmeteo.py       archive, forecast, fixed-lead, satellite, elevation
    arco_era5.py       Zarr, for areas and pressure levels (optional extra)
tests/                 42 tests, none of which touch the network
```

## Related repositories

- [aiwp-china-verification](https://github.com/Zhaohh0706/aiwp-china-verification) — fixed-lead verification of physics and AI weather models at Chinese stations
- [green-ai-ledger](https://github.com/Zhaohh0706/green-ai-ledger) — compute energy and carbon, with the grid factor pinned rather than guessed
