"""XBloom Original (J20) — status only, through xBloom's cloud.

A second config-entry type (``device_type == "original"``) in the same
domain. Nothing here imports ``j15`` or touches the BLE stack. The
coordinator registers in ``hass.data[DOMAIN]`` like every model, so the
panel and the LLM status tool reach it; it declares no capabilities, so
nothing shared can send it a command.

The Original has no local API; the integration reads what the official app
reads: the thing shadow (``POST /api/device/thing_shadow/``, polled) and its
live deltas (the app's WebSocket). It sends the machine nothing. Controls
(grind, pour, recipes, auto mode, Wi-Fi setup) wait on the repository's
``original`` branch until an Original owner has confirmed them on real
hardware.
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

# The entity platform modules, found by models.async_setup_platform_entry.
from . import binary_sensor, sensor  # noqa: F401
from .client import J20ApiError, J20CloudClient
from .coordinator import J20Coordinator

_LOGGER = logging.getLogger(__name__)

J20_PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


type J20ConfigEntry = ConfigEntry[J20Coordinator]

# account (lower-cased email) -> (shared client, entry ids using it). Several
# Originals on one account share one session: a second login could
# invalidate the first entry's token, and they'd keep logging each other out.
_CLIENTS_KEY = f"{DOMAIN}_j20_clients"


def _account(entry: ConfigEntry) -> str:
    return str(entry.data.get(CONF_EMAIL, "")).strip().lower()


def _release_client(hass: HomeAssistant, entry: ConfigEntry) -> None:
    clients = hass.data.get(_CLIENTS_KEY, {})
    shared = clients.get(_account(entry))
    if shared is not None:
        shared[1].discard(entry.entry_id)
        if not shared[1]:
            clients.pop(_account(entry), None)


async def async_setup_entry(hass: HomeAssistant, entry: J20ConfigEntry) -> bool:
    clients = hass.data.setdefault(_CLIENTS_KEY, {})
    shared = clients.get(_account(entry))
    if shared is None:
        shared = clients[_account(entry)] = (
            J20CloudClient(async_get_clientsession(hass)),
            set(),
        )
    client, users = shared
    users.add(entry.entry_id)
    if not client.logged_in:
        try:
            logged_in = await client.async_login(
                entry.data.get(CONF_EMAIL, ""), entry.data.get(CONF_PASSWORD, "")
            )
        except J20ApiError as exc:
            _release_client(hass, entry)
            raise ConfigEntryNotReady(f"xBloom cloud unreachable: {exc}") from exc
        if not logged_in:
            _release_client(hass, entry)
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
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        _release_client(hass, entry)
        raise

    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_COORDINATOR: coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, J20_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_j20_listener))
    # Live shadow deltas over the app's WebSocket; cancelled on unload.
    entry.async_create_background_task(
        hass, coordinator.async_run_live_updates(), "xb_original_live_updates"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: J20ConfigEntry) -> bool:
    await entry.runtime_data.async_shutdown()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, J20_PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        _release_client(hass, entry)
    return unload_ok


async def _async_update_j20_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
