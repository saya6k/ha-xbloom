"""Studio recipe execution and Easy Mode slot writes.

The recipe store itself (CRUD, seeding, cloud sync) is model-agnostic and
lives in ``recipes/store.py``; this mixin turns a stored recipe into the
Studio's BLE brew sequence.
"""
from __future__ import annotations

import logging

from homeassistant.exceptions import HomeAssistantError

from custom_components.xbloom.const import CONF_EASY_SLOTS
from custom_components.xbloom.models.j15 import brewing
from custom_components.xbloom.models.j15.ble.models import (
    CupType,
    PourPattern,
    PourStep,
    VibrationPattern,
    XBloomRecipe,
)
from custom_components.xbloom.recipes.schema import (
    compute_total_water_ml,
    find_recipe,
    scale_pours_to_total,
)

from .constants import (
    MIN_FIRMWARE_EASY_MODE,
    MIN_FIRMWARE_TEA,
    WATER_SOURCE_TANK,
    _firmware_at_least,
)

_LOGGER = logging.getLogger(__name__)

_YAML_CUP_TYPE_MAP = {
    "x_pod": int(CupType.X_POD),
    "xpod": int(CupType.X_POD),
    "omni_dripper": int(CupType.OMNI_DRIPPER),
    "other": int(CupType.OTHER),
    "tea": int(CupType.TEA),
}

_YAML_VIBRATION_MAP = {
    "none": VibrationPattern.NONE,
    "before": VibrationPattern.BEFORE,
    "after": VibrationPattern.AFTER,
    "both": VibrationPattern.BOTH,
}


def _build_recipe_from_yaml(raw: dict) -> XBloomRecipe:
    """Build an XBloomRecipe from a validated YAML recipe dict.

    Bypasses xbloom.models.recipes.parse_recipe_json because that helper
    expects the upstream JSON shape (camelCase keys, `dose`, `cupType`)
    and treats `grind_size: 0` / `bean_weight: 0` as missing via `or`,
    silently substituting defaults — which routes tea recipes (no grind,
    no beans) into the coffee brew path.

    Reads the local schema's cloud-shaped field names (``dose_g``,
    ``ratio``, ``pours[].volume_ml/temperature_c/pause_seconds``) but
    still constructs the vendored ``XBloomRecipe``/``PourStep`` with
    THEIR field names (``bean_weight``, ``total_water``, ``volume``,
    ``temperature``, ``pausing``) — that vendored class is untouched, so
    the translation happens only here.
    """
    cup_raw = raw.get("cup_type", 0)
    if isinstance(cup_raw, str):
        cup_val = _YAML_CUP_TYPE_MAP.get(cup_raw.strip().lower(), 0)
    else:
        cup_val = int(cup_raw)

    pours: list[PourStep] = []
    for p in raw.get("pours", []):
        vib_raw = p.get("vibration", "none")
        vib = (
            _YAML_VIBRATION_MAP.get(vib_raw.strip().lower(), VibrationPattern.NONE)
            if isinstance(vib_raw, str)
            else VibrationPattern(int(vib_raw))
        )
        pours.append(
            PourStep(
                volume=int(p["volume_ml"]),
                temperature=int(p["temperature_c"]),
                flow_rate=float(p.get("flow_rate", 3.0)),
                pausing=int(p.get("pause_seconds", 0)),
                pattern=PourPattern(int(p.get("pattern", 2))),
                vibration=vib,
            )
        )

    # total_water = dose_g * ratio (matches the XBloom cloud API's own
    # dose/grandWater relationship), rounded to the nearest ml to absorb
    # float drift from a repeating-decimal ratio. Falls back to summing
    # pour volumes when ratio/dose_g can't produce a total (tea recipes
    # have no weighed dose) — see schema.compute_total_water_ml, shared
    # so this and the LLM-facing recipe summary can't disagree on the
    # actual brewed total. A zero footer byte 2 causes the machine to
    # skip grinding (hot water only) on Easy Mode slots and may also
    # confuse live brew, so the fallback still matters here.
    total_water = round(compute_total_water_ml(raw))

    return XBloomRecipe(
        grind_size=int(raw.get("grind_size", 0)),
        total_water=total_water,
        rpm=int(raw.get("rpm", 80)),
        cup_type=cup_val,
        name=str(raw.get("name", "Unknown")),
        bean_weight=float(raw.get("dose_g", 0.0)),
        pours=pours,
    )


