"""Sensor entities for XBloom Original (J20)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...entity import scope_unique_ids
from .coordinator import J20Coordinator
from .entity import J20Entity
from .values import (
    DISP_RESERVE_MEANINGS,
    warning_active,
    warning_state,
    work_mode_state,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: J20Coordinator = entry.runtime_data
    async_add_entities(
        scope_unique_ids(entry.entry_id, [
            J20StateSensor(coordinator, entry),
            J20WarningSensor(coordinator, entry),
            J20BrewProgressSensor(coordinator, entry),
            J20PodSensor(coordinator, entry),
            J20McuVersionSensor(coordinator, entry, "PowerMCU", "power_mcu"),
            J20McuVersionSensor(coordinator, entry, "PannelMCU", "panel_mcu"),
        ])
    )


class _J20Sensor(J20Entity, SensorEntity):
    pass


class J20StateSensor(_J20Sensor):
    _attr_translation_key = "j20_state"
    _attr_unique_id = "xbloom_j20_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [  # noqa: RUF012  HA entity-attribute convention
        "idle", "awake", "ready", "grinding", "brewing", "complete",
        "sleeping",
    ]

    @property
    def native_value(self) -> str | None:
        return work_mode_state(self.coordinator.live.get("work_mode"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "work_mode": self.coordinator.data.get("work_mode"),
            "online": self.coordinator.data.get("online"),
        }


class J20WarningSensor(_J20Sensor):
    _attr_translation_key = "j20_warning"
    _attr_unique_id = "xbloom_j20_warning"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [  # noqa: RUF012  HA entity-attribute convention
        "none", "grind_setting", "water_low", "dock", "no_beans",
        "water_shortage", "voltage", "unknown",
    ]

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.online:
            return None
        return warning_state(self.coordinator.live.get("warning"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        warning = self.coordinator.live.get("warning")
        return {"warning_code": warning, "active": warning_active(warning)}


class J20BrewProgressSensor(_J20Sensor):
    _attr_translation_key = "j20_brew_progress"
    _attr_unique_id = "xbloom_j20_brew_progress"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.live.get("disp_reserve")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        value = self.coordinator.live.get("disp_reserve")
        return {"meaning": DISP_RESERVE_MEANINGS.get(value)}


class J20PodSensor(_J20Sensor):
    _attr_translation_key = "j20_pod"
    _attr_unique_id = "xbloom_j20_pod"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.live.get("pod")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"pod_present": self.coordinator.live.get("pod") is not None}


class J20McuVersionSensor(_J20Sensor):
    """Firmware version of one MCU, as reported in the thing shadow."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: J20Coordinator, entry: ConfigEntry, mcu: str, key: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._mcu = mcu
        self._attr_translation_key = f"j20_{key}"
        self._attr_unique_id = f"xbloom_j20_{key}"

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data.get("mcu_versions") or {}).get(self._mcu)
