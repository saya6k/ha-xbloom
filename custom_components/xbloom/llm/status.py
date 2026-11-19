"""Tool: xbloom__get_status — read current machine state."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .base import XBloomBaseTool

_LOGGER = logging.getLogger(__name__)


class XBloomStatusTool(XBloomBaseTool):
    """Return the current state of the XBloom machine."""

    name = "xbloom__get_status"
    title = "Get xBloom status"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=True
    )
    description = (
        "Get the current status of the xBloom coffee machine: whether it can "
        "be reached, its running state (idle/grinding/brewing/paused/error/"
        "sleeping), and whatever else the model reports (brewer temperature, "
        "scale weight, water level, firmware version, any active warning)."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        if not await self.coordinator.async_ensure_available():
            return llm.ToolResult(
                data={
                    "connected": False,
                    "machine": self.coordinator.machine_label,
                    "error": "connect_failed",
                    "instruction": self.coordinator.unreachable_hint,
                },
                error=True,
            )

        return llm.ToolResult(
            data={
                "connected": True,
                # Model-specific fields (machine.Machine.status_summary).
                **self.coordinator.status_summary(),
                "instruction": (
                    "Summarize the machine status conversationally. Mention only "
                    "the fields that are relevant to what the user asked. Do not "
                    "list raw key/value pairs."
                ),
            }
        )
