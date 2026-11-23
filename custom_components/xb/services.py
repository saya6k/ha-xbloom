"""The integration's services (``xbloom.*``), shared by every machine model.

Every service is about recipes (or a recipe-capable machine's settings),
so they exist while at least one ``Capability.RECIPES`` machine is set up.
Each handler resolves its targets through ``machine.machines`` filtered by
the capability it needs, so a model without it is never called.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
import yaml
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_BYPASS_TEMPERATURE,
    ATTR_BYPASS_VOLUME,
    ATTR_CATEGORY,
    ATTR_CHANGES,
    ATTR_CUP_TYPE,
    ATTR_DISPLAY_BRIGHTNESS_LEVEL,
    ATTR_DOSE_G,
    ATTR_FLAVOR,
    ATTR_GRIND_SIZE,
    ATTR_KEYWORD,
    ATTR_MACHINE,
    ATTR_ORIGIN,
    ATTR_POUR_RADIUS_LEVEL,
    ATTR_PROCESS,
    ATTR_QUERY,
    ATTR_RATIO,
    ATTR_RECIPE,
    ATTR_RECIPE_ID,
    ATTR_RECIPE_YAML,
    ATTR_ROAST,
    ATTR_RPM,
    ATTR_SHARE_URL,
    ATTR_SLOT,
    ATTR_SORT,
    ATTR_SORT_DIRECTION,
    ATTR_SRC,
    ATTR_VARIETAL,
    ATTR_VIBRATION_AMPLITUDE_LEVEL,
    DOMAIN,
    SERVICE_ADVANCED_SETTINGS,
    SERVICE_CLOUD_EXPORT_RECIPE,
    SERVICE_CLOUD_IMPORT_RECIPE,
    SERVICE_CLOUD_SEARCH_COLLECTIVE_RECIPES,
    SERVICE_CREATE_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_EDIT_RECIPE,
    SERVICE_EXECUTE_RECIPE,
    SERVICE_EXECUTE_TEA_RECIPE,
    SERVICE_LIST_RECIPES,
    SERVICE_RESET_AUTO_MODE,
    SERVICE_WRITE_RECIPE_TO_AUTO_MODE,
    SERVICE_WRITE_RECIPE_TO_EASY_SLOT,
)
from .machine import Capability, machines
from .recipes.schema import find_recipe

_LOGGER = logging.getLogger(__name__)

_SERVICES = (
    SERVICE_EXECUTE_RECIPE,
    SERVICE_EXECUTE_TEA_RECIPE,
    SERVICE_LIST_RECIPES,
    SERVICE_CREATE_RECIPE,
    SERVICE_EDIT_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_CLOUD_IMPORT_RECIPE,
    SERVICE_CLOUD_EXPORT_RECIPE,
    SERVICE_CLOUD_SEARCH_COLLECTIVE_RECIPES,
    SERVICE_WRITE_RECIPE_TO_EASY_SLOT,
    SERVICE_WRITE_RECIPE_TO_AUTO_MODE,
    SERVICE_RESET_AUTO_MODE,
    SERVICE_ADVANCED_SETTINGS,
)

# extra=ALLOW_EXTRA lets config_entry_id (a custom field, not HA's
# built-in target: mechanism — see _coordinators_for_call) pass through
# alongside the typed fields below without being explicitly declared in
# every one of these schemas.
EXECUTE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_RECIPE): cv.string,
        vol.Optional(ATTR_GRIND_SIZE): vol.All(vol.Coerce(int), vol.Range(min=1, max=80)),
        vol.Optional(ATTR_RPM): vol.All(vol.Coerce(int), vol.Range(min=60, max=120)),
        vol.Optional(ATTR_DOSE_G): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100)
        ),
        vol.Optional(ATTR_RATIO): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=50)
        ),
        vol.Optional(ATTR_CUP_TYPE): vol.In(
            ["x_pod", "xpod", "omni_dripper", "other", "tea"]
        ),
        vol.Optional(ATTR_BYPASS_VOLUME): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=200)
        ),
        vol.Optional(ATTR_BYPASS_TEMPERATURE): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

# No dose/ratio/grind/bypass — none apply to the tea BLE sequence.
EXECUTE_TEA_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_RECIPE): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

CLOUD_IMPORT_RECIPE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_SHARE_URL): cv.string,
            vol.Optional(ATTR_RECIPE_ID): cv.string,
        },
        extra=vol.ALLOW_EXTRA,
    ),
    cv.has_at_least_one_key(ATTR_SHARE_URL, ATTR_RECIPE_ID),
)

LIST_RECIPES_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_QUERY): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

CREATE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RECIPE_YAML): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

EDIT_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RECIPE): cv.string,
        vol.Required(ATTR_CHANGES): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

DELETE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RECIPE): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

CLOUD_EXPORT_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RECIPE): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

WRITE_RECIPE_TO_EASY_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SLOT): vol.All(cv.string, vol.Upper, vol.In(["A", "B", "C"])),
        vol.Optional(ATTR_RECIPE): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

WRITE_RECIPE_TO_AUTO_MODE_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_RECIPE): cv.string},
    extra=vol.ALLOW_EXTRA,
)
RESET_AUTO_MODE_SCHEMA = vol.Schema({}, extra=vol.ALLOW_EXTRA)

ADVANCED_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_POUR_RADIUS_LEVEL): vol.All(vol.Coerce(int), vol.Range(min=0, max=4)),
        vol.Optional(ATTR_VIBRATION_AMPLITUDE_LEVEL): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=5)
        ),
        vol.Optional(ATTR_DISPLAY_BRIGHTNESS_LEVEL): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=3)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


# Facet filters are lists of codes and/or display names — the services.yaml
# multi-select submits snapshot codes (custom values allowed for categories
# added upstream later), the LLM tool passes name lists; both are resolved
# against the live criteria table in _cloud_client._resolve_criteria_values.
_FACET_LIST = vol.All(cv.ensure_list, [cv.string])

CLOUD_SEARCH_COLLECTIVE_RECIPES_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_KEYWORD): cv.string,
        vol.Optional(ATTR_CATEGORY): vol.In(["coffee", "tea"]),
        vol.Optional(ATTR_SRC): vol.In(["official", "user"]),
        vol.Optional(ATTR_MACHINE): _FACET_LIST,
        vol.Optional(ATTR_CUP_TYPE): _FACET_LIST,
        vol.Optional(ATTR_ORIGIN): _FACET_LIST,
        vol.Optional(ATTR_VARIETAL): _FACET_LIST,
        vol.Optional(ATTR_PROCESS): _FACET_LIST,
        vol.Optional(ATTR_ROAST): _FACET_LIST,
        vol.Optional(ATTR_FLAVOR): _FACET_LIST,
        vol.Optional(ATTR_SORT, default="likes"): vol.In(["date", "likes", "downloads"]),
        vol.Optional(ATTR_SORT_DIRECTION, default="desc"): vol.In(["asc", "desc"]),
    },
    extra=vol.ALLOW_EXTRA,
)



def _coordinators_for_call(
    hass: HomeAssistant,
    call: ServiceCall,
    capability: Capability = Capability.RECIPES,
) -> list:
    """Resolve which machines with ``capability`` a service call targets.

    With no config_entry target, applies to all configured machines
    (there is usually exactly one). A ``config_entry`` selector (not
    ``device``) is used so the picker offers exactly one item per
    physical XBloom machine — a device selector would also list the
    Grinder/Scale/Brewer child devices, and there's no way to filter
    those out of a plain fields-level device selector (hassfest
    rejects an ``entity:`` filter key there; that's only valid inside a
    ``target:`` block, which these services don't use). Each config
    entry maps 1:1 to a coordinator already, so no device-registry
    lookup is needed at all.

    ``config_entry_id`` is a bare string, not a list — HA's
    ``ConfigEntrySelector`` has no ``multiple`` option (confirmed against
    core's own ``helpers/selector.py``), so a real call only ever
    carries at most one id. Hardware-reported 2026-07-17: this
    used to do ``for eid in entry_ids`` over that string, which iterates
    it character-by-character — no single character ever matches a real
    config entry id, so every service call that actually specified a
    target machine failed with "No XBloom machine matched the service
    call," regardless of which service.
    """
    all_coords = machines(hass, capability)
    entry_id = call.data.get("config_entry_id")
    if not entry_id:
        # With several machines, an untargeted call used to run on all of
        # them (execute_recipe brewed on every Studio at once) or on
        # whichever came first — never what the caller meant. Ask instead.
        if len(all_coords) > 1:
            raise HomeAssistantError(
                "Several xBloom machines are set up — choose one with "
                "config_entry_id."
            )
        return list(all_coords.values())
    coordinator = all_coords.get(entry_id)
    return [coordinator] if coordinator else []


def _resolve_recipe_name(coord: Any, identifier: str | None) -> str:
    """The stored recipe ``identifier`` names, or the selected one."""
    if identifier:
        resolved = find_recipe(coord.recipes or {}, identifier)
        name = resolved[0] if resolved else None
    else:
        name = coord.selected_recipe
    if not name or name not in (coord.recipes or {}):
        raise ServiceValidationError(
            f"No recipe matches {identifier!r} on {coord.machine_label}."
            if identifier
            else f"No recipe is selected on {coord.machine_label}."
        )
    return name


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_EXECUTE_RECIPE):
        return

    async def _handle_execute_recipe(call: ServiceCall) -> None:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        # One machine at most (_coordinators_for_call). A refused brew (low
        # water, unsupported firmware, unknown recipe) fails the call, so an
        # automation can tell that nothing was brewed.
        coord = coordinators[0]
        name = _resolve_recipe_name(coord, call.data.get(ATTR_RECIPE))
        # select_recipe syncs the grind/RPM sliders to the recipe;
        # explicit overrides then take precedence for this brew.
        coord.select_recipe(name)
        if ATTR_GRIND_SIZE in call.data:
            coord.grind_size = int(call.data[ATTR_GRIND_SIZE])
        if ATTR_RPM in call.data and Capability.GRIND_SPEED in coord.capabilities:
            coord.rpm = int(call.data[ATTR_RPM])
        coord.async_update_listeners()
        overrides = {
            key: call.data[key]
            for key in (ATTR_DOSE_G, ATTR_RATIO, ATTR_CUP_TYPE)
            if key in call.data
        }
        await coord.async_execute_recipe(
            overrides=overrides or None,
            bypass_volume=call.data.get(ATTR_BYPASS_VOLUME),
            bypass_temperature=call.data.get(ATTR_BYPASS_TEMPERATURE),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXECUTE_RECIPE,
        _handle_execute_recipe,
        schema=EXECUTE_RECIPE_SCHEMA,
    )

    async def _handle_execute_tea_recipe(call: ServiceCall) -> None:
        """Leaner sibling of execute_recipe for tea — see brewing.py's
        _async_brew_tea, which takes no dose/ratio/grind/bypass overrides."""
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        coord = coordinators[0]
        name = _resolve_recipe_name(coord, call.data.get(ATTR_RECIPE))
        recipe = coord.recipes[name]
        if str(recipe.get("cup_type", "")).strip().lower() != "tea":
            raise ServiceValidationError(
                f"{name!r} is not a tea recipe (cup_type={recipe.get('cup_type')!r}) "
                "— use execute_recipe instead."
            )
        coord.select_recipe(name)
        await coord.async_execute_recipe()

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXECUTE_TEA_RECIPE,
        _handle_execute_tea_recipe,
        schema=EXECUTE_TEA_RECIPE_SCHEMA,
    )

    async def _handle_cloud_import_recipe(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        identifier = call.data.get(ATTR_SHARE_URL) or call.data.get(ATTR_RECIPE_ID)
        # Imports into every targeted machine's local store; the response
        # reflects the first machine (there is usually exactly one).
        first: ServiceResponse = None
        for coord in coordinators:
            result = await coord.async_import_cloud_recipe(identifier)
            if not result.get("success"):
                _LOGGER.warning(
                    "cloud_import_recipe failed for %s: %s",
                    coord.machine_label, result.get("message", result.get("error")),
                )
                continue
            if first is None:
                first = {
                    "uid": result["uid"],
                    "name": result["name"],
                    "recipe": result["recipe"],
                }
        if first is None:
            raise HomeAssistantError("cloud_import_recipe failed on every machine.")
        return first

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLOUD_IMPORT_RECIPE,
        _handle_cloud_import_recipe,
        schema=CLOUD_IMPORT_RECIPE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_write_recipe_to_easy_slot(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call, Capability.EASY_SLOTS)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        result = await coordinators[0].async_write_easy_slot(
            call.data[ATTR_SLOT], identifier=call.data.get(ATTR_RECIPE)
        )
        if not result.get("success"):
            raise HomeAssistantError(
                result.get("message", "write_recipe_to_easy_slot failed")
            )
        return {"slot": result["slot"], "uid": result["uid"], "name": result["name"]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_RECIPE_TO_EASY_SLOT,
        _handle_write_recipe_to_easy_slot,
        schema=WRITE_RECIPE_TO_EASY_SLOT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_write_recipe_to_auto_mode(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call, Capability.AUTO_MODE)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine with an auto mode matched the call.")
        result = await coordinators[0].async_write_auto_mode(call.data.get(ATTR_RECIPE))
        return {"uid": result.get("uid"), "name": result.get("name")}

    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_RECIPE_TO_AUTO_MODE,
        _handle_write_recipe_to_auto_mode,
        schema=WRITE_RECIPE_TO_AUTO_MODE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_reset_auto_mode(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call, Capability.AUTO_MODE)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine with an auto mode matched the call.")
        result = await coordinators[0].async_reset_auto_mode()
        return {"name": result.get("name")}

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_AUTO_MODE,
        _handle_reset_auto_mode,
        schema=RESET_AUTO_MODE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_advanced_settings(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call, Capability.ADVANCED_SETTINGS)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        result = await coordinators[0].async_set_advanced_settings(
            pour_radius_level=call.data.get(ATTR_POUR_RADIUS_LEVEL),
            vibration_amplitude_level=call.data.get(ATTR_VIBRATION_AMPLITUDE_LEVEL),
            display_brightness_level=call.data.get(ATTR_DISPLAY_BRIGHTNESS_LEVEL),
        )
        if not result.get("success"):
            raise HomeAssistantError(result.get("message", "advanced_settings failed"))
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADVANCED_SETTINGS,
        _handle_advanced_settings,
        schema=ADVANCED_SETTINGS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_cloud_export_recipe(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        # Cloud accounts are per-machine credentials — export from the
        # first targeted machine's store/account.
        result = await coordinators[0].async_export_recipe(call.data[ATTR_RECIPE])
        if not result.get("success"):
            raise HomeAssistantError(result.get("message", "cloud_export_recipe failed"))
        response: ServiceResponse = {"recipe": result["recipe"]}
        if "id" in result:
            response["id"] = result["id"]
            response["link"] = result.get("link")
        if result.get("warning"):
            response["warning"] = result["warning"]
        return response

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLOUD_EXPORT_RECIPE,
        _handle_cloud_export_recipe,
        schema=CLOUD_EXPORT_RECIPE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    # The local recipe store is per machine (per config entry) — like the
    # cloud-account services before them, the local CRUD services act on
    # the first targeted machine's store (there is usually exactly one).

    async def _handle_list_recipes(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        result = coordinators[0].list_local_recipes(query=call.data.get(ATTR_QUERY))
        return {"recipes": result["recipes"]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_RECIPES,
        _handle_list_recipes,
        schema=LIST_RECIPES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    def _parse_yaml_mapping(text: str) -> dict:
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise HomeAssistantError(f"Invalid recipe YAML: {exc}") from exc
        if not isinstance(parsed, dict):
            raise HomeAssistantError("Recipe YAML must be a mapping.")
        return parsed

    async def _handle_create_recipe(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        parsed = _parse_yaml_mapping(call.data[ATTR_RECIPE_YAML])
        result = coordinators[0].create_local_recipe(parsed)
        if not result.get("success"):
            raise HomeAssistantError(result.get("message", "create_recipe failed"))
        return {"uid": result["uid"], "name": result["name"]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_RECIPE,
        _handle_create_recipe,
        schema=CREATE_RECIPE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_edit_recipe(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        # Partial by design — only the fields present in `changes` move;
        # everything else (including uid/cloud metadata) stays.
        changes = _parse_yaml_mapping(call.data[ATTR_CHANGES])
        result = await coordinators[0].async_edit_local_recipe(
            call.data[ATTR_RECIPE], changes
        )
        if not result.get("success"):
            raise HomeAssistantError(result.get("message", "edit_recipe failed"))
        return {"uid": result["uid"], "name": result["name"], "recipe": result["recipe"]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_EDIT_RECIPE,
        _handle_edit_recipe,
        schema=EDIT_RECIPE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_delete_recipe(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        result = coordinators[0].delete_local_recipe(call.data[ATTR_RECIPE])
        if not result.get("success"):
            raise HomeAssistantError(result.get("message", "delete_recipe failed"))
        return {"uid": result["uid"], "name": result["name"]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_RECIPE,
        _handle_delete_recipe,
        schema=DELETE_RECIPE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_cloud_search_collective_recipes(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators_for_call(hass, call)
        if not coordinators:
            raise HomeAssistantError("No xBloom machine matched the service call.")
        # Public, unauthenticated, and not tied to any one machine's cloud
        # account — reuses the first targeted machine's coordinator purely
        # for its HTTP session / cached criteria lookup, same device
        # resolution convention as cloud_search_recipes.
        result = await coordinators[0].async_search_collective_recipes(
            keyword=call.data.get(ATTR_KEYWORD),
            category=call.data.get(ATTR_CATEGORY),
            src=call.data.get(ATTR_SRC),
            machine=call.data.get(ATTR_MACHINE),
            cup_type=call.data.get(ATTR_CUP_TYPE),
            origin=call.data.get(ATTR_ORIGIN),
            varietal=call.data.get(ATTR_VARIETAL),
            process=call.data.get(ATTR_PROCESS),
            roast=call.data.get(ATTR_ROAST),
            flavor=call.data.get(ATTR_FLAVOR),
            sort=call.data.get(ATTR_SORT, "likes"),
            sort_direction=call.data.get(ATTR_SORT_DIRECTION, "desc"),
        )
        if not result.get("success"):
            raise HomeAssistantError(
                result.get("message", "cloud_search_collective_recipes failed")
            )
        return {
            "recipes": result["list"],
            "total": result.get("total"),
            "unmatched": result.get("unmatched"),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLOUD_SEARCH_COLLECTIVE_RECIPES,
        _handle_cloud_search_collective_recipes,
        schema=CLOUD_SEARCH_COLLECTIVE_RECIPES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """After a machine unloads: drop the services once none are left,
    else refresh the remaining machines' recipe dropdowns."""
    remaining = list(machines(hass, Capability.RECIPES).values())
    if not remaining:
        for service in _SERVICES:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
        return
    # Drop the unloaded machine's recipes from the other machines'
    # recipe-service dropdowns (see _async_refresh_recipe_service_schemas).
    await remaining[0]._async_refresh_recipe_service_schemas()
