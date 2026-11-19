"""XBloom Coffee Machine integration for Home Assistant.

Layout:

* ``models/<model>/`` — one package per machine model (transport,
  coordinator, entities). ``j15`` is the Studio (Bluetooth LE), ``j20`` the
  Original (Wi-Fi, xBloom cloud only).
* Shared features that work on any model — ``services``, ``llm``,
  ``panel``, ``recipes``, ``cloud`` — never import ``models``.

This module loads the integration-wide pieces once (``async_setup``) and
routes each config entry to its model package.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_LEGACY_ENTRY_ID, DOMAIN
from .legacy import (
    async_adopt,
    async_flag_restart,
    async_legacy_installed,
    async_restore_children,
)
from .llm_api import register_llm_api
from .machine import Capability, get_machine
from .migrations import async_migrate_entry as async_migrate_entry
from .models import model_for as _model
from .panel import async_register_panel
from .recipes import CONFIG_SCHEMA as CONFIG_SCHEMA
from .recipes import async_load_recipes
from .services import async_register_services, async_unload_services


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Load the bundled + YAML recipe layers and register the sidebar panel.

    While the pre-3.0 integration (domain ``xbloom``) is still running it
    owns the panel URL; this one waits for the restart — see legacy.py.
    """
    async_load_recipes(hass, config)
    if await async_legacy_installed(hass):
        async_flag_restart(hass)
    else:
        # Sidebar panel (global — it lists every configured machine).
        await async_register_panel(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one machine with its model package, then the shared features
    its capabilities allow."""
    if await async_legacy_installed(hass):
        # The old integration still runs this machine until a restart.
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="legacy_restart_required"
        )
    children = None
    if CONF_LEGACY_ENTRY_ID in entry.data:
        # Re-created from a pre-3.0 entry: take over its devices and
        # entities before the model registers any (entity ids are kept).
        children = await async_adopt(hass, entry)
    if not await _model(entry).async_setup_entry(hass, entry):
        return False
    if children:
        async_restore_children(hass, entry.entry_id, children)
    machine = get_machine(hass, entry.entry_id)
    if machine is not None:
        # Voice/chat agents can drive any model through the LLM API; the
        # tool set follows the machine's capabilities. Unregistered by
        # entry.async_on_unload.
        register_llm_api(hass, entry, machine.machine_label)
        if Capability.RECIPES in machine.capabilities:
            async_register_services(hass)  # idempotent across entries
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one machine (and the services, with the last recipe machine)."""
    if not await _model(entry).async_unload_entry(hass, entry):
        return False
    await async_unload_services(hass)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up stores owned by a removed machine."""
    remove = getattr(_model(entry), "async_remove_entry", None)
    if remove is not None:
        await remove(hass, entry)
