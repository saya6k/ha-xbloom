"""Tool: xbloom__tare_scale — zero the scale (cmd 8500)."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.xb.machine import Capability

from .base import XBloomBaseTool

_LOGGER = logging.getLogger(__name__)


class XBloomTareScaleTool(XBloomBaseTool):
    """Zero the XBloom scale."""

    required = frozenset({Capability.TARE})
    name = "xbloom__tare_scale"
    title = "Tare xBloom scale"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=False, idempotent=True, open_world=True
    )
    description = (
        "Zero (tare) the xBloom built-in scale. Use this when the user asks "
        "to tare, zero, or reset the scale, or after they've placed a cup or "
        "dripper and want the reading to start at 0 g. The action is instant "
        "and has no parameters."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        if (unavailable := await self._unavailable()) is not None:
            return unavailable

        try:
            await self.coordinator.async_tare_scale()
        except Exception as exc:
            _LOGGER.exception("xbloom__tare_scale failed: %s", exc)
            return llm.ToolResult(
                data={
                    "success": False,
                    "error": f"Tare failed: {exc!s}",
                },
                error=True,
            )

        return llm.ToolResult(
            data={
                "success": True,
                "instruction": "Briefly confirm to the user that the scale has been zeroed.",
            }
        )
