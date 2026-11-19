"""Tool: xbloom__grind — manual grind with custom size and RPM."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.xb.machine import Capability, ManualLimits

from .base import XBloomBaseTool

_LOGGER = logging.getLogger(__name__)


def _parameters(limits: ManualLimits) -> vol.Schema:
    """The tool schema, bounded by the machine's own grind range."""
    size_min, size_max = limits.grind_size
    reference = (
        " — see xbloom__execute_recipe's GRIND SIZE REFERENCE for "
        "recommended ranges per brew method."
        if limits.grind_guide
        else ". Other machines' grind numbers don't carry over to this scale."
    )
    fields: dict = {
        vol.Optional(
            "grind_size",
            description=(
                f"Grind size on this machine's scale "
                f"({size_min}=finest–{size_max}=coarsest). "
                f"Defaults to the machine's current setting{reference}"
            ),
        ): vol.All(vol.Coerce(int), vol.Range(min=size_min, max=size_max)),
    }
    if limits.rpm is not None:
        rpm_min, rpm_max = limits.rpm
        fields[
            vol.Optional(
                "rpm",
                description=(
                    f"Grinder speed in RPM ({rpm_min}–{rpm_max}). Defaults "
                    "to the machine's current setting."
                ),
            )
        ] = vol.All(vol.Coerce(int), vol.Range(min=rpm_min, max=rpm_max))
    fields[
        vol.Optional(
            "confirmed",
            default=False,
            description=(
                "Set to true ONLY after the user has confirmed starting "
                "the grind the first (arming) call announced."
            ),
        )
    ] = bool
    return vol.Schema(fields)


def _describe(*, arms_on_machine: bool, has_recipes: bool, has_rpm: bool) -> str:
    """The same text for every model, minus what this one lacks."""
    arm = (
        "ARMS the machine — it opens its grind page showing the requested "
        "settings without starting —"
        if arms_on_machine
        else "only ARMS the request — nothing is sent to the machine yet —"
    )
    after = (
        "Use xbloom__pour afterwards for a manual pour, or "
        "xbloom__execute_recipe to grind and pour a full recipe in one call."
        if has_recipes
        else "Use xbloom__pour afterwards for a manual pour."
    )
    return (
        f"Grind beans on the xBloom with a custom grind size{' and RPM' if has_rpm else ''}. "
        "This is a manual grind — it does NOT pour water. Two-phase flow: "
        f"the first call (without confirmed) {arm} and you must then ask the "
        "user to confirm. Call again with confirmed=true to start the grind; "
        "if the user declines, call xbloom__cancel. confirmed=true only "
        "starts a grind that is still armed — otherwise it arms again and you "
        f"must ask again. {after}"
    )


class XBloomGrindTool(XBloomBaseTool):
    """Start a manual grind with the requested grind size (and RPM)."""

    required = frozenset({Capability.GRIND})
    name = "xbloom__grind"
    title = "Grind xBloom"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = ""  # built per machine in __init__ (_describe)

    def __init__(self, coordinator, hass: HomeAssistant) -> None:  # noqa: ANN001
        super().__init__(coordinator, hass)
        self.parameters = _parameters(coordinator.limits)
        self.description = _describe(
            arms_on_machine=self._arms_on_machine,
            has_recipes=Capability.RECIPES in coordinator.capabilities,
            has_rpm=coordinator.limits.rpm is not None,
        )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        args = tool_input.tool_args
        grind_size = args.get("grind_size")
        rpm = args.get("rpm")

        if (unavailable := await self._unavailable()) is not None:
            return unavailable

        # Mirror the slider state so the corresponding number entities
        # reflect what was actually requested.
        if grind_size is not None:
            self.coordinator.grind_size = int(grind_size)
        if rpm is not None:
            self.coordinator.rpm = int(rpm)

        confirmed = bool(args.get("confirmed", False))
        # Only a standing arm can be started. confirmed=true without one
        # (fresh conversation, or the arm was cleared machine-side) arms
        # again and asks again — never a one-shot start.
        start = confirmed and self.coordinator.armed_operation == "grind"
        try:
            if start:
                # Phase 2: the arm is still standing — bare start.
                ok = await self.coordinator.async_confirm_grind()
            else:
                # Phase 1: arm — the machine opens its grind page showing
                # the requested settings while the user is being asked.
                ok = await self.coordinator.async_arm_grind()
        except Exception as exc:
            _LOGGER.exception("xbloom__grind failed: %s", exc)
            return llm.ToolResult(
                data={
                    "success": False,
                    "error": f"Grind failed: {exc!s}",
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
                    "grind_size": self.coordinator.grind_size,
                    "rpm": getattr(self.coordinator, "rpm", None),
                    "instruction": (
                        (
                            "Grinding has NOT started: there was no standing arm "
                            "to confirm, so the machine was armed again. "
                            if confirmed
                            else ""
                        )
                        + self._armed_text("grind")
                        + "Ask the user to confirm starting the grind; "
                        "if they agree, call xbloom__grind again with "
                        "confirmed=true. If they decline, call xbloom__cancel."
                    ),
                }
            )
        return llm.ToolResult(
            data={
                "success": True,
                "grind_size": self.coordinator.grind_size,
                "rpm": getattr(self.coordinator, "rpm", None),
                "instruction": "Briefly confirm to the user that grinding has started.",
            }
        )
