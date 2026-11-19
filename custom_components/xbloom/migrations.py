"""Config-entry migrations (shared by every machine model).

Entries created after a migration was added are born at the current
version, so each step is a no-op for them.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_RECIPES
from .entity import scoped_unique_id
from .recipes.schema import new_recipe_uid

_LOGGER = logging.getLogger(__name__)


def _migrate_recipe_v1_to_v2(recipe: dict) -> dict:
    """Translate one v1-schema recipe dict to the v2 (cloud-shaped) schema.

    v1: bean_weight/total_water, pours[].volume/temperature/pausing.
    v2: dose_g/ratio, pours[].volume_ml/temperature_c/pause_seconds.
    ``pattern``/``vibration`` are unchanged — only the renamed fields move.
    """
    dose_g = float(recipe.get("bean_weight", 15.0))
    total_water = float(recipe.get("total_water", 250))
    # ratio is meaningless for a zero dose (tea) — omit it and let
    # schema.compute_total_water_ml's pour-volume-sum fallback take over,
    # exactly as it did pre-migration when total_water was stored directly.
    ratio = (total_water / dose_g) if dose_g > 0 else None

    new_pours = []
    for p in recipe.get("pours", []):
        new_pour = dict(p)
        if "volume" in new_pour:
            new_pour["volume_ml"] = new_pour.pop("volume")
        if "temperature" in new_pour:
            new_pour["temperature_c"] = new_pour.pop("temperature")
        if "pausing" in new_pour:
            new_pour["pause_seconds"] = new_pour.pop("pausing")
        new_pours.append(new_pour)

    new_recipe = dict(recipe)
    new_recipe.pop("bean_weight", None)
    new_recipe.pop("total_water", None)
    new_recipe["dose_g"] = dose_g
    new_recipe["ratio"] = ratio
    new_recipe["pours"] = new_pours
    return new_recipe


def _migrate_recipe_v2_to_v3(recipe: dict) -> dict:
    """Inject the v3 local-store metadata into one v2 recipe dict.

    Every stored recipe gains a ``uid`` (the stable local identity) and a
    ``source`` of ``manual`` — v2 predates seed provenance, and options
    recipes were by definition user-managed. Existing metadata (e.g. a
    dict that somehow already has a uid) is left untouched.
    """
    migrated = dict(recipe)
    migrated.setdefault("uid", new_recipe_uid())
    migrated.setdefault("source", "manual")
    return migrated


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a config entry to the current schema version.

    v1 -> v2: recipes stored in ``entry.options[CONF_RECIPES]`` are
    rewritten from the old field names to the cloud-shaped ones (see
    ``_migrate_recipe_v1_to_v2``). Recipes defined in ``configuration.yaml``
    are NOT touched here — the integration doesn't own that file — so YAML
    users must update their field names by hand; this logs a pointer to do so.

    v2 -> v3: every stored recipe gains local-store metadata (``uid`` /
    ``source`` — see ``_migrate_recipe_v2_to_v3``); tombstones (None) are
    preserved as-is.
    """
    if entry.version == 1:
        options_recipes = entry.options.get(CONF_RECIPES) or {}
        if isinstance(options_recipes, dict) and options_recipes:
            migrated = {
                name: (_migrate_recipe_v1_to_v2(recipe) if recipe is not None else None)
                for name, recipe in options_recipes.items()
            }
            new_options = dict(entry.options)
            new_options[CONF_RECIPES] = migrated
            hass.config_entries.async_update_entry(
                entry, options=new_options, version=2,
            )
            _LOGGER.info(
                "Migrated %d UI-managed recipe(s) to the new schema "
                "(dose_g/ratio, pours[].volume_ml/temperature_c/pause_seconds)",
                len([r for r in migrated.values() if r is not None]),
            )
        else:
            hass.config_entries.async_update_entry(entry, version=2)

        _LOGGER.warning(
            "XBloom recipe schema changed (bean_weight/total_water -> "
            "dose_g/ratio; pours[].volume/temperature/pausing -> "
            "volume_ml/temperature_c/pause_seconds). UI-managed recipes "
            "were migrated automatically. If you define recipes in "
            "configuration.yaml, update their field names by hand — see "
            "the README for the new recipe format."
        )

    if entry.version == 2:
        options_recipes = entry.options.get(CONF_RECIPES) or {}
        if isinstance(options_recipes, dict) and options_recipes:
            migrated = {
                name: (_migrate_recipe_v2_to_v3(recipe) if recipe is not None else None)
                for name, recipe in options_recipes.items()
            }
            new_options = dict(entry.options)
            new_options[CONF_RECIPES] = migrated
            hass.config_entries.async_update_entry(
                entry, options=new_options, version=3,
            )
            _LOGGER.info(
                "Migrated %d UI-managed recipe(s) to v3 (local uid/source metadata)",
                len([r for r in migrated.values() if r is not None]),
            )
        else:
            hass.config_entries.async_update_entry(entry, version=3)

    if entry.version == 3 and entry.minor_version < 2:
        # Entity unique ids used to be fixed strings ("xbloom_grind"), so
        # a second machine's entities collided with the first's and were
        # never created. Scope this entry's registry entries to the entry;
        # only the unique id changes — entity ids, names, areas and
        # history are untouched.
        await er.async_migrate_entries(
            hass, entry.entry_id, _scope_unique_id_migration(entry.entry_id)
        )
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True


def _scope_unique_id_migration(
    entry_id: str,
) -> Callable[[er.RegistryEntry], dict | None]:
    """Registry migration callback: prefix unscoped unique ids with the entry id."""
    prefix = scoped_unique_id(entry_id, "")

    @callback
    def _migrate(entity_entry: er.RegistryEntry) -> dict | None:
        if entity_entry.unique_id.startswith(prefix):
            return None
        return {"new_unique_id": scoped_unique_id(entry_id, entity_entry.unique_id)}

    return _migrate
