"""The recipe store shared by every recipe-capable machine.

``schema`` defines the recipe format, ``default_recipes`` the bundled
fallback set, and ``store`` the per-entry local store (CRUD, seeding, cloud
import/export). This module loads the integration-wide layers — bundled
defaults and ``configuration.yaml`` recipes — into ``hass.data``.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant

from ..const import CONF_RECIPES, DOMAIN
from .default_recipes import DEFAULT_RECIPES
from .schema import RECIPE_SCHEMA, yaml_recipe_uid

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_RECIPES, default=[]): [RECIPE_SCHEMA],
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def async_load_recipes(hass: HomeAssistant, config: dict) -> None:
    """Load bundled defaults + YAML-defined recipes into hass.data.

    ``hass.data[DOMAIN]["default_recipes"]`` (``default_recipes.DEFAULT_RECIPES``)
    is only used by ``coordinator.seed_bundled_recipes()`` — the fresh-install
    seed that fills the local store before the one-time cloud seed
    (``coordinator.async_seed_recipes``) completes. Recipe merge order
    (lowest precedence first), rebuilt by ``coordinator._rebuild_recipes()``:
      1. ``configuration.yaml`` ``xbloom: recipes:`` block.
      2. The local store — ``entry.options[CONF_RECIPES]`` (source of truth).
    """
    hass.data.setdefault(DOMAIN, {})

    # Defaults are validated through the same schema as YAML so any
    # malformed default is caught at startup rather than at brew time.
    validated_defaults: dict[str, dict] = {}
    for raw in DEFAULT_RECIPES:
        try:
            validated = RECIPE_SCHEMA(raw)
        except vol.Invalid as exc:
            _LOGGER.error(
                "Bundled default recipe %r failed schema validation; skipping: %s",
                raw.get("name", "<unnamed>"), exc,
            )
            continue
        validated_defaults[validated["name"]] = validated
    hass.data[DOMAIN]["default_recipes"] = validated_defaults

    if DOMAIN in config and CONF_RECIPES in config[DOMAIN]:
        recipes = config[DOMAIN][CONF_RECIPES]
        # YAML recipes are re-loaded every boot, so their uid must be
        # deterministic (derived from the name) to stay stable.
        hass.data[DOMAIN]["yaml_recipes"] = {
            r["name"]: {**r, "uid": yaml_recipe_uid(r["name"]), "source": "yaml"}
            for r in recipes
        }
        _LOGGER.info(
            "Loaded %d default + %d YAML recipe(s)",
            len(validated_defaults), len(recipes),
        )
    else:
        hass.data[DOMAIN]["yaml_recipes"] = {}
        _LOGGER.info(
            "Loaded %d default recipe(s); no YAML recipes configured",
            len(validated_defaults),
        )
