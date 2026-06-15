"""Config flow for XBloom integration."""
from __future__ import annotations

import re
import logging
from typing import Any

import voluptuous as vol
import yaml

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_MAC_ADDRESS,
    CONF_RECIPES,
    CONF_TELEMETRY_INTERVAL,
    CONF_SESSION_TIMEOUT,
    DEFAULT_TELEMETRY_INTERVAL,
    DEFAULT_SESSION_TIMEOUT,
    DOMAIN,
)
from .schema import RECIPE_SCHEMA

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _valid_mac(mac: str) -> bool:
    return bool(MAC_RE.match(mac.strip()))


STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MAC_ADDRESS): str,
        vol.Optional(CONF_TELEMETRY_INTERVAL, default=DEFAULT_TELEMETRY_INTERVAL): vol.All(
            int, vol.Range(min=1, max=60)
        ),
        vol.Optional(CONF_SESSION_TIMEOUT, default=DEFAULT_SESSION_TIMEOUT): vol.All(
            int, vol.Range(min=10, max=3600)
        ),
    }
)


class XBloomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for XBloom."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: list[dict] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_MAC_ADDRESS].strip().upper()

            if not _valid_mac(mac):
                errors[CONF_MAC_ADDRESS] = "invalid_mac"
            else:
                # Check uniqueness
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()

                # Quick connection test
                try:
                    from xbloom import XBloomClient

                    client = XBloomClient(mac_address=mac)
                    ok = await client.connect(timeout=15.0)
                    if ok:
                        await client.disconnect()
                    else:
                        errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "cannot_connect"

                if not errors:
                    return self.async_create_entry(
                        title=f"XBloom ({mac})",
                        data={
                            CONF_MAC_ADDRESS: mac,
                            CONF_TELEMETRY_INTERVAL: user_input.get(
                                CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL
                            ),
                            CONF_SESSION_TIMEOUT: user_input.get(
                                CONF_SESSION_TIMEOUT, DEFAULT_SESSION_TIMEOUT
                            ),
                        },
                    )

        # Show form — optionally pre-fill with discovered device
        discovered_mac = ""
        try:
            from xbloom.scanner import discover_devices

            _LOGGER.debug("Scanning for XBloom devices…")
            devices = await discover_devices(timeout=5.0)
            if devices:
                discovered_mac = devices[0].address
                _LOGGER.info("Auto-discovered XBloom: %s", discovered_mac)
        except Exception as exc:
            _LOGGER.debug("BLE scan error (non-fatal): %s", exc)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAC_ADDRESS,
                    default=discovered_mac or vol.UNDEFINED,
                ): str,
                vol.Optional(
                    CONF_TELEMETRY_INTERVAL,
                    default=DEFAULT_TELEMETRY_INTERVAL,
                ): vol.All(int, vol.Range(min=1, max=60)),
                vol.Optional(
                    CONF_SESSION_TIMEOUT,
                    default=DEFAULT_SESSION_TIMEOUT,
                ): vol.All(int, vol.Range(min=10, max=3600)),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> XBloomOptionsFlow:
        return XBloomOptionsFlow(config_entry)


_RECIPE_YAML_PLACEHOLDER = """\
name: My Recipe
grind_size: 60
rpm: 80
bean_weight: 16.0
total_water: 250
cup_type: omni_dripper
pours:
  - volume: 50
    temperature: 92
    pausing: 45
    pattern: spiral
    vibration: after
  - volume: 100
    temperature: 92
    pausing: 30
    pattern: spiral
  - volume: 100
    temperature: 92
    pattern: spiral
"""


