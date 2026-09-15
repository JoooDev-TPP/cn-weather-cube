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

Every bug that cost this project a day was a unit or a name that looked right:

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