def _apply_pour_overrides(recipe: XBloomRecipe, overrides: list[dict]) -> None:
    """Override individual pours' volume / flow_rate / pattern by index.

    Each entry is a dict with a 0-based ``pour_index`` plus any of
    ``volume`` / ``flow_rate`` / ``pattern`` (pattern as an int 0/1/2).
    Used by the LLM execute tool so an agent can tweak single pours
    without rewriting the saved recipe. Out-of-range indexes are skipped.
    The dataclass validates only at construction, so callers are
    responsible for passing in-range values (the tool schema enforces it).
    """
    for ov in overrides:
        idx = int(ov.get("pour_index", -1))
        if not 0 <= idx < len(recipe.pours):
            continue
        pour = recipe.pours[idx]
        if ov.get("volume") is not None:
            pour.volume = int(ov["volume"])
        if ov.get("flow_rate") is not None:
            pour.flow_rate = float(ov["flow_rate"])
        if ov.get("pattern") is not None:
            pour.pattern = PourPattern(int(ov["pattern"]))


class RecipesMixin:
    """Recipe selection/execution and Easy Mode slots (Studio BLE)."""

    def select_recipe(self, name: str | None) -> None:
        """Set the active recipe and sync the grind/RPM sliders to it.

        Only coffee recipes that actually grind push their grind_size /
        rpm onto the number entities — tea and no-grind recipes leave the
        sliders untouched (they don't grind, so their values are
        meaningless and would clobber the user's manual-grind settings).
        After syncing, the number entities are the source of truth: the
        user can tweak them and :meth:`async_execute_recipe` will brew
        with the tweaked values. (Bypass is recipe-scoped, not a slider —
        it stays on the YAML value unless overridden per brew.)
        """
        self.selected_recipe = name
        raw = (self.recipes or {}).get(name) if name else None
        if not raw:
            return
        cup = raw.get("cup_type", "omni_dripper")
        is_tea = str(cup).strip().lower() == "tea" or cup == int(CupType.TEA)
        grind = int(raw.get("grind_size", 0) or 0)
        if is_tea or grind <= 0:
            return
        self.grind_size = grind
        self.rpm = int(raw.get("rpm", self.rpm) or self.rpm)
        self.async_update_listeners()

    def _prepare_recipe_execution(
        self,
        *,
        overrides: dict | None,
        pour_overrides: list[dict] | None,
        bypass_volume: float | None,
        bypass_temperature: float | None,
    ) -> tuple[XBloomRecipe, bool, float, float] | None:
        """Shared pre-brew logic for :meth:`async_execute_recipe` (full
        single-shot) and :meth:`async_arm_recipe` (arm half of the
        two-stage manual button flow, 2026-07-18): water check, firmware
        gate, recipe build with overrides/pour_overrides/grind-rpm-sync
        applied, and bypass resolution. Both callers apply the exact same
        checks/overrides/rescaling this way; only what happens after
        (full brew vs. arm-only) differs between them.

        Returns ``(recipe, is_tea, bypass_vol, bypass_temp)``, or
        ``None`` if no valid recipe is selected (callers log and no-op).
        Raises ``HomeAssistantError`` for a low water level or an
        unsupported-firmware tea recipe — checked before any BLE traffic,
        so callers should call this before their own ``try`` block.
        """
        # Check water BEFORE touching the machine (mode switch, BLE writes,
        # etc.) — without this, a low-water recipe attempt runs the whole
        # brew sequence and only fails once the firmware fires
        # RD_ErrorLackOfWater, so the user finds out mid-attempt instead of
        # up front. Skipped when the user has told us they're on a direct
        # (hose) feed: water_level_ok tracks the internal tank sensor, which
        # stays empty/unreliable by design on a hose setup and would
        # otherwise block every brew. (This is the same water_source select
        # that otherwise only affects manual pour — here it's just the
        # user's declaration of which feed is actually plumbed in.)
        if self.water_source == WATER_SOURCE_TANK and not self.data.get(
            "water_level_ok", True
        ):
            raise HomeAssistantError(
                "xBloom water level is too low — refill the tank before brewing."
            )
        if not self.selected_recipe or self.selected_recipe not in self.recipes:
            _LOGGER.warning("No valid recipe selected (%s)", self.selected_recipe)
            return None
        # Tea (cmd 4512/4513) doesn't exist on firmware older than
        # V12.0D.300 — the machine would silently ignore it rather than
        # refuse cleanly, so check before touching the machine at all
        # (mode switch included). See MIN_FIRMWARE_TEA's docstring above.
        raw_cup_type = self.recipes[self.selected_recipe].get("cup_type")
        if overrides and "cup_type" in overrides:
            raw_cup_type = overrides["cup_type"]
        if str(raw_cup_type).lower() == "tea" and not _firmware_at_least(
            self.data.get("version"), MIN_FIRMWARE_TEA
        ):
            raise HomeAssistantError(
                f"Tea recipes require xBloom firmware {MIN_FIRMWARE_TEA} or newer "
                f"(current: {self.data.get('version') or 'unknown'})."
            )
        raw = self.recipes[self.selected_recipe]
        # Not a recipe field: applied to every pour once the recipe is built.
        temperature = (overrides or {}).get("temperature_c")
        if overrides:
            raw = {**raw, **{k: v for k, v in overrides.items() if k != "temperature_c"}}
            if "dose_g" in overrides or "ratio" in overrides:
                dose = float(raw.get("dose_g", 0) or 0)
                ratio = raw.get("ratio")
                if dose > 0 and ratio:
                    effective_bypass = (
                        float(raw.get("bypass_volume", 0.0) or 0.0)
                        if bypass_volume is None else float(bypass_volume)
                    )
                    raw["pours"] = scale_pours_to_total(
                        raw.get("pours", []),
                        dose * float(ratio) - effective_bypass,
                    )
        recipe = _build_recipe_from_yaml(raw)
        if temperature is not None:
            for pour in recipe.pours:
                pour.temperature = int(temperature)
        is_tea = brewing.is_tea_recipe(recipe)
        if not is_tea and recipe.grind_size > 0:
            recipe.grind_size = int(self.grind_size)
            recipe.rpm = int(self.rpm)
        if pour_overrides:
            _apply_pour_overrides(recipe, pour_overrides)
        # Bypass — coffee only. Default to the recipe's YAML value; an
        # explicit override (service / LLM) wins. The tea sequence forces
        # bypass off internally, so tea always passes 0/0.
        if is_tea:
            bypass_vol = bypass_temp = 0.0
        else:
            bypass_vol = (
                float(raw.get("bypass_volume", 0.0) or 0.0)
                if bypass_volume is None else float(bypass_volume)
            )
            bypass_temp = (
                float(raw.get("bypass_temperature", 0.0) or 0.0)
                if bypass_temperature is None else float(bypass_temperature)
            )
        return recipe, is_tea, bypass_vol, bypass_temp

    async def async_execute_recipe(
        self,
        *,
        overrides: dict | None = None,
        pour_overrides: list[dict] | None = None,
        bypass_volume: float | None = None,
        bypass_temperature: float | None = None,
    ) -> None:
        """Execute the currently selected YAML recipe.

        Routing lives in :mod:`brewing`. Coffee uses an inline sequence
        (mirrors brAzzi64/xbloom-ble) that threads bypass_volume /
        bypass_temperature into the 8102 packet; tea (cup_type=4) takes
        the separate tea sequence. See brewing.py.

        For coffee grinding recipes the live ``grind_size`` / ``rpm``
        number values override the YAML — :meth:`select_recipe` keeps
        them in sync with the recipe, so by execute time they hold either
        the recipe value or the user's tweak. Tea / no-grind recipes are
        brewed as configured. ``bypass_volume`` / ``bypass_temperature``
        (service / LLM only) override the recipe's bypass for this brew —
        ``None`` means use the recipe's YAML value; bypass can be added to
        a recipe that has none. Tea always brews with bypass off.
        ``pour_overrides`` (LLM-only) tweaks individual pours' volume /
        flow_rate / pattern.

        ``overrides`` replaces top-level recipe scalars (``dose_g`` /
        ``ratio`` / ``cup_type``) for this brew only — the stored recipe
        is untouched. ``temperature_c`` (LLM-only) sets every pour's
        water temperature. Changing ``dose_g``/``ratio`` changes the total
        brew water, so the pours are proportionally rescaled to keep
        ``sum(pours) + bypass == dose_g * ratio`` (the machine's own
        invariant). Grind/RPM overrides go through the number-entity
        values above instead.

        Always brews in one call — the execute_recipe / execute_tea_recipe
        services call this directly. See
        :meth:`async_arm_recipe`/:meth:`async_confirm_recipe` for the
        two-stage form (HA button entity and the LLM tool).
        """
        if not await self._async_ensure_connected():
            return
        prepared = self._prepare_recipe_execution(
            overrides=overrides, pour_overrides=pour_overrides,
            bypass_volume=bypass_volume, bypass_temperature=bypass_temperature,
        )
        if prepared is None:
            return
        recipe, _is_tea, bypass_vol, bypass_temp = prepared
        try:
            # No Easy→Pro switch here — Easy Mode runs the 8001/8002 brew
            # sequence fine (hardware-confirmed 2026-07-19; the old "hot
            # water only in Easy" belief was the ratio-footer bug). See
            # connection.py's note where _ensure_pro_mode used to live.
            # Snapshot the final (post-override, post-rescale) pour list so
            # the "bloom" handler in _dispatch_event can look up each
            # pour's actual flow_rate as the brew progresses.
            self._active_recipe_pours = recipe.pours
            self._executing_recipe = True
            self._active_operation = "recipe"
            self.current_pour_index = None
            # Sleep-retry wrapped (2026-07-18) — see coordinator.
            # connection._async_retry_while_sleeping's docstring. If the
            # machine was asleep, none of this sequence's writes took
            # effect, so retrying the whole thing from the top is safe.
            await self._async_retry_while_sleeping(
                lambda: brewing.async_execute_recipe(
                    self.client, recipe,
                    bypass_volume=bypass_vol,
                    bypass_temperature=bypass_temp,
                )
            )
        except Exception as exc:
            _LOGGER.error("Recipe execute error: %s", exc, exc_info=True)
            self._executing_recipe = False
            self._active_recipe_pours = None
            self._active_operation = None

    async def async_arm_recipe(
        self,
        *,
        overrides: dict | None = None,
        pour_overrides: list[dict] | None = None,
        bypass_volume: float | None = None,
        bypass_temperature: float | None = None,
    ) -> None:
        """Arm half of the two-stage execute-recipe flow (the HA button's
        first press, and the xbloom__execute_recipe LLM tool's first
        call — see ``_armed_operation``'s docstring in ``__init__.py``):
        runs every check :meth:`async_execute_recipe` does and queues the
        currently selected recipe on the machine (through 8001/8004 for
        coffee, or 4513 for tea) without starting it.
        ``async_confirm_recipe()`` sends the actual go command. Takes the
        same per-brew overrides as :meth:`async_execute_recipe`; they are
        baked into the queued payload, so the confirm needs none.
        """
        if not await self._async_ensure_connected():
            return
        prepared = self._prepare_recipe_execution(
            overrides=overrides, pour_overrides=pour_overrides,
            bypass_volume=bypass_volume, bypass_temperature=bypass_temperature,
        )
        if prepared is None:
            return
        recipe, is_tea, bypass_vol, bypass_temp = prepared
        try:
            tea_payload = await self._async_retry_while_sleeping(
                lambda: brewing.async_arm_recipe(
                    self.client, recipe,
                    bypass_volume=bypass_vol,
                    bypass_temperature=bypass_temp,
                )
            )
            self._armed_operation = "recipe"
            self._armed_recipe_is_tea = is_tea
            self._armed_recipe_tea_payload = tea_payload
            # Snapshot now so pause/resume-adjacent UI (flow rate, etc.)
            # is ready the instant confirm actually starts the brew —
            # mirrors async_execute_recipe's own timing relative to its
            # single BLE call.
            self._active_recipe_pours = recipe.pours
        except Exception as exc:
            _LOGGER.error("Recipe arm error: %s", exc, exc_info=True)
            self._active_recipe_pours = None

    async def async_confirm_recipe(self) -> bool:
        """Second press of the two-stage manual execute-recipe button
        flow: send the go command (8002 coffee, 4512 tea) for the recipe
        queued by :meth:`async_arm_recipe`.

        Returns whether the brew started. Failures are logged, not
        raised, so the LLM tool reads this to avoid telling the user a
        refused brew has started.
        """
        if not await self._async_ensure_connected():
            return False
        try:
            self._executing_recipe = True
            self._active_operation = "recipe"
            self.current_pour_index = None
            await self._async_retry_while_sleeping(
                lambda: brewing.async_confirm_recipe(
                    self.client,
                    is_tea=self._armed_recipe_is_tea,
                    tea_payload=self._armed_recipe_tea_payload,
                )
            )
        except Exception as exc:
            _LOGGER.error("Recipe confirm error: %s", exc, exc_info=True)
            self._executing_recipe = False
            self._active_recipe_pours = None
            self._active_operation = None
            return False
        finally:
            self._armed_operation = None
            self._armed_recipe_is_tea = False
            self._armed_recipe_tea_payload = None
        return True

    async def async_write_easy_slot(
        self, slot_letter: str, identifier: str | None = None
    ) -> dict:
        """Write a recipe to Easy Mode slot A/B/C (11510, type-2 packet).

        ``identifier`` (uid / cloud table id / share URL/id / name)
        selects the recipe; omitted, the currently-selected recipe (the
        Recipe ``select`` entity) is written — that's what the slot
        button entities do. A share URL/id not present locally is
        auto-imported first (clone + uid), so "write this shared recipe
        to slot B" is one call. On success **only the target letter's**
        slot → recipe mapping is persisted in ``entry.options["easy_slots"]``
        so the slot text entities can show (and restore) what HA last
        *intentionally* wrote; the machine itself never reports slot
        contents.

        Live-verified 2026-07-15 (cross-referenced against
        Janczykkkko/xbloom-ble's independent capture): the machine only
        *persists* a slot when all three (A/B/C) are written together —
        writing one alone leaves it hung at "saving" (RETRY) — and only
        accepts slot writes in Pro Mode. So this call fills in the other
        two slots from ``entry.options["easy_slots"]`` (falling back to
        the target recipe for a slot HA has never written — the machine
        has no readback, so there's nothing else to preserve it with),
        force-switches to Pro Mode if needed, writes all three, then
        restores whatever mode the machine was in before. That fallback
        recipe *is* sent to the machine for an unwritten slot (there's no
        other valid payload to send), but it is deliberately **not**
        recorded as that slot's own assignment — otherwise the first
        write to any slot would make every other never-configured slot's
        sensor falsely flip from unknown to "registered" too (hardware-
        confirmed 2026-07-17: writing only slot A with B/C both unknown
        made all three sensors show as registered).
        """
        if identifier:
            resolved = find_recipe(self.recipes or {}, identifier)
            if resolved is None and self._looks_like_share_ref(str(identifier)):
                imported = await self.async_import_cloud_recipe(str(identifier))
                if not imported.get("success"):
                    return imported
                resolved = find_recipe(self.recipes or {}, imported["uid"])
            if resolved is None:
                return {
                    "success": False,
                    "error": "recipe_not_found",
                    "message": f"No local recipe matches {identifier!r}.",
                }
            name, raw = resolved
        else:
            name = self.selected_recipe
            if not name or name not in (self.recipes or {}):
                _LOGGER.warning(
                    "Easy slot write ignored — no recipe selected (%s)", name
                )
                return {
                    "success": False,
                    "error": "no_recipe_selected",
                    "message": "No recipe is selected.",
                }
            raw = self.recipes[name]

        # Easy Mode slots (11510) are payload-identical to the coffee-only
        # 8001/8004 auto-brew recipe blob (brewing.async_write_easy_slots
        # builds them with the same _build_coffee_recipe_payload used
        # there) — there is no dedicated tea slot format, and 8004 itself
        # is hardware-confirmed to NOT enter tea mode (see the tea
        # sections in docs/en/brewing-notes.md). So a tea recipe written
        # to a slot can never brew as real tea from the physical button
        # — at best it silently runs as a flat no-siphon multi-pour, and
        # at worst (if the recipe's grind_size/dose_g were left at
        # RECIPE_SCHEMA's coffee-oriented defaults, 50/15.0g, instead of
        # explicitly zeroed)
        # it grinds beans for a tea recipe, which is what this check exists
        # to catch. Checked before any BLE traffic (mode switch included).
        if str(raw.get("cup_type", "")).strip().lower() == "tea":
            _LOGGER.warning(
                "Easy slot write refused — %r is a tea recipe, Easy Mode "
                "slots can't brew tea correctly", name,
            )
            return {
                "success": False,
                "error": "tea_not_supported_in_easy_slot",
                "message": (
                    f"{name!r} is a tea recipe — Easy Mode slots can't brew "
                    "tea correctly (no dedicated tea slot format on this "
                    "firmware). Use the execute_tea_recipe service or the "
                    "Recipe select entity + manual brew instead."
                ),
            }

        if not await self._async_ensure_connected():
            return {
                "success": False,
                "error": "not_connected",
                "message": "The xBloom is not connected over Bluetooth.",
            }

        # Auto/Easy Mode (cmd 11510/11511/11512) doesn't exist on firmware
        # older than V12.0D.210 — check before any mode-switch/slot-write
        # BLE traffic. See MIN_FIRMWARE_EASY_MODE's docstring above.
        if not _firmware_at_least(self.data.get("version"), MIN_FIRMWARE_EASY_MODE):
            return {
                "success": False,
                "error": "firmware_too_old",
                "message": (
                    f"Easy Mode requires xBloom firmware {MIN_FIRMWARE_EASY_MODE} "
                    f"or newer (current: {self.data.get('version') or 'unknown'})."
                ),
            }

        target_letter = slot_letter.strip().upper()
        if target_letter not in ("A", "B", "C"):
            return {
                "success": False,
                "error": "invalid_slot",
                "message": f"slot must be A, B, or C — got {slot_letter!r}",
            }

        # Fill in the other two slots from our own record of what HA last
        # wrote (the machine can't be asked what's actually there). A
        # slot HA has never written mirrors the target recipe rather than
        # being left as an unknown/blank.
        slot_names = {target_letter: name}
        slot_raws = {target_letter: raw}
        for other in ("A", "B", "C"):
            if other == target_letter:
                continue
            contents = self.easy_slot_contents(other)
            other_resolved = (
                find_recipe(self.recipes or {}, contents["uid"])
                if contents and contents.get("uid") else None
            )
            if other_resolved:
                slot_names[other], slot_raws[other] = other_resolved
            else:
                slot_names[other], slot_raws[other] = name, raw

        try:
            slot_recipes = {
                letter: _build_recipe_from_yaml(slot_raws[letter])
                for letter in ("A", "B", "C")
            }
        except Exception as exc:
            _LOGGER.error(
                "Easy slot write error building recipes (%s): %s",
                target_letter, exc, exc_info=True,
            )
            return {
                "success": False,
                "error": "write_failed",
                "message": f"Slot write failed: {exc}",
            }

        switched_to_pro = False
        try:
            if (self.data or {}).get("mode", "pro") == "easy":
                await self._async_switch_mode_with_retry("pro")
                switched_to_pro = True

            # Sleep-retry wrapped (2026-07-18) — see coordinator.
            # connection._async_retry_while_sleeping's docstring.
            await self._async_retry_while_sleeping(
                lambda: brewing.async_write_easy_slots(self.client, slot_recipes)
            )
        except Exception as exc:
            _LOGGER.error(
                "Easy slot write error (%s): %s", target_letter, exc, exc_info=True
            )
            return {
                "success": False,
                "error": "write_failed",
                "message": f"Slot write failed: {exc}",
            }
        finally:
            if switched_to_pro:
                try:
                    await self._async_switch_mode_with_retry("easy")
                    await self.async_refresh()
                except Exception as exc:
                    _LOGGER.warning("Restoring Easy Mode after slot write failed: %s", exc)

        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            slots = dict(entry.options.get(CONF_EASY_SLOTS) or {})
            # Only the target letter was actually requested by the user —
            # the other two were mirrored purely to satisfy the hardware's
            # all-three-at-once write requirement above. Recording them
            # here too would make untouched slots' sensors falsely show as
            # "registered" the first time any slot is written.
            slots[target_letter] = {
                "uid": slot_raws[target_letter].get("uid"),
                "name": slot_names[target_letter],
            }
            new_options = dict(entry.options)
            new_options[CONF_EASY_SLOTS] = slots
            self.hass.config_entries.async_update_entry(entry, options=new_options)
        self.async_update_listeners()
        return {"success": True, "slot": target_letter, "name": name,
                "uid": raw.get("uid")}

    def easy_slot_contents(self, slot_letter: str) -> dict | None:
        """What HA last wrote to a slot — ``{"uid", "name"}`` or None."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return None
        slots = entry.options.get(CONF_EASY_SLOTS) or {}
        return slots.get(slot_letter.upper())

