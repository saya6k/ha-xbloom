"""Number entities for XBloom Original (J20)."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...entity import scope_unique_ids
from .coordinator import J20Coordinator
from .entity import J20Entity
from .values import (
    GRIND_SIZE_MAX,
    GRIND_SIZE_MIN,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    VOLUME_MAX,
    VOLUME_MIN,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: J20Coordinator = entry.runtime_data
    async_add_entities(
        scope_unique_ids(entry.entry_id, [
            J20GrindSizeNumber(coordinator, entry),
            J20VolumeNumber(coordinator, entry),
            J20TemperatureNumber(coordinator, entry),
        ])
    )


class J20GrindSizeNumber(J20Entity, NumberEntity):
    """The grind size the next ``button.j20_grind`` press will send.

    Range/default from ``CoffeeConstantUtil.getGrindSizeJ20`` (1–30, 15).
    Local UI state only — the shadow carries no live grind size.
    """

    _attr_translation_key = "j20_grind_size"
    _attr_unique_id = "xbloom_j20_grind_size"
    _attr_native_min_value = GRIND_SIZE_MIN
    _attr_native_max_value = GRIND_SIZE_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float:
        return float(self.coordinator.grind_size)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.grind_size = int(value)
        self.async_write_ha_state()


class J20VolumeNumber(J20Entity, NumberEntity):
    """Millilitres the next ``button.j20_pour`` press will send (30–500)."""

    _attr_translation_key = "j20_volume"
    _attr_unique_id = "xbloom_j20_volume"
    _attr_native_min_value = VOLUME_MIN
    _attr_native_max_value = VOLUME_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_device_class = NumberDeviceClass.VOLUME
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float:
        return float(self.coordinator.volume_ml)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.volume_ml = int(value)
        self.async_write_ha_state()


class J20TemperatureNumber(J20Entity, NumberEntity):
    """Pour temperature in °C (85–96; the ends map to RT 85 / BP 95)."""

    _attr_translation_key = "j20_temperature"
    _attr_unique_id = "xbloom_j20_temperature"
    _attr_native_min_value = TEMPERATURE_MIN
    _attr_native_max_value = TEMPERATURE_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float:
        return float(self.coordinator.temperature_c)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.temperature_c = float(value)
        self.async_write_ha_state()
