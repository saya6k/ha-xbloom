"""Move a pre-3.0 install (domain ``xbloom``) over to this one (``xb``).

Home Assistant keys config entries, the entity registry and the device
registry by integration domain, and has no rename of its own. HACS installs
the update into ``custom_components/xb`` and leaves ``custom_components/xbloom``
behind, so after the update the old integration keeps running its machines
and this one isn't even loaded (HA loads an integration only for its own
config entries). The move is therefore started by the user:

1. Settings → Add integration → xBloom. While ``xbloom`` entries exist the
   config flow offers to move them (``async_step_migrate``). Submitting
   deletes the old folder and re-creates each ``xbloom`` entry here (same
   title, data, options and unique id) marked with the old entry id.
2. Until Home Assistant restarts the old integration is still running, so
   the new entries wait (setup error, Repairs issue "restart"); two
   integrations would fight over the same Bluetooth link and panel URL.
3. After the restart the old integration is gone. ``async_adopt`` moves each
   old entry's devices and entities over — entity ids, names, areas and
   history stay, only the owning entry and unique-id prefix change — copies
   its pod library and deletes the old entry.

Not carried over (release notes): ``xbloom.*`` service calls become
``xb.*``, a ``configuration.yaml`` ``xbloom:`` block becomes ``xb:``, and a
conversation agent re-selects the machine's LLM API (its id carries the
entry id).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import CONF_LEGACY_ENTRY_ID, DOMAIN
from .entity import scoped_unique_id

_LOGGER = logging.getLogger(__name__)

LEGACY_DOMAIN = "xbloom"
RESTART_ISSUE = "legacy_restart_required"
# pods.PodLibrary's store key (kept identical across the rename).
_POD_STORE = "xbloom_pods_{}"
_POD_STORE_VERSION = 1


def legacy_folder(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("custom_components", LEGACY_DOMAIN))


def legacy_running(hass: HomeAssistant) -> bool:
    """The old integration is loaded in this run (its folder may already be
    gone — it stays loaded until Home Assistant restarts)."""
    return LEGACY_DOMAIN in hass.config.components


async def async_legacy_installed(hass: HomeAssistant) -> bool:
    return legacy_running(hass) or await hass.async_add_executor_job(
        legacy_folder(hass).is_dir
    )


def pending_legacy_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Old entries not yet re-created here."""
    claimed = {
        entry.data.get(CONF_LEGACY_ENTRY_ID)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    return [
        old
        for old in hass.config_entries.async_entries(LEGACY_DOMAIN)
        if old.entry_id not in claimed
    ]


def _remove_folder(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


async def async_start_migration(hass: HomeAssistant) -> None:
    """Delete the old folder and re-create every old entry here.

    Called by the config flow's migrate step; each entry is created by its
    own import flow (one flow makes one entry).
    """
    await hass.async_add_executor_job(_remove_folder, legacy_folder(hass))
    for old in pending_legacy_entries(hass):
        _LOGGER.info("Moving xBloom machine %r over from the pre-3.0 install", old.title)
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={CONF_LEGACY_ENTRY_ID: old.entry_id},
            )
        )


def async_flag_restart(hass: HomeAssistant) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        RESTART_ISSUE,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=RESTART_ISSUE,
    )


def _new_identifier(identifier: tuple[str, str], old_id: str, new_id: str) -> tuple[str, str]:
    domain, value = identifier
    if domain != LEGACY_DOMAIN:
        return identifier
    # Studio devices are keyed by the entry id ("<entry>", "<entry>_grinder");
    # Original devices by the cloud device id ("j20_<id>").
    if value == old_id or value.startswith(f"{old_id}_"):
        value = new_id + value[len(old_id):]
    return (DOMAIN, value)


async def async_adopt(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Take over the old entry's devices, entities and pods, then delete it.

    Called by ``async_setup_entry`` before the model sets the entry up, so
    the model finds its registry entries already in place. The old
    integration must not be loaded (entities it has loaded can't move).

    A main device with child devices (the Studio's Grinder/Scale/Brewer)
    can't change config entry, and child devices can't move at all, so the
    children are removed and the model re-creates them under the moved main
    device; their user settings are returned for ``async_restore_children``.
    """
    old_id = entry.data[CONF_LEGACY_ENTRY_ID]
    new_id = entry.entry_id
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Entities first: removing a device deletes the entities still owned by
    # that device's entry (moved ones only lose the device link, restored
    # when the model registers them again).
    for entity in er.async_entries_for_config_entry(ent_reg, old_id):
        base = entity.unique_id.removeprefix(f"{old_id}_")
        ent_reg.async_update_entity_platform(
            entity.entity_id,
            DOMAIN,
            new_config_entry_id=new_id,
            new_unique_id=scoped_unique_id(new_id, base),
        )

    children: dict = {}
    for child in dr.async_child_entries_for_config_entry(dev_reg, old_id):
        idents = frozenset(_new_identifier(i, old_id, new_id) for i in child.identifiers)
        children[idents] = {
            "area_id": child.area_id,
            "disabled_by": child.disabled_by,
            "labels": set(child.labels),
            "name_by_user": child.name_by_user,
        }
        dev_reg.async_remove_device(child.id)
    for device in dr.async_entries_for_config_entry(dev_reg, old_id):
        dev_reg.async_update_device(
            device.id,
            new_config_entry_id=new_id,
            new_identifiers={_new_identifier(i, old_id, new_id) for i in device.identifiers},
        )

    old_pods: Store = Store(hass, _POD_STORE_VERSION, _POD_STORE.format(old_id))
    if (pods := await old_pods.async_load()) is not None:
        await Store(hass, _POD_STORE_VERSION, _POD_STORE.format(new_id)).async_save(pods)
        await old_pods.async_remove()

    data = {k: v for k, v in entry.data.items() if k != CONF_LEGACY_ENTRY_ID}
    hass.config_entries.async_update_entry(entry, data=data)
    if hass.config_entries.async_get_entry(old_id) is not None:
        await hass.config_entries.async_remove(old_id)
    ir.async_delete_issue(hass, DOMAIN, RESTART_ISSUE)
    _LOGGER.info("Moved xBloom machine %r over from the pre-3.0 install", entry.title)
    return children


def async_restore_children(hass: HomeAssistant, entry_id: str, children: dict) -> None:
    """Give the re-created child devices back their user settings."""
    dev_reg = dr.async_get(hass)
    for idents, settings in children.items():
        for ident in idents:
            if (child := dev_reg.async_get_child_device_by_identifier(ident, entry_id)) is not None:
                changes = {k: v for k, v in settings.items() if v not in (None, set())}
                if changes:
                    dev_reg.async_update_child_device(child.id, **changes)
                break
