"""Select entities for XBloom Original (J20)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...entity import scope_unique_ids
from .coordinator import J20Coordinator
from .entity import J20Entity
from .values import PATTERN_CENTER, PATTERN_CIRCULAR, PATTERN_SPIRAL


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: J20Coordinator = entry.runtime_data
    async_add_entities(
        scope_unique_ids(
            entry.entry_id,
            [J20PatternSelect(coordinator, entry), J20RecipeSelect(coordinator, entry)],
        )
    )


class J20PatternSelect(J20Entity, SelectEntity):
    """Pour pattern encoded in the ``bruw_curve`` (center/circular/spiral)."""

    _attr_translation_key = "j20_pattern"
    _attr_unique_id = "xbloom_j20_pattern"
    _attr_options = [  # noqa: RUF012  HA entity-attribute convention
        PATTERN_CENTER,
        PATTERN_CIRCULAR,
        PATTERN_SPIRAL,
    ]

    @property
    def current_option(self) -> str:
        return self.coordinator.pattern

    async def async_select_option(self, option: str) -> None:
        self.coordinator.pattern = option
        self.async_write_ha_state()


class J20RecipeSelect(J20Entity, SelectEntity):
    """The recipe the execute button and the panel brew."""

    _attr_translation_key = "j20_recipe"
    _attr_unique_id = "xbloom_j20_recipe"

    @property
    def options(self) -> list[str]:
        return self.coordinator.recipe_names

    @property
    def current_option(self) -> str | None:
        return self.coordinator.selected_recipe

    async def async_select_option(self, option: str) -> None:
        if option in (self.coordinator.recipes or {}):
            self.coordinator.select_recipe(option)
