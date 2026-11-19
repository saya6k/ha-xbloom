"""XBloom Coffee Machine integration for Home Assistant.

One domain, two machine families, each in its own package:

* ``j15`` — Studio (J15): Bluetooth LE, recipes, services, panel, LLM tools.
* ``j20`` — Original (J20): Wi-Fi, driven through the xBloom cloud only.

This module only routes each config entry to its family. The two never
share runtime state: Studio keeps its coordinators in ``hass.data[DOMAIN]``
(which its services, panel, and LLM tools iterate), and Original keeps its
coordinator in ``entry.runtime_data``.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import j15, j20
from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_ORIGINAL

# configuration.yaml's ``xbloom: recipes:`` block and the Studio services are
# integration-wide, so the Studio package also owns ``async_setup``.
from .j15 import CONFIG_SCHEMA as CONFIG_SCHEMA
from .j15 import async_setup as async_setup


def _is_original(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_ORIGINAL


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Studio or Original config entry."""
    if _is_original(entry):
        return await j20.async_setup_entry(hass, entry)
    return await j15.async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a config entry; Original entries were born at the current version."""
    if _is_original(entry):
        return True
    return await j15.async_migrate_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Studio or Original config entry."""
    if _is_original(entry):
        return await j20.async_unload_entry(hass, entry)
    return await j15.async_unload_entry(hass, entry)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up stores owned by a removed entry (Studio only has any)."""
    if not _is_original(entry):
        await j15.async_remove_entry(hass, entry)
