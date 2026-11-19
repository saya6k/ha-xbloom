"""Tool: xbloom__pour — manual pour with custom temperature and volume."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.xbloom.machine import Capability, ManualLimits

from .base import XBloomBaseTool

_LOGGER = logging.getLogger(__name__)


def _parameters(limits: ManualLimits) -> vol.Schema:
    """The tool schema, bounded by the machine's own pour ranges.

    Temperature has a controlled range plus, where the model has one, a
    special "boiling" mode: ``limits.boiling_c`` is the slider's BP end,
    which the model transmits as its boiling constant (Studio: 96 -> 98 °C;
    see coordinator._wire_temperature, T11).
    """
    temp_min, temp_max = limits.temperature_c
    vol_min, vol_max = limits.volume_ml
    fields: dict = {
        vol.Optional(
            "temperature",
            description=(
                f"Water temperature in Celsius ({temp_min}–{temp_max}). "
                "Required unless boiling=true."
            ),
        ): vol.All(vol.Coerce(float), vol.Range(min=temp_min, max=temp_max)),
        vol.Optional(
            "boiling",
            description=(
                "Set to true to use the boiling-point mode instead of a "
                "specific temperature. When true, the temperature field "
                "is ignored."
            ),
            default=False,
        ): bool,
        vol.Required(
            "volume",
            description=f"Volume of water in milliliters ({vol_min}–{vol_max}).",
        ): vol.All(vol.Coerce(int), vol.Range(min=vol_min, max=vol_max)),
    }
    if limits.flow_rate is not None:
        flow_min, flow_max = limits.flow_rate
        fields[
            vol.Optional(
                "flow_rate",
                description=(
                    f"Flow rate in ml/s ({flow_min}–{flow_max}). "
                    "Defaults to the machine's current setting."
                ),
            )
        ] = vol.All(vol.Coerce(float), vol.Range(min=flow_min, max=flow_max))
    fields[
        vol.Optional(
            "confirmed",
            default=False,
            description=(
                "Set to true ONLY after the user has confirmed starting "
                "the pour the first (arming) call announced."
            ),
        )
    ] = bool
    return vol.Schema(fields)


class XBloomPourTool(XBloomBaseTool):
    """Start a manual pour with the requested temperature and volume."""

    required = frozenset({Capability.POUR})
    name = "xbloom__pour"
    title = "Pour xBloom"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Pour water from the xBloom with a custom temperature and volume. "
        "This is a manual pour — it does NOT grind beans. Temperature is in "
        "degrees Celsius (see the temperature parameter's range) or set "
        "boiling=true for the boiling-point mode (used for tea or "
        "descaling). Volume is in milliliters. "
        "Two-phase flow: the first call (without confirmed) ARMS the "
        "machine — it opens its pour page showing the requested "
        "temperature and pattern without pouring — and you must then ask "
        "the user to confirm (and to place a cup). Call again with "
        "confirmed=true to start the pour; if the user declines, call "
        "xbloom__cancel to back out. confirmed=true only starts a pour "
        "that is still armed — otherwise it arms again and you must ask "
        "again."
    )
    def __init__(self, coordinator, hass: HomeAssistant) -> None:  # noqa: ANN001
        super().__init__(coordinator, hass)
        self.parameters = _parameters(coordinator.limits)

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        args = tool_input.tool_args
        limits = self.coordinator.limits
        temp_min, temp_max = limits.temperature_c
        boiling = bool(args.get("boiling", False)) and limits.boiling_c is not None
        if boiling:
            temperature = float(limits.boiling_c)
        elif "temperature" in args:
            temperature = float(args["temperature"])
        else:
            return llm.ToolResult(
                data={
                    "success": False,
                    "error": "missing_temperature",
                    "instruction": (
                        "Ask the user what temperature they want, between "
                        f"{temp_min}°C and {temp_max}°C, or "
                        "whether they want boiling-point water."
                    ),
                },
                error=True,
            )
        volume = int(args["volume"])
        flow_rate = args.get("flow_rate")

        if (unavailable := await self._unavailable()) is not None:
            return unavailable

        # Mirror the slider state so the corresponding number entities reflect
        # what was actually requested.
        self.coordinator.temperature = round(temperature)
        self.coordinator.volume = volume
        if flow_rate is not None:
            self.coordinator.flow_rate = float(flow_rate)

        confirmed = bool(args.get("confirmed", False))
        # Only a standing arm can be started; confirmed=true without one
        # arms again and asks again — never a one-shot start.
        start = confirmed and self.coordinator.armed_operation == "pour"
        try:
            if start:
                ok = await self.coordinator.async_confirm_pour()
            else:
                # Phase 1: arm — 8007 plus the entry push of the mirrored
                # temperature/pattern, so the machine display shows what
                # will happen while the user is being asked.
                ok = await self.coordinator.async_arm_pour()
        except Exception as exc:
            _LOGGER.exception("xbloom__pour failed: %s", exc)
            return llm.ToolResult(
                data={
                    "success": False,
                    "error": f"Pour failed: {exc!s}",
                },
                error=True,
            )

        # Notify entities that slider state changed.
        self.coordinator.async_update_listeners()

        if not ok:
            # The coordinator logs and swallows BLE failures.
            return llm.ToolResult(
                data={
                    "success": False,
                    "error": "start_failed" if start else "arm_failed",
                    "instruction": (
                        "Tell the user the xBloom did not respond, so nothing "
                        "was started."
                    ),
                },
                error=True,
            )

        if not start:
            return llm.ToolResult(
                data={
                    "success": True,
                    "armed": True,
                    "temperature_c": temperature,
                    "boiling": boiling,
                    "volume_ml": volume,
                    "instruction": (
                        (
                            "The pour has NOT started: there was no standing arm "
                            "to confirm, so the machine was armed again. "
                            if confirmed
                            else ""
                        )
                        + "The machine is now showing its pour page with these "
                        "settings. Ask the user to confirm starting the pour "
                        "and to place a cup under the dispenser; if they "
                        "agree, call xbloom__pour again with confirmed=true "
                        "(same arguments). If they decline, call xbloom__cancel."
                    ),
                }
            )
        return llm.ToolResult(
            data={
                "success": True,
                "temperature_c": temperature,
                "boiling": boiling,
                "volume_ml": volume,
                "flow_rate_ml_s": getattr(self.coordinator, "flow_rate", None),
                "instruction": (
                    "Briefly confirm to the user that the pour has started. If "
                    "boiling=true, describe it as 'boiling water'; otherwise "
                    "mention the temperature in Celsius."
                ),
            }
        )
