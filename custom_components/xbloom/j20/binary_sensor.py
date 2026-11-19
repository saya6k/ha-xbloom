"""Binary sensor entities for XBloom Original (J20)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DATA_COORDINATOR, DOMAIN
from ..entity import scope_unique_ids
from .coordinator import J20Coordinator
from .entity import J20Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: J20Coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        scope_unique_ids(entry.entry_id, [
            J20OnlineBinarySensor(coordinator, entry),
            J20ProblemBinarySensor(coordinator, entry),
            J20GrindingBinarySensor(coordinator, entry),
            J20CalibratingBinarySensor(coordinator, entry),
        ])
    )


class _J20BinarySensor(J20Entity, BinarySensorEntity):
    pass


class J20OnlineBinarySensor(_J20BinarySensor):
    _attr_translation_key = "j20_online"
    _attr_unique_id = "xbloom_j20_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("online"))


class J20ProblemBinarySensor(_J20BinarySensor):
    _attr_translation_key = "j20_problem"
    _attr_unique_id = "xbloom_j20_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        warning = self.coordinator.data.get("warning")
        return warning not in (None, 0)


class J20GrindingBinarySensor(_J20BinarySensor):
    _attr_translation_key = "j20_grinding"
    _attr_unique_id = "xbloom_j20_grinding"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("grinding")


class J20CalibratingBinarySensor(_J20BinarySensor):
    _attr_translation_key = "j20_calibrating"
    _attr_unique_id = "xbloom_j20_calibrating"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("calibrating")