def _options_recipes(entry: config_entries.ConfigEntry) -> dict[str, dict]:
    """Return UI-managed recipes from entry.options as a plain dict."""
    raw = entry.options.get(CONF_RECIPES) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _save_options(
    entry: config_entries.ConfigEntry,
    *,
    recipes: dict[str, dict] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge the existing options with new recipes / settings and return the blob."""
    new_options: dict[str, Any] = dict(entry.options)
    if recipes is not None:
        new_options[CONF_RECIPES] = recipes
    if settings:
        new_options.update(settings)
    return new_options


class XBloomOptionsFlow(config_entries.OptionsFlow):
    """Options flow — menu-driven settings + recipe CRUD."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._editing: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_recipe", "edit_recipe", "delete_recipe"],
        )

    # ── Settings (telemetry + session timeout) ───────────────────────

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=_save_options(self._entry, settings=user_input),
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TELEMETRY_INTERVAL,
                        default=self._entry.options.get(
                            CONF_TELEMETRY_INTERVAL,
                            self._entry.data.get(
                                CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL
                            ),
                        ),
                    ): vol.All(int, vol.Range(min=1, max=60)),
                    vol.Optional(
                        CONF_SESSION_TIMEOUT,
                        default=self._entry.options.get(
                            CONF_SESSION_TIMEOUT,
                            self._entry.data.get(
                                CONF_SESSION_TIMEOUT, DEFAULT_SESSION_TIMEOUT
                            ),
                        ),
                    ): vol.All(int, vol.Range(min=10, max=3600)),
                }
            ),
        )

    # ── Add recipe ───────────────────────────────────────────────────

    async def async_step_add_recipe(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors, placeholders = {}, {}
        default_yaml = _RECIPE_YAML_PLACEHOLDER

        if user_input is not None:
            default_yaml = user_input.get("recipe_yaml", default_yaml)
            recipe, err = _parse_and_validate(default_yaml)
            if err:
                errors["base"], placeholders["error"] = err
            else:
                existing = _options_recipes(self._entry)
                if recipe["name"] in existing:
                    errors["base"] = "recipe_exists"
                    placeholders["error"] = recipe["name"]
                else:
                    existing[recipe["name"]] = recipe
                    return self.async_create_entry(
                        title="",
                        data=_save_options(self._entry, recipes=existing),
                    )

        return self.async_show_form(
            step_id="add_recipe",
            data_schema=vol.Schema(
                {
                    vol.Required("recipe_yaml", default=default_yaml): TextSelector(
                        TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    # ── Edit recipe (2 steps: pick → YAML) ───────────────────────────

    async def async_step_edit_recipe(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        existing = _options_recipes(self._entry)
        if not existing:
            return self.async_abort(reason="no_recipes")

        if user_input is None:
            return self.async_show_form(
                step_id="edit_recipe",
                data_schema=vol.Schema(
                    {
                        vol.Required("recipe_name"): SelectSelector(
                            SelectSelectorConfig(
                                options=sorted(existing.keys()),
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                    }
                ),
            )

        self._editing = user_input["recipe_name"]
        return await self.async_step_edit_recipe_yaml()

    async def async_step_edit_recipe_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        existing = _options_recipes(self._entry)
        if not self._editing or self._editing not in existing:
            return self.async_abort(reason="no_recipes")

        errors, placeholders = {}, {}
        default_yaml = yaml.safe_dump(
            dict(existing[self._editing]), allow_unicode=True, sort_keys=False
        )

        if user_input is not None:
            default_yaml = user_input.get("recipe_yaml", default_yaml)
            recipe, err = _parse_and_validate(default_yaml)
            if err:
                errors["base"], placeholders["error"] = err
            else:
                # Allow rename — drop the old key, add under the new name.
                new_recipes = {
                    k: v for k, v in existing.items() if k != self._editing
                }
                new_recipes[recipe["name"]] = recipe
                return self.async_create_entry(
                    title="",
                    data=_save_options(self._entry, recipes=new_recipes),
                )

        return self.async_show_form(
            step_id="edit_recipe_yaml",
            data_schema=vol.Schema(
                {
                    vol.Required("recipe_yaml", default=default_yaml): TextSelector(
                        TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    # ── Delete recipe ────────────────────────────────────────────────

    async def async_step_delete_recipe(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        existing = _options_recipes(self._entry)
        if not existing:
            return self.async_abort(reason="no_recipes")

        if user_input is None:
            return self.async_show_form(
                step_id="delete_recipe",
                data_schema=vol.Schema(
                    {
                        vol.Required("recipe_name"): SelectSelector(
                            SelectSelectorConfig(
                                options=sorted(existing.keys()),
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                    }
                ),
            )

        name = user_input["recipe_name"]
        remaining = {k: v for k, v in existing.items() if k != name}
        return self.async_create_entry(
            title="",
            data=_save_options(self._entry, recipes=remaining),
        )


def _parse_and_validate(raw: str) -> tuple[dict | None, tuple[str, str] | None]:
    """Parse YAML + RECIPE_SCHEMA. Returns (recipe, None) on success or
    (None, (error_key, detail)) on failure."""
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, ("invalid_yaml", str(exc))
    if not isinstance(parsed, dict):
        return None, ("invalid_yaml", "recipe must be a YAML mapping")
    try:
        return RECIPE_SCHEMA(parsed), None
    except vol.Invalid as exc:
        return None, ("invalid_recipe", str(exc))
