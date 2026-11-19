"""The LLM API prompt, built from what one machine can do.

Every model gets the same sentences for the features it shares, and only
those: capabilities pick the paragraphs, ``ManualLimits`` fills in ranges
(and the grind guide, when the model has one), and two model attributes
cover the rest — ``brews_tea`` (default True) and ``recipe_overrides``
(the per-brew overrides its recipe run honours; default: all of them).
"""
from __future__ import annotations

from typing import Any

from custom_components.xb.machine import Capability

# Every per-brew override xbloom__execute_recipe can take.
ALL_RECIPE_OVERRIDES = frozenset(
    {
        "grind_size", "rpm", "dose_g", "ratio", "temperature_c", "cup_type",
        "bypass", "pour_overrides",
    }
)


def _join(items: list[str], conjunction: str = "and") -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]


def _intro(caps: frozenset, brews_tea: bool) -> str:
    if not caps:
        return (
            "You can check an xBloom coffee machine's status. It can't be "
            "controlled from here yet — no brewing, grinding, pouring or "
            "recipes; tell the user to use the machine or the xBloom app."
        )
    missing = []
    if Capability.RECIPES not in caps:
        missing.append("saved recipes")
    elif not brews_tea:
        missing.append("tea brewer")
    if Capability.EASY_SLOTS not in caps:
        missing.append("Easy Mode slots")
    if Capability.TARE not in caps:
        missing.append("scale")
    text = "You can control an xBloom coffee machine."
    if missing:
        text += f" It has no {_join(missing, 'or')} here — don't offer them."
    return text


_STATUS = (
    "Use xbloom__get_status to check whether the machine is reachable and "
    "what it is doing (temperature, weight and brew state where it reports "
    "them)."
)

_RECIPES = (
    "LOCAL RECIPES are the source of truth — what the Recipe dropdown "
    "shows and what you brew from. Use xbloom__list_recipes to see them "
    "(optionally filtered by a name query). Use xbloom__get_recipe to read "
    "one recipe's full detail (grind and each pour's volume / flow rate / "
    "pattern) before tweaking it. Every tool that takes a `recipe` "
    "argument accepts the recipe's local uid, cloud table id, share "
    "URL/id, or exact name — prefer the uid from xbloom__list_recipes when "
    "you have it. Use xbloom__create_recipe to build a new local recipe "
    "from scratch, xbloom__edit_recipe to change fields of an existing one "
    "(pass only the fields to change; a full pours list replaces the "
    "pours), and xbloom__delete_recipe to remove one locally (a cloud "
    "copy, if any, is untouched). Deleting is destructive — you MUST ask "
    "the user to confirm which recipe before passing confirmed=true."
)


def _manual(caps: frozenset, limits: Any) -> str:
    parts = []
    if Capability.POUR in caps:
        (t_low, t_high), (v_low, v_high) = limits.temperature_c, limits.volume_ml
        boiling = " or boiling=true for boiling water" if limits.boiling_c else ""
        parts.append(
            f"Use xbloom__pour for a manual pour of {v_low}–{v_high} ml at "
            f"{t_low}–{t_high} °C{boiling}."
        )
    if Capability.GRIND in caps:
        low, high = limits.grind_size
        rpm = " and RPM" if Capability.GRIND_SPEED in caps else ""
        parts.append(
            f"Use xbloom__grind for a manual grind with a grind size on this "
            f"machine's scale ({low}=finest to {high}=coarsest){rpm}."
        )
    if {Capability.POUR, Capability.GRIND} <= caps:
        parts.append(
            "xbloom__grind and xbloom__pour are separate manual actions "
            "(grind does not pour, pour does not grind)"
            + (
                "; use xbloom__execute_recipe instead to do both in one "
                "recipe-driven brew."
                if Capability.RECIPES in caps
                else "."
            )
        )
    return " ".join(parts)


def _two_phase(caps: frozenset, arms_on_machine: bool) -> str:
    tools = [
        f"xbloom__{name}"
        for name, cap in (
            ("grind", Capability.GRIND),
            ("pour", Capability.POUR),
            ("execute_recipe", Capability.RECIPES),
        )
        if cap in caps
    ]
    if not tools:
        return ""
    first = (
        "the first call ARMS the machine (it opens the matching page or start "
        "prompt showing the settings without starting)"
        if arms_on_machine
        else "the first call only prepares the request (nothing starts on "
        "the machine yet)"
    )
    return (
        f"{_join(tools)} {'are all' if len(tools) > 1 else 'is'} two-phase: "
        f"{first}, then you ask the user to confirm and call the same tool "
        "again with confirmed=true to start; if the user declines, call "
        "xbloom__cancel. Never pass confirmed=true on a first call — it only "
        "starts an operation that is still armed, otherwise it just arms again."
    )


_CANCEL = (
    "Use xbloom__cancel to back out of an armed operation or stop a running "
    "grind/pour/brew; if it reports something can't be stopped, tell the user."
)
_TARE = "Use xbloom__tare_scale to zero the scale."
_CALIBRATE = (
    "Use xbloom__calibrate_grinder to run the grinder's calibration (it "
    "runs on its own) when the user reports grind sizes seem off or asks to "
    "(re)calibrate."
)


