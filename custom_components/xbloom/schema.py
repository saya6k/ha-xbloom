"""YAML / options recipe schemas.

Lifted out of ``__init__.py`` so ``config_flow.py``'s OptionsFlow can
validate recipes coming from the UI without re-importing the package
root (which would create a circular import during config-flow setup).
"""
from __future__ import annotations

import voluptuous as vol

import homeassistant.helpers.config_validation as cv

_PATTERN_NAME_TO_INT = {"center": 0, "circular": 1, "spiral": 2}


def _coerce_pour_pattern(value):
    """Accept either the int (0/1/2) or the name (center/circular/spiral)."""
    if isinstance(value, bool):
        raise vol.Invalid(f"pattern must be a string or int (got {value!r})")
    if isinstance(value, int):
        if value in (0, 1, 2):
            return value
        raise vol.Invalid(f"pattern int must be 0, 1, or 2 (got {value})")
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _PATTERN_NAME_TO_INT:
            return _PATTERN_NAME_TO_INT[key]
        raise vol.Invalid(
            f"pattern must be one of {list(_PATTERN_NAME_TO_INT)} (got {value!r})"
        )
    raise vol.Invalid(f"pattern must be a string or int (got {type(value).__name__})")


POUR_SCHEMA = vol.Schema(
    {
        vol.Required("volume"): cv.positive_int,
        vol.Required("temperature"): cv.positive_int,
        vol.Optional("flow_rate", default=3.0): vol.Coerce(float),
        vol.Optional("pausing", default=0): vol.Coerce(int),
        vol.Optional("pattern", default=2): _coerce_pour_pattern,
        vol.Optional("vibration", default="none"): vol.In(
            ["none", "before", "after", "both"]
        ),
    }
)

RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("grind_size", default=50): vol.Coerce(int),
        vol.Optional("rpm", default=80): vol.Coerce(int),
        vol.Optional("bean_weight", default=15.0): vol.Coerce(float),
        vol.Optional("total_water", default=250): vol.Coerce(int),
        vol.Optional("cup_type", default="omni_dripper"): cv.string,
        vol.Optional("bypass_volume", default=0): vol.Coerce(float),
        vol.Optional("bypass_temperature", default=0): vol.Coerce(float),
        vol.Required("pours"): [POUR_SCHEMA],
    }
)
