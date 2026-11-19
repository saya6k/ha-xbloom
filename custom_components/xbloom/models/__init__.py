"""One package per xBloom machine model; see the integration's ``__init__``.

The only place a config entry is mapped to its model. Each model package
imports its own entity platform modules, so a platform is found as an
attribute of the model (``model.sensor``); a model without that platform
never forwards it.
"""
from __future__ import annotations

from types import ModuleType

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import CONF_DEVICE_TYPE, DEVICE_TYPE_ORIGINAL, DEVICE_TYPE_STUDIO
from . import j15, j20

# device_type -> model package. Entries created before device_type existed
# are Studios.
MODELS: dict[str, ModuleType] = {
    DEVICE_TYPE_STUDIO: j15,
    DEVICE_TYPE_ORIGINAL: j20,
}


def model_for(entry: ConfigEntry) -> ModuleType:
    """The model package that owns a config entry."""
    return MODELS[entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_STUDIO)]


async def async_setup_platform_entry(
    platform: Platform,
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one entity platform with the entry's model."""
    await getattr(model_for(entry), platform.value).async_setup_entry(
        hass, entry, async_add_entities
    )
