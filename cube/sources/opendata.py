"""Raw forecast files, one message at a time, over HTTP range requests.

Everything else in this package asks a point API a question and gets a small
answer back. That is the right shape almost always, and it has two ceilings:

* **A quota.** The archives that serve forecasts at a stated lead let through a
  few stations an hour. A study that wants twelve stations and three variables
  waits days for data that is sitting in a public bucket.
* **A menu.** A point API serves the variables it chose to serve. Ask for
  something it does not carry — a level, a model, a field — and there is no
  request to make.

The raw files have neither, and they are public: ECMWF's open data and NOAA's
GFS both publish every run to S3, with a sidecar index giving the byte offset of
every message inside. One 100 m wind field is 1.4 MB inside a 146 MB file, so
fetching exactly that message costs **one per cent of the bytes** and no quota
at all.

What this costs in return is that nothing is done for you. The file says
``m s**-1`` and ``K``; ECMWF's irradiance is joules accumulated since the start
of the forecast while GFS's is watts averaged over a window; a 0.25° grid has no
point at your site, only one near it. Each of those is a number that comes out
plausible and wrong. So each is handled explicitly below, and the distance to
the grid point travels with the data.

Needs ``eccodes`` to decode, which is why this is an optional extra:

    pip install "cn-weather-cube[opendata]"
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..schema import VARIABLES, UnitMismatch

USER_AGENT = "cn-weather-cube/0.1 (research)"
RAW_CACHE = Path(
    os.environ.get("CN_WEATHER_CUBE_CACHE", Path.home() / ".cache" / "cn-weather-cube")
) / "opendata-messages"

# GRIB spells its units its own way.  Normalising before the check means the
# guard catches a real unit swap and not a typographic one.
UNIT_ALIASES = {"m s**-1": "m/s", "J m**-2": "J/m^2", "K": "K", "W m**-2": "W/m^2"}


@dataclass(frozen=True)
class Model:
    """One publisher's layout, which is all that differs between them."""

    name: str
    label: str
    bucket: str
    index_kind: str            # "ecmwf" (json lines) or "noaa" (colon-delimited text)
    cycles: tuple[int, ...]    # run hours published
    #: cube variable -> the GRIB fields it is built from, in order
    fields: dict[str, tuple[str, ...]]

    def paths(self, run: pd.Timestamp, step: int) -> tuple[str, str]:
        raise NotImplementedError


@dataclass(frozen=True)
class Ecmwf(Model):
    stream: str = "oper"
    directory: str = "ifs"

    def paths(self, run: pd.Timestamp, step: int) -> tuple[str, str]:
        day = run.strftime("%Y%m%d")
        stem = (f"{day}/{run.hour:02d}z/{self.directory}/0p25/{self.stream}/"
                f"{day}{run.hour:02d}0000-{step}h-{self.stream}-fc")
        return stem + ".grib2", stem + ".index"


@dataclass(frozen=True)
class Noaa(Model):
    def paths(self, run: pd.Timestamp, step: int) -> tuple[str, str]:
        day = run.strftime("%Y%m%d")
        stem = f"gfs.{day}/{run.hour:02d}/atmos/gfs.t{run.hour:02d}z.pgrb2.0p25.f{step:03d}"
        return stem, stem + ".idx"


_ECMWF_FIELDS = {
    "ws10": ("10u", "10v"), "wd10": ("10u", "10v"),
    "ws100": ("100u", "100v"), "wd100": ("100u", "100v"),
    "t2m": ("2t",),
    # Accumulated since the start of the forecast; see ``series``.
    "ghi": ("ssrd",),
}

