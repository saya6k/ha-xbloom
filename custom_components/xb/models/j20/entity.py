"""Shared entity base for the Original (J20) platform."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import J20Coordinator


class J20Entity(CoordinatorEntity[J20Coordinator]):
    """Base for every J20 entity — one device, no child devices."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: J20Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Available while the cloud answers.

        An offline machine's live readings are unknown, not unavailable:
        the shadow keeps its last reported values while the machine is off
        Wi-Fi, so those entities read ``coordinator.live`` (empty then) and
        report None instead of stale state.
        """
        return super().available
