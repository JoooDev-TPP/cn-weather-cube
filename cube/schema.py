"""One name and one unit per quantity, declared rather than assumed.

Every bug that cost this project a day was a unit or a name that looked right.
Open-Meteo returns wind in km/h unless told otherwise and METAR reports it in
knots, so comparing them produces a forecast 3.6 or 1.9 times too fast and a
scorecard that still looks like a scorecard. SMARD serves energy per quarter
hour, not power, so German midday photovoltaics reads twelve gigawatts instead
of fifty and nothing about it looks wrong. A column named ``rmse_c`` for
"celsius" ends up holding Wh/m² once a third variable arrives.

None of those raise. They produce numbers, and the numbers are wrong by a factor
that is plausible enough to survive a glance. So in this package a variable is
not a string: it is a name, a unit, and what to do when a source offers
something else.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variable:
    """A quantity, its canonical unit, and how to ask a source for it."""

    name: str
    unit: str
    description: str
    #: What a source must return for this variable to be accepted as-is.
    #: A source that returns anything else is converted explicitly or refused;
    #: it is never silently taken at face value.
    accepted_units: tuple[str, ...]
    #: Whether the quantity is an instantaneous rate (averaged when resampled)
    #: or an accumulation (summed).  Getting this backwards turns a daily mean
    #: irradiance into something 24 times too small, or a daily total into
    #: something 24 times too large.
    accumulates: bool = False


VARIABLES: dict[str, Variable] = {
    "ghi": Variable(
        "ghi", "W/m^2", "global horizontal irradiance",
        ("W/m²", "W/m^2", "w/m2"),
    ),
    "dni": Variable(
        "dni", "W/m^2", "direct normal irradiance", ("W/m²", "W/m^2", "w/m2"),
    ),
    "dhi": Variable(
        "dhi", "W/m^2", "diffuse horizontal irradiance", ("W/m²", "W/m^2", "w/m2"),
    ),
    "t2m": Variable("t2m", "degC", "air temperature at 2 m", ("°C", "degC", "C")),
    "d2m": Variable("d2m", "degC", "dewpoint at 2 m", ("°C", "degC", "C")),
    "rh": Variable("rh", "percent", "relative humidity", ("%", "percent")),
    "sp": Variable("sp", "hPa", "surface pressure", ("hPa", "mb", "millibar")),
    "ws10": Variable("ws10", "m/s", "wind speed at 10 m", ("m/s", "ms")),
    "wd10": Variable("wd10", "deg", "wind direction at 10 m", ("°", "deg")),
    "ws100": Variable("ws100", "m/s", "wind speed at 100 m", ("m/s", "ms")),
    "wd100": Variable("wd100", "deg", "wind direction at 100 m", ("°", "deg")),
    "cloud": Variable("cloud", "percent", "total cloud cover", ("%", "percent")),
    "precip": Variable(
        "precip", "mm", "precipitation", ("mm", "millimeter"), accumulates=True,
    ),
}


class UnitMismatch(RuntimeError):
    """A source returned a unit this package will not guess at.

    Deliberately not a warning. A warning in a notebook scrolls off the top and
    the analysis continues with wrong numbers; this stops it.
    """


def check_unit(variable: str, returned: str) -> None:
    """Confirm a source gave what it was asked for, or refuse to continue."""
    spec = VARIABLES[variable]
    if returned not in spec.accepted_units:
        raise UnitMismatch(
            f"{variable}: asked for {spec.unit}, source returned {returned!r}. "
            f"Refusing to continue — a silent unit mismatch here is a factor-of-"
            f"{'3.6' if 'm/s' in spec.unit else 'unknown'} error that still looks "
            f"like a plausible number."
        )


def resolve(names: list[str] | str) -> list[Variable]:
    """Turn user-supplied names into specifications, refusing unknown ones."""
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in names if n not in VARIABLES]
    if unknown:
        raise KeyError(
            f"unknown variable(s) {unknown}; known: {sorted(VARIABLES)}"
        )
    return [VARIABLES[n] for n in names]