MODELS: dict[str, Model] = {
    "ifs": Ecmwf("ifs", "ECMWF IFS 0.25°", "https://ecmwf-forecasts.s3.amazonaws.com/",
                 "ecmwf", (0, 6, 12, 18), _ECMWF_FIELDS),
    "aifs": Ecmwf("aifs", "ECMWF AIFS 0.25° (machine-learned)",
                  "https://ecmwf-forecasts.s3.amazonaws.com/", "ecmwf", (0, 6, 12, 18),
                  _ECMWF_FIELDS, directory="aifs-single"),
    "gfs": Noaa("gfs", "NOAA GFS 0.25°", "https://noaa-gfs-bdp-pds.s3.amazonaws.com/",
                "noaa", (0, 6, 12, 18),
                {"ws10": ("UGRD:10 m above ground", "VGRD:10 m above ground"),
                 "wd10": ("UGRD:10 m above ground", "VGRD:10 m above ground"),
                 "ws100": ("UGRD:100 m above ground", "VGRD:100 m above ground"),
                 "wd100": ("UGRD:100 m above ground", "VGRD:100 m above ground"),
                 "t2m": ("TMP:2 m above ground",)}),
}


def _get(url: str, headers: dict | None = None, retries: int = 3, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (403, 404):      # not published (yet, or ever)
                raise
            last = error
        except Exception as error:            # noqa: BLE001 - network, retried
            last = error
    raise RuntimeError(f"failed to fetch {url}: {last}")


def index(model: str, run: pd.Timestamp, step: int) -> list[dict]:
    """Every message in one file, with the bytes it occupies.

    Two formats.  ECMWF writes one JSON object per message carrying ``_offset``
    and ``_length``; NOAA writes a colon-delimited line carrying only the start,
    so the length is the next message's start and the last one runs to the end
    of the file - encoded here as ``None``, which the range request reads as
    "to the end".
    """
    spec = MODELS[model]
    _, index_path = spec.paths(run, step)
    return parse_index(_get(spec.bucket + index_path).decode("utf-8", "replace"), spec.index_kind)


def parse_index(raw: str, kind: str) -> list[dict]:
    """The two sidecar formats, kept separate from the fetching so they can be tested."""
    out = []
    if kind == "ecmwf":
        for line in raw.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            out.append({"select": entry["param"], "raw": entry,
                        "offset": int(entry["_offset"]), "length": int(entry["_length"])})
        return out

    rows = [line.split(":") for line in raw.splitlines() if line.strip()]
    for i, row in enumerate(rows):
        start = int(row[1])
        end = int(rows[i + 1][1]) if i + 1 < len(rows) else None
        out.append({"select": f"{row[3]}:{row[4]}", "raw": row,
                    "offset": start, "length": None if end is None else end - start})
    return out


def _cache_path(model: str, run: pd.Timestamp, step: int, select: str) -> Path:
    tag = hashlib.sha1(f"{model}|{run.isoformat()}|{step}|{select}".encode()).hexdigest()[:16]
    return RAW_CACHE / model / f"{tag}.grib2"


def message(model: str, run: pd.Timestamp, step: int, select: str,
            entries: list[dict] | None = None) -> bytes:
    """One GRIB message, fetched by byte range and kept.

    The cache is on the message rather than on the point, because the expensive
    part is the transfer and a second site in the same field costs nothing.
    """
    path = _cache_path(model, run, step, select)
    if path.exists():
        return path.read_bytes()

    spec = MODELS[model]
    entries = entries if entries is not None else index(model, run, step)
    matches = [e for e in entries if e["select"] == select]
    if not matches:
        raise KeyError(f"{model} {run:%Y-%m-%d %Hz} +{step}h has no message {select!r}")
    entry = matches[0]
    end = "" if entry["length"] is None else str(entry["offset"] + entry["length"] - 1)
    data_path, _ = spec.paths(run, step)
    body = _get(spec.bucket + data_path, {"Range": f"bytes={entry['offset']}-{end}"})
    if not body.startswith(b"GRIB"):
        raise RuntimeError(
            f"{model} {select}: the bytes at the offset the index gave are not a GRIB "
            f"message. The index and the file have gone out of step; do not decode this."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body


def _read_point(body: bytes, lat: float, lon: float) -> tuple[float, str, float]:
    """Value at the nearest grid point, its unit as the file states it, and the distance."""
    import eccodes

    handle = eccodes.codes_new_from_message(body)
    try:
        unit = eccodes.codes_get(handle, "units")
        near = eccodes.codes_grib_find_nearest(handle, lat, lon)[0]
        return float(near.value), unit, float(near.distance)
    finally:
        eccodes.codes_release(handle)


def series(model: str, run: str | pd.Timestamp, variable: str, lat: float, lon: float,
           steps: tuple[int, ...] = (24,), max_distance_km: float = 40.0) -> pd.DataFrame:
    """One variable at one point, at stated leads, from one model run.

    The lead is not inferred and cannot drift: it is in the file's name. That is
    the property a verification study needs and the thing a "latest available"
    archive cannot give.

    Irradiance is the case where the file's own semantics have to be undone.
    ECMWF publishes ``ssrd`` accumulated in joules since the start of the
    forecast, so the flux over a period is the difference between two steps
    divided by the seconds between them. Taking the raw value as a rate is a
    factor-of-3600-and-rising error, and it is the kind that looks plausible at
    step 1 and absurd only at step 100.
    """
    spec = MODELS[model]
    if variable not in spec.fields:
        raise KeyError(
            f"{spec.label} does not publish {variable} in this file set; "
            f"it has {sorted(spec.fields)}"
        )
    run = pd.Timestamp(run, tz=None)
    if run.hour not in spec.cycles:
        raise ValueError(f"{spec.label} runs at {spec.cycles}, not {run.hour:02d}z")

    accumulated = variable == "ghi"
    rows = []
    for step in steps:
        needed = [step] + ([step - _previous_gap(steps, step)] if accumulated else [])
        values = {}
        for at_step in needed:
            parts = []
            entries = index(model, run, at_step)
            for selector in spec.fields[variable]:
                body = message(model, run, at_step, selector, entries)
                value, unit, distance = _read_point(body, lat, lon)
                if distance > max_distance_km:
                    raise RuntimeError(
                        f"nearest grid point is {distance:.0f} km from ({lat}, {lon}); "
                        f"refusing rather than passing off a distant cell as this site"
                    )
                parts.append((value, UNIT_ALIASES.get(unit, unit), distance))
            values[at_step] = parts

        parts = values[step]
        unit = parts[0][1]
        distance = parts[0][2]
        if variable.startswith("ws"):
            value, unit = float(np.hypot(parts[0][0], parts[1][0])), "m/s"
        elif variable.startswith("wd"):
            u, v = parts[0][0], parts[1][0]
            value, unit = float((270.0 - np.degrees(np.arctan2(v, u))) % 360.0), "deg"
        elif variable == "t2m":
            if unit != "K":
                raise UnitMismatch(f"t2m: expected K from the file, got {unit!r}")
            value, unit = parts[0][0] - 273.15, "degC"
        elif accumulated:
            earlier = values[needed[1]][0][0]
            seconds = (step - needed[1]) * 3600
            value, unit = (parts[0][0] - earlier) / seconds, "W/m^2"
        else:
            value = parts[0][0]

        expected = VARIABLES[variable].unit
        if unit not in VARIABLES[variable].accepted_units and unit != expected:
            raise UnitMismatch(
                f"{variable}: this package works in {expected}; the file gave {unit!r} "
                f"and no conversion is defined for it."
            )
        rows.append({"run": run, "step_hours": step,
                     "valid_time": run + pd.Timedelta(hours=step),
                     "value": value, "unit": expected,
                     "grid_distance_km": distance, "model": spec.label})
    return pd.DataFrame(rows)


def _previous_gap(steps: tuple[int, ...], step: int) -> int:
    """How far back the accumulation has to be differenced against.

    The step before this one in the requested list, or the publication interval
    when this is the first - never zero, which would divide by nothing.
    """
    earlier = [s for s in sorted(steps) if s < step]
    if earlier:
        return step - earlier[-1]
    return 3 if step <= 90 else 6
