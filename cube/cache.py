"""A local cache keyed on everything that could change the answer.

The failure this design is aimed at: a cached file that no longer matches the
request that would produce it. Add a model to the list, change the lead time,
switch the satellite product — and a key built from the site and the dates alone
serves the old file, so the change appears to have had no effect.

The key is therefore a hash of the whole request, including the source's name
and version. Anything that alters the data alters the key.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path(
    os.environ.get("CN_WEATHER_CUBE_CACHE", Path.home() / ".cache" / "cn-weather-cube")
)


def key(request: dict) -> str:
    """A stable hash of a request. Dict ordering must not change the key."""
    payload = json.dumps(request, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def path(request: dict, root: Path | None = None) -> Path:
    root = root or DEFAULT_ROOT
    source = str(request.get("source", "unknown"))
    return root / source / f"{key(request)}.parquet"


def load(request: dict, root: Path | None = None) -> pd.DataFrame | None:
    target = path(request, root)
    return pd.read_parquet(target) if target.exists() else None


def store(request: dict, frame: pd.DataFrame, root: Path | None = None) -> Path:
    target = path(request, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    # The request is written beside the data so a cache directory can be read
    # by a human six months later without reverse-engineering the hash.
    target.with_suffix(".json").write_text(
        json.dumps(request, indent=2, sort_keys=True, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    frame.to_parquet(target)
    return target
