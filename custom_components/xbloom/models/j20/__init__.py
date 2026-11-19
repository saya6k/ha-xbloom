"""XBloom Original (J20) — cloud-only support.

This package is wired into the existing ``xbloom`` integration as a second
config-entry type (``device_type == "original"``) so HACS keeps shipping
one domain. Nothing here imports ``j15`` or touches the BLE stack: Studio
and Original are separate coordinators and separate entity sets. The
coordinator registers in ``hass.data[DOMAIN]`` like every model, so the
shared LLM tools and panel reach it — limited to its ``capabilities``
(manual grind/pour, stop, calibrate; no recipes, so no services).

The Original has no local control path. The integration is a client of
xBloom's cloud, exactly like the official app:

* read: ``POST /api/device/thing_shadow/`` (polled)
* write: ``POST /api/device/command`` with a ``desired`` shadow document

The app's WebSocket is intentionally not used in v1 — polling covers the
state HA needs, and the subscribe/auth handshake is undocumented.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TELEMETRY_INTERVAL,
    DATA_COORDINATOR,
    DEFAULT_J20_SCAN_INTERVAL,
    DOMAIN,
)
from .client import J20ApiError, J20CloudClient
from .coordinator import J20Coordinator

_LOGGER = logging.getLogger(__name__)

J20_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


type J20ConfigEntry = ConfigEntry[J20Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: J20ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = J20CloudClient(session)
    try:
        logged_in = await client.async_login(
            entry.data.get(CONF_EMAIL, ""), entry.data.get(CONF_PASSWORD, "")
        )
    except J20ApiError as exc:
        raise ConfigEntryNotReady(f"xBloom cloud unreachable: {exc}") from exc
    if not logged_in:
        raise ConfigEntryAuthFailed("XBloom Original login failed")

    coordinator = J20Coordinator(
        hass,
        entry,
        client,
        entry.options.get(
            CONF_TELEMETRY_INTERVAL,
            entry.data.get(CONF_TELEMETRY_INTERVAL, DEFAULT_J20_SCAN_INTERVAL),
        ),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_COORDINATOR: coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, J20_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_j20_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: J20ConfigEntry) -> bool:
    await entry.runtime_data.async_shutdown()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, J20_PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def _async_update_j20_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
