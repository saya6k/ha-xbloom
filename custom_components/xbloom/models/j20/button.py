"""Button entities for XBloom Original (J20)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...entity import scope_unique_ids
from .coordinator import J20Coordinator
from .entity import J20Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: J20Coordinator = entry.runtime_data
    async_add_entities(
        scope_unique_ids(entry.entry_id, [
            J20GrindButton(coordinator, entry),
            J20PourButton(coordinator, entry),
            J20StopButton(coordinator, entry),
            J20CalibrateButton(coordinator, entry),
        ])
    )


class _J20Button(J20Entity, ButtonEntity):
    pass


class J20GrindButton(_J20Button):
    """Start grinding at the current ``number.j20_grind_size`` value."""

    _attr_translation_key = "j20_grind"
    _attr_unique_id = "xbloom_j20_grind"

    async def async_press(self) -> None:
        await self.coordinator.async_grind()


class J20PourButton(_J20Button):
    """Start a manual pour with the current volume/temperature/pattern."""

    _attr_translation_key = "j20_pour"
    _attr_unique_id = "xbloom_j20_pour"

    async def async_press(self) -> None:
        await self.coordinator.async_pour()


class J20StopButton(_J20Button):
    """Cancel: drop a pending (LLM) arm, else stop a running pour (the app's
    ``bruw_curve: "FFFF11"`` sentinel). Also the panel's Cancel button."""

    _attr_translation_key = "j20_stop"
    _attr_unique_id = "xbloom_j20_stop"

    async def async_press(self) -> None:
        await self.coordinator.async_cancel()


class J20CalibrateButton(_J20Button):
    _attr_translation_key = "j20_calibrate"
    _attr_unique_id = "xbloom_j20_calibrate"

    async def async_press(self) -> None:
        await self.coordinator.async_calibrate_grinder()
