"""Binary sensor platform — routes the entry to its machine family."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_ORIGINAL
from .models.j15.binary_sensor import async_setup_entry as _async_setup_j15
from .models.j20.binary_sensor import async_setup_entry as _async_setup_j20


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_ORIGINAL:
        await _async_setup_j20(hass, entry, async_add_entities)
    else:
        await _async_setup_j15(hass, entry, async_add_entities)
