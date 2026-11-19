"""The machine interface shared features (services, LLM tools, panel,
recipe store) program against, so they never import a model package.

Every model's coordinator registers itself in
``hass.data[DOMAIN][entry_id][DATA_COORDINATOR]`` and declares what it can
do through ``capabilities``. Shared code looks machines up with
:func:`machines` / :func:`get_machine`, filtered by the capability it
needs, and only then calls the members that capability promises. Adding a
model means implementing the base protocol plus the protocols of the
capabilities it claims — nothing outside ``models/`` changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN


class Capability(StrEnum):
    """What a machine model supports; gates every shared feature."""

    GRIND = "grind"  # ManualControl: arm/confirm grind at grind_size
    GRIND_SPEED = "grind_speed"  # ...plus a settable rpm
    POUR = "pour"  # ManualControl: arm/confirm pour of volume at temperature
    POUR_FLOW_RATE = "pour_flow_rate"  # ...plus a settable flow_rate
    CANCEL = "cancel"  # async_cancel()
    CALIBRATE_GRINDER = "calibrate_grinder"  # async_calibrate_grinder()
    TARE = "tare"  # async_tare_scale()
    RECIPES = "recipes"  # RecipeStoreMixin + recipe execution
    EASY_SLOTS = "easy_slots"  # async_write_easy_slot / easy_slot_contents
    PODS = "pods"  # pod_library + _async_lookup_pod
    ADVANCED_SETTINGS = "advanced_settings"  # async_set_advanced_settings
    PHOTO = "photo"  # async_read_photo / photo_version / detected_color


@dataclass(frozen=True, slots=True)
class ManualLimits:
    """The ranges a model's manual grind/pour accept (tool schemas use them)."""

    grind_size: tuple[int, int]
    temperature_c: tuple[int, int]
    # The setpoint the model treats as boiling (its BP end), if any.
    boiling_c: int | None
    volume_ml: tuple[int, int]
    rpm: tuple[int, int] | None = None
    flow_rate: tuple[float, float] | None = None


class Machine(Protocol):
    """What every model's coordinator provides."""

    entry_id: str
    capabilities: frozenset[Capability]
    data: dict[str, Any] | None

    @property
    def machine_label(self) -> str:
        """A stable, human-readable id (MAC, serial …) for API names."""

    @property
    def unreachable_hint(self) -> str:
        """An LLM instruction for when the machine can't be reached."""

    async def async_ensure_available(self) -> bool:
        """Make the machine reachable for a command (connect, wake …)."""

    def status_summary(self) -> dict[str, Any]:
        """Model-specific status fields for the LLM status tool."""

    def async_update_listeners(self) -> None: ...


class ManualControlMachine(Machine, Protocol):
    """GRIND / POUR / CANCEL / CALIBRATE_GRINDER members."""

    limits: ManualLimits
    grind_size: int
    temperature: float
    volume: int

    @property
    def armed_operation(self) -> str | None:
        """"grind" / "pour" / "recipe" while armed and waiting to confirm."""

    async def async_arm_grind(self) -> bool: ...
    async def async_confirm_grind(self) -> bool: ...
    async def async_arm_pour(self) -> bool: ...
    async def async_confirm_pour(self) -> bool: ...
    async def async_cancel(self) -> None: ...
    async def async_calibrate_grinder(self) -> None: ...


def machines(
    hass: HomeAssistant, capability: Capability | None = None
) -> dict[str, Any]:
    """Every set-up machine (entry_id -> coordinator), optionally only those
    with ``capability``."""
    found: dict[str, Any] = {}
    for entry_id, data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(data, dict) or DATA_COORDINATOR not in data:
            continue
        machine = data[DATA_COORDINATOR]
        if capability is None or capability in machine.capabilities:
            found[entry_id] = machine
    return found


def get_machine(
    hass: HomeAssistant, entry_id: str, capability: Capability | None = None
) -> Any | None:
    """One machine by entry id, or None (unknown, unloaded, or lacking
    ``capability``)."""
    return machines(hass, capability).get(entry_id)
