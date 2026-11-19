"""Catalog of every XBloom LLM tool.

The single place that imports the tool implementations. Kept out of
llm/__init__.py so the platform entry module stays light — this module is
only imported on the XBloom API's success path, pre-warmed in the import
executor by `XBloomCoffeeAPI.async_get_api_instance` (see llm_api.py).

tests/test_llm_prompt.py checks the Studio prompt (llm/prompt.py) against this
tool list — update both together when a tool is added/removed/renamed.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

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
from .prompt import build_prompt  # noqa: F401  (callers import it from here)
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


def build_tools(coordinator: Any, hass: HomeAssistant) -> list[XBloomBaseTool]:
    """Fresh instances of every tool the machine's capabilities cover."""
    return [
        tool(coordinator, hass)
        for tool in TOOL_CLASSES
        if tool.required <= coordinator.capabilities
    ]
