"""XBloom Studio (J15) — the Bluetooth LE machine family.

Sets up one Studio config entry: the BLE coordinator, the main device and
the Studio entity platforms. Features shared by every model (services, LLM
tools, the sidebar panel, the recipe store schema) live outside ``models/``.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ...const import (
    CONF_ACCOUNT_RECIPES_SEEDED,
    CONF_EASY_SLOTS,
    CONF_EMAIL,
    CONF_MAC_ADDRESS,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_RECIPES,
    CONF_RECIPES_SEEDED,
    CONF_SESSION_TIMEOUT,
    CONF_STORE_REGION,
    CONF_TELEMETRY_INTERVAL,
    CONF_TEMP_UNIT,
    CONF_WATER_SOURCE,
    CONF_WEIGHT_UNIT,
    DATA_COORDINATOR,
    DEFAULT_MODE,
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_TELEMETRY_INTERVAL,
    DEFAULT_TEMP_UNIT,
    DEFAULT_WATER_SOURCE,
    DEFAULT_WEIGHT_UNIT,
    DOMAIN,
)
from ...pods import PodLibrary

# The entity platform modules, found by models.async_setup_platform_entry.
from . import (  # noqa: F401
    binary_sensor,
    button,
    event,
    number,
    select,
    sensor,
    switch,
    update,
)
from .coordinator import XBloomCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up XBloom from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    mac = entry.data[CONF_MAC_ADDRESS]
    telemetry_interval = entry.options.get(
        CONF_TELEMETRY_INTERVAL,
        entry.data.get(CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL),
    )
    # water_source, mode, and the display units are stored in options so
    # they survive HA restarts.  Falls back to sensible defaults if never set.
    initial_water_source = entry.options.get(CONF_WATER_SOURCE, DEFAULT_WATER_SOURCE)
    initial_mode = entry.options.get(CONF_MODE, DEFAULT_MODE)
    initial_weight_unit = entry.options.get(CONF_WEIGHT_UNIT, DEFAULT_WEIGHT_UNIT)
    initial_temp_unit = entry.options.get(CONF_TEMP_UNIT, DEFAULT_TEMP_UNIT)
    # Idle disconnect timeout — how long the machine may sit doing nothing
    # before the BLE link is dropped until the next action (0 = never drop).
    # Collected by the config flow since the first release but not acted on
    # until 2026-07-19; see the coordinator's _session_timeout.
    session_timeout = entry.options.get(
        CONF_SESSION_TIMEOUT,
        entry.data.get(CONF_SESSION_TIMEOUT, DEFAULT_SESSION_TIMEOUT),
    )

    coordinator = XBloomCoordinator(
        hass=hass,
        mac_address=mac,
        entry_id=entry.entry_id,
        update_interval=telemetry_interval,
        initial_water_source=initial_water_source,
        initial_mode=initial_mode,
        initial_weight_unit=initial_weight_unit,
        initial_temp_unit=initial_temp_unit,
        session_timeout=session_timeout,
        cloud_email=entry.data.get(CONF_EMAIL),
        cloud_password=entry.data.get(CONF_PASSWORD),
    )

    # Recipe merge order — lowest precedence first:
    #   1. YAML (configuration.yaml xbloom: recipes:)
    #   2. The local store (entry.options[CONF_RECIPES]) — source of truth.
    # On a fresh install the store is empty, so seed it synchronously with
    # the bundled default_recipes.py list (no network) — the one-time
    # cloud seed (a background task, kicked off below) adds the account's
    # own or XBloom's official recipes on top.
    coordinator.seed_bundled_recipes()
    coordinator._rebuild_recipes()
    await coordinator.pod_library.async_load()

    # Initial data fetch (non-blocking; device may not be connected yet)
    await coordinator.async_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        # Snapshot for _async_update_listener's recipe-only-change check.
        "options_snapshot": dict(entry.options),
    }
    # coordinator.seed_bundled_recipes() + _rebuild_recipes() above ran
    # before this entry was registered in hass.data, so the recipe-service
    # dropdown refresh they triggered couldn't see this machine's own
    # recipes yet (only any other already-configured machine's). Refresh
    # once more now that self-lookup works.
    await coordinator._async_refresh_recipe_service_schemas()

    # Register the main device explicitly, before any platform is set up.
    # async_forward_entry_setups fans the platforms out concurrently, so
    # relying on whichever entity happens to reference the main device
    # first is a race: if a platform whose entities all point at a
    # child device (grinder/scale/brewer) needs the parent's registry id when
    # its entity is added. Explicit registration removes that ordering race.
    main_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="xBloom Coffee Machine",
        manufacturer="xBloom",
    )
    coordinator.parent_device_id = main_device.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # React to options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # One-time cloud seed of the local recipe store (account recipes if an
    # account is configured, else XBloom's official public recipes) — run
    # in the background so a slow/unreachable cloud API can't delay
    # integration setup. No-ops once the seed flags are set; there is no
    # periodic re-sync anymore (the local store is the source of truth).
    hass.async_create_task(coordinator.async_seed_recipes())

    return True


# Options keys that only affect the recipe store — writes touching nothing
# else (seed task, import/create/edit/delete services) must not bounce the
# BLE connection with a full reload; a recipe rebuild is enough.
_RECIPE_ONLY_OPTION_KEYS = {
    CONF_RECIPES,
    CONF_RECIPES_SEEDED,
    CONF_ACCOUNT_RECIPES_SEEDED,
    CONF_EASY_SLOTS,
}

# CONF_MODE is persisted by XBloomCoordinator.async_set_mode() purely so the
# preference survives restarts/reconnects — the BLE mode-switch command has
# already been sent over the air by that point. Live-observed 2026-07-04: a
# full reload here disconnects BLE (async_unload_entry -> async_disconnect())
# and nothing reconnects automatically afterwards, so the connection switch
# was left stuck "off" on every mode change. No BLE-affecting work is needed
# for this key, so it's exempt from the reload just like the recipe keys.
# The unit/water-source keys are exempt for the same reason (they used to
# reload — and therefore drop the BLE connection — on every water-source
# select change); their only follow-up is pushing the new values to the
# machine, which coordinator._handle_unit_options_change() does in place.
_UNIT_OPTION_KEYS = {CONF_WATER_SOURCE, CONF_WEIGHT_UNIT, CONF_TEMP_UNIT}
# CONF_STORE_REGION only picks which web store the panel's links open.
_NO_RELOAD_OPTION_KEYS = (
    _RECIPE_ONLY_OPTION_KEYS | {CONF_MODE, CONF_STORE_REGION} | _UNIT_OPTION_KEYS
)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change.

    Exception: changes confined to ``_NO_RELOAD_OPTION_KEYS`` (the recipe
    store, the mode preference, and the unit/water-source preferences)
    skip the reload — a recipe rebuild / an in-place unit push is enough —
    so these updates apply without dropping the BLE connection.
    """
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data and DATA_COORDINATOR in data:
        prev = data.get("options_snapshot") or {}
        cur = dict(entry.options)
        changed = {k for k in set(prev) | set(cur) if prev.get(k) != cur.get(k)}
        data["options_snapshot"] = cur
        if changed and changed <= _NO_RELOAD_OPTION_KEYS:
            coordinator: XBloomCoordinator = data[DATA_COORDINATOR]
            if changed & _RECIPE_ONLY_OPTION_KEYS:
                coordinator._rebuild_recipes()
            if changed & _UNIT_OPTION_KEYS:
                coordinator._handle_unit_options_change(cur)
            coordinator.async_update_listeners()
            return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and disconnect BLE."""
    coordinator: XBloomCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_disconnect()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The machine was removed — drop its pod library store."""
    await PodLibrary(hass, entry.entry_id).async_remove()
