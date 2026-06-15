"""Tool: write_xbloom_easy_slot — push a recipe to onboard slot A/B/C."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .base import XBloomBaseTool
from .recipe import _summarize_recipe

_LOGGER = logging.getLogger(__name__)

VALID_SLOTS = ("A", "B", "C")


class XBloomWriteEasySlotTool(XBloomBaseTool):
    """Write a configured recipe to one of the machine's three Easy Mode slots.

    Easy Mode slots are the A / B / C shortcuts on the device's physical
    UI. Once a recipe is pushed to a slot, the user can run that recipe
    from the machine without opening Home Assistant or the app.
    """

    name = "write_xbloom_easy_slot"
    description = (
        "Save a configured XBloom recipe into one of the machine's three "
        "onboard Easy Mode slots (A, B, or C). After this the user can run "
        "the recipe directly from the device's slot button without using "
        "Home Assistant. This action does NOT brew anything — it only "
        "stores the recipe on the machine. Existing slot contents are "
        "overwritten."
    )
    parameters = vol.Schema(
        {
            vol.Required(
                "slot",
                description="Target slot — must be A, B, or C (case-insensitive).",
            ): vol.All(str, vol.Upper, vol.In(VALID_SLOTS)),
            vol.Required(
                "recipe_name",
                description=(
                    "Exact name of a recipe configured under the xbloom "
                    "section in configuration.yaml. Use list_xbloom_recipes "
                    "to discover available names."
                ),
            ): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict:
        slot_letter = str(tool_input.tool_args["slot"]).strip().upper()
        recipe_name = tool_input.tool_args["recipe_name"]

        if slot_letter not in VALID_SLOTS:
            return {
                "success": False,
                "error": "invalid_slot",
                "instruction": (
                    "Tell the user the slot must be A, B, or C and ask "
                    "which one they meant."
                ),
            }

        recipes = self.coordinator.recipes or {}
        if recipe_name not in recipes:
            return {
                "success": False,
                "error": "recipe_not_found",
                "available_recipes": list(recipes.keys()),
                "instruction": (
                    "Tell the user that recipe was not found. If there are "
                    "available recipes, mention them so the user can pick "
                    "one to push to the slot."
                ),
            }

        client = self.coordinator.client
        if client is None or not client.is_connected:
            try:
                ok = await self.coordinator.async_connect()
            except Exception as exc:
                _LOGGER.exception("auto-connect before slot write failed: %s", exc)
                ok = False
            if not ok:
                return {
                    "success": False,
                    "error": "connect_failed",
                    "instruction": (
                        "Tell the user the XBloom could not be reached over "
                        "Bluetooth. Ask them to check the machine is powered "
                        "on and in range."
                    ),
                }

        # Mirror the recipe-execute tool: setting selected_recipe lets
        # the existing coordinator.async_write_easy_slot path do the
        # YAML→XBloomRecipe conversion in one place.
        self.coordinator.selected_recipe = recipe_name
        try:
            await self.coordinator.async_write_easy_slot(slot_letter)
        except Exception as exc:
            _LOGGER.exception("write_xbloom_easy_slot failed: %s", exc)
            return {
                "success": False,
                "error": f"Slot write failed: {exc!s}",
            }

        # Reflect the selection on the recipe select entity.
        self.coordinator.async_update_listeners()

        return {
            "success": True,
            "slot": slot_letter,
            "recipe": _summarize_recipe(recipes[recipe_name]),
            "instruction": (
                f"Confirm to the user that the recipe is now stored on "
                f"slot {slot_letter}, and remind them they can run it from "
                f"the machine's onboard Easy Mode buttons."
            ),
        }
