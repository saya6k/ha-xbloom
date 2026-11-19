"""Catalog of every XBloom LLM tool.

The single place that imports the tool implementations. Kept out of
llm/__init__.py so the platform entry module stays light — this module is
only imported on the XBloom API's success path, pre-warmed in the import
executor by `XBloomCoffeeAPI.async_get_api_instance` (see llm_api.py).

tests/test_llm_prompt.py checks XBLOOM_LLM_PROMPT against this tool list —
update both together when a tool is added/removed/renamed.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.xb.const import XBLOOM_LLM_PROMPT
from custom_components.xb.machine import Capability

from .base import XBloomBaseTool
from .calibrate import XBloomCalibrateGrinderTool
from .cancel import XBloomCancelTool
from .cloud_recipe import (
    XBloomExportRecipeTool,
    XBloomImportCloudRecipeTool,
    XBloomSearchCollectiveRecipesTool,
)
from .grind import XBloomGrindTool
from .local_recipe import (
    XBloomCreateRecipeTool,
    XBloomDeleteRecipeTool,
    XBloomEditRecipeTool,
)
from .pour import XBloomPourTool
from .recipe import (
    XBloomExecuteRecipeTool,
    XBloomGetRecipeTool,
    XBloomListRecipesTool,
)
from .slot import XBloomWriteEasySlotTool
from .status import XBloomStatusTool
from .tare import XBloomTareScaleTool

TOOL_CLASSES: tuple[type[XBloomBaseTool], ...] = (
    XBloomStatusTool,
    XBloomListRecipesTool,
    XBloomGetRecipeTool,
    XBloomCreateRecipeTool,
    XBloomEditRecipeTool,
    XBloomDeleteRecipeTool,
    XBloomPourTool,
    XBloomGrindTool,
    XBloomCancelTool,
    XBloomExecuteRecipeTool,
    XBloomWriteEasySlotTool,
    XBloomTareScaleTool,
    XBloomCalibrateGrinderTool,
    XBloomImportCloudRecipeTool,
    XBloomSearchCollectiveRecipesTool,
    XBloomExportRecipeTool,
)


def build_prompt(coordinator: Any) -> str:
    """The API prompt for one machine.

    A recipe-capable machine (the Studio) gets the full XBLOOM_LLM_PROMPT.
    Anything else gets a prompt built from its own capabilities and
    ManualLimits, so the agent is never told about tools, ranges or a
    transport the machine doesn't have.
    """
    caps = coordinator.capabilities
    if Capability.RECIPES in caps:
        return XBLOOM_LLM_PROMPT
    limits = coordinator.limits
    parts = [
        "You can control an xBloom coffee machine for manual grinds and "
        "pours. It has no saved recipes, Easy Mode slots or scale here — "
        "don't offer them.",
        "Use xbloom__get_status to check whether the machine is reachable "
        "and what it is doing. Every tool reaches the machine on its own; if "
        "one reports it unreachable, follow that tool's instruction.",
    ]
    if Capability.GRIND in caps:
        low, high = limits.grind_size
        parts.append(
            f"Use xbloom__grind for a manual grind. grind_size is on this "
            f"machine's own scale, {low}=finest to {high}=coarsest; grind "
            "numbers from other machines or recipes don't carry over."
        )
    if Capability.POUR in caps:
        (t_low, t_high), (v_low, v_high) = limits.temperature_c, limits.volume_ml
        boiling = " or boiling=true for boiling water" if limits.boiling_c else ""
        parts.append(
            f"Use xbloom__pour for a manual pour of {v_low}–{v_high} ml at "
            f"{t_low}–{t_high} °C{boiling}."
        )
    if caps & {Capability.GRIND, Capability.POUR}:
        parts.append(
            "xbloom__grind and xbloom__pour are two-phase: the first call only "
            "arms the request, then you ask the user to confirm and call the "
            "same tool again with confirmed=true. Never pass confirmed=true on "
            "a first call."
        )
    if Capability.CANCEL in caps:
        parts.append(
            "Use xbloom__cancel when the user declines an armed request or "
            "wants a running pour stopped; if it reports something can't be "
            "stopped, tell the user."
        )
    if Capability.CALIBRATE_GRINDER in caps:
        parts.append(
            "Use xbloom__calibrate_grinder when the user asks to calibrate or "
            "zero the grinder."
        )
    return " ".join(parts)


def build_tools(coordinator: Any, hass: HomeAssistant) -> list[XBloomBaseTool]:
    """Fresh instances of every tool the machine's capabilities cover."""
    return [
        tool(coordinator, hass)
        for tool in TOOL_CLASSES
        if tool.required <= coordinator.capabilities
    ]
