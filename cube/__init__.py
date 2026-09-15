"""cn-weather-cube: one call for weather at a point, with its units attached.

Point weather for renewable-energy work, without downloading anything. The
difference from a thin wrapper around an API is that every quantity here carries
its unit and every source declares what it is: a reanalysis is labelled a
reanalysis, a fixed-lead forecast is labelled with its lead, and a request whose
units do not match raises instead of returning a plausible wrong number.
"""
from .api import fetch, choose_source
from .schema import VARIABLES, UnitMismatch, resolve

__all__ = ["fetch", "choose_source", "VARIABLES", "UnitMismatch", "resolve"]
__version__ = "0.1.0"