def _execute(overrides: frozenset, brews_tea: bool) -> str:
    phrases = []
    if "grind_size" in overrides or "rpm" in overrides:
        keys = [k for k in ("grind_size", "rpm") if k in overrides]
        phrases.append(f"{' and/or '.join(keys)} (coffee recipes only)")
    if {"dose_g", "ratio"} <= overrides:
        phrases.append(
            "dose_g and/or ratio (pour volumes rescale so total water stays "
            "dose × ratio)"
        )
    if "temperature_c" in overrides:
        phrases.append("temperature_c (every pour's water temperature)")
    if "cup_type" in overrides:
        phrases.append("cup_type")
    if "pour_overrides" in overrides:
        phrases.append(
            "pour_overrides (per-pour volume / flow_rate / pattern keyed by "
            "0-based pour_index from xbloom__get_recipe)"
        )
    text = "Use xbloom__execute_recipe to run a saved recipe."
    if phrases:
        text += (
            " To run it with adjustments for this brew only, pass "
            + ", ".join(phrases[:-1])
            + (", and/or " if len(phrases) > 1 else "")
            + phrases[-1]
            + ". Overrides never change the stored recipe — use "
            "xbloom__edit_recipe for permanent changes. Only override what "
            "the user asked to change."
        )
    items = "beans or tea leaves" if brews_tea else "beans"
    text += (
        f" When xbloom__execute_recipe arms a recipe it returns a checklist "
        f"({items}, dripper, paper filter, cup — only the items that recipe "
        "needs). Ask the user about every item and pass confirmed=true only "
        "after they confirm all of them. Pass any overrides on the arming call."
    )
    return text


_EASY_SLOTS = (
    "Use xbloom__write_easy_slot to store a recipe on one of the machine's "
    "three onboard Easy Mode slots (A/B/C) so the user can run it from "
    "the device without Home Assistant — it does not brew anything. A "
    "share URL that isn't a local recipe yet is imported automatically first."
)
_CONNECTION = (
    "Every tool reaches the machine on its own — you do not need a separate "
    "connect step. Only tell the user about the connection if a tool "
    "reports the machine unreachable, and follow that tool's instruction."
)


def _grind_guide(limits: Any, brews_tea: bool) -> str:
    guide = getattr(limits, "grind_guide", None)
    if not guide:
        return ""
    low, high = limits.grind_size
    ranges = ", ".join(f"{method} {a}–{b}" for method, a, b in guide)
    text = (
        f"GRIND SIZE REFERENCE — recipe.grind_size uses this machine's scale "
        f"({low}=finest, {high}=coarsest). Recommended ranges per brew method: "
        f"{ranges}. When the user asks what grind to use or wants advice on "
        "tuning a recipe, pick a value inside the matching range (start "
        "mid-range and adjust finer for slower extraction or coarser for "
        "faster)."
    )
    if brews_tea:
        text += " Tea recipes do not grind, so grind_size is ignored."
    return text


_CLOUD = (
    "CLOUD BOUNDARY: use xbloom__import_cloud_recipe to save a recipe from "
    "an xBloom share URL or id (e.g. from the official app's Share button, "
    "or a collective.xbloom.com/recipe link) as a local recipe — no xBloom "
    "account is needed. Use xbloom__export_recipe to push a local recipe to "
    "the user's xBloom cloud account (visible in the official app) and get "
    "a share link; re-exporting updates the same cloud recipe in place. "
    "Without a configured account nothing is uploaded and no link is "
    "returned — tell the user they can add one under Settings > Devices & "
    "Services > xBloom > Configure. Cloud recipes cannot be deleted from "
    "here — that's done in the official app. "
    "xbloom__search_collective_recipes browses xBloom's public community "
    "recipe hub (collective.xbloom.com) — no account needed. Use it when "
    "the user wants to discover new recipes shared by xBloom or other "
    "users (by keyword, coffee/tea category, origin, roast, flavor notes, "
    "etc.) rather than manage their own saved recipes. Results include a "
    "share_url — pass that to xbloom__import_cloud_recipe to save one locally."
)


def build_prompt(coordinator: Any) -> str:
    """The API prompt for one machine."""
    caps = frozenset(coordinator.capabilities)
    # A status-only machine (no controls) has no manual limits.
    limits = getattr(coordinator, "limits", None)
    brews_tea = bool(getattr(coordinator, "brews_tea", True))
    recipes = Capability.RECIPES in caps
    overrides = frozenset(getattr(coordinator, "recipe_overrides", ALL_RECIPE_OVERRIDES))
    parts = [
        _intro(caps, brews_tea),
        _STATUS,
        _RECIPES if recipes else "",
        _manual(caps, limits),
        _two_phase(caps, bool(getattr(coordinator, "arming_buttons", False))),
        _CANCEL if Capability.CANCEL in caps else "",
        _TARE if Capability.TARE in caps else "",
        _CALIBRATE if Capability.CALIBRATE_GRINDER in caps else "",
        _execute(overrides, brews_tea) if recipes else "",
        _EASY_SLOTS if Capability.EASY_SLOTS in caps else "",
        _CONNECTION,
        _grind_guide(limits, brews_tea) if recipes else "",
        _CLOUD if recipes else "",
    ]
    return " ".join(part for part in parts if part)
