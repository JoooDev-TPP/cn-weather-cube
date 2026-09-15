"""Turning weather into the quantities a renewable plant actually responds to.

A panel does not respond to global horizontal irradiance; it responds to what
lands on its own tilted plane. A turbine does not respond to 10 m wind; it
responds to the wind at its hub, through air of a particular density, and to the
cube of it. These conversions are where a weather series becomes a power input,
and each of them is a place to be wrong quietly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Sea-level standard atmosphere, used only for the density correction.
RHO_STANDARD = 1.225  # kg/m^3
R_DRY = 287.058  # J/(kg K)


def plane_of_array(
    frame: pd.DataFrame,
    latitude: float,
    longitude: float,
    tilt: float,
    azimuth: float = 180.0,
    albedo: float = 0.2,
    ghi: str = "ghi",
    dni: str | None = None,
    dhi: str | None = None,
) -> pd.Series:
    """Irradiance on a tilted plane, W/m².

    Where only global horizontal is available the beam and diffuse components
    are separated with the Erbs correlation. That is a correlation, not a
    measurement: it is fitted to mid-latitude data and carries several per cent
    of error on hourly values. A source that publishes direct and diffuse
    directly should be used instead, and this function will if given them.
    """
    import pvlib

    site = pvlib.location.Location(latitude, longitude, tz="UTC")
    solar = site.get_solarposition(frame.index)

    if dni and dhi and dni in frame and dhi in frame:
        beam, diffuse = frame[dni], frame[dhi]
    else:
        decomposed = pvlib.irradiance.erbs(
            frame[ghi], solar["zenith"], frame.index
        )
        beam, diffuse = decomposed["dni"], decomposed["dhi"]

    total = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solar["apparent_zenith"],
        solar_azimuth=solar["azimuth"],
        dni=beam,
        ghi=frame[ghi],
        dhi=diffuse,
        albedo=albedo,
    )
    return total["poa_global"].rename("poa")


def hub_wind(
    frame: pd.DataFrame,
    hub_height: float,
    lower: str = "ws10",
    lower_height: float = 10.0,
    upper: str | None = "ws100",
    upper_height: float = 100.0,
    default_alpha: float = 0.143,
) -> pd.Series:
    """Wind speed at hub height, m/s, by power law.

    With two levels the shear exponent is fitted from the data at each time
    step, which is the honest thing to do: shear varies by a factor of three
    between a stable night and a convective afternoon, and a single exponent
    applied to both is a large error in opposite directions.

    With one level the one-seventh power law is used, and that is an assumption,
    not a result. It is stated in the returned series' name so it travels with
    the numbers.
    """
    below = frame[lower].clip(lower=0.01)
    if upper and upper in frame:
        above = frame[upper].clip(lower=0.01)
        # alpha from the two levels: ln(v2/v1) / ln(z2/z1)
        alpha = np.log(above / below) / np.log(upper_height / lower_height)
        # Physical shear runs from roughly zero to 0.6; values outside that come
        # from near-calm hours where the ratio is noise, and are replaced rather
        # than allowed to drive a cubed term.
        alpha = alpha.where((alpha > -0.1) & (alpha < 0.8), default_alpha)
        name = "ws_hub"
    else:
        alpha = pd.Series(default_alpha, index=frame.index)
        name = f"ws_hub_assumed_alpha_{default_alpha}"
    return (below * (hub_height / lower_height) ** alpha).rename(name)


def air_density(
    frame: pd.DataFrame, temperature: str = "t2m", pressure: str | None = "sp"
) -> pd.Series:
    """Air density, kg/m³, from the ideal gas law.

    Turbine power is proportional to it. Cold dense winter air on the Inner
    Mongolian plateau and warm thin summer air differ by around ten per cent,
    which is ten per cent of output at the same wind speed.
    """
    kelvin = frame[temperature] + 273.15
    if pressure and pressure in frame:
        pascals = frame[pressure] * 100.0
        return (pascals / (R_DRY * kelvin)).rename("rho")
    # No pressure: return the temperature part only, and say so in the name.
    return (RHO_STANDARD * 288.15 / kelvin).rename("rho_no_pressure")


def clearsky_index(
    frame: pd.DataFrame, latitude: float, longitude: float, ghi: str = "ghi"
) -> pd.Series:
    """Irradiance as a fraction of what a cloudless sky would deliver.

    Below twenty watts of clear-sky the ratio is noise over noise and the sun is
    driving nothing, so it is set to zero rather than allowed to blow up.
    """
    import pvlib

    site = pvlib.location.Location(latitude, longitude, tz="UTC")
    clear = site.get_clearsky(frame.index, model="haurwitz")["ghi"]
    return pd.Series(
        np.where(clear > 20, frame[ghi] / clear.clip(lower=20), 0.0),
        index=frame.index,
        name="kt",
    )
