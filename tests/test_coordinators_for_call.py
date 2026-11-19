"""Tests for services._coordinators_for_call.

Hardware-reported 2026-07-17: a real `xbloom.advanced_settings` call with a
real `config_entry_id` failed with "No XBloom machine matched the service
call." Root cause: HA's ConfigEntrySelector has no `multiple` option, so
`call.data["config_entry_id"]` is a bare string, not a list — the old code
did `for eid in entry_ids`, which iterates a string character-by-character,
so no single character ever matched a real config entry id. This broke
every service call that specified a target machine, for every service that
goes through this helper.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant.core")

from custom_components.xbloom.const import DATA_COORDINATOR, DOMAIN
from custom_components.xbloom.machine import Capability
from custom_components.xbloom.services import _coordinators_for_call
from tests.machine_fakes import FakeStudio


def _hass_with_entries(entries: dict):
    return SimpleNamespace(data={DOMAIN: entries})


def test_a_recipe_named_coordinator_is_not_a_machine():
    """hass.data[DOMAIN] also holds name-keyed recipe maps."""
    studio = FakeStudio()
    hass = _hass_with_entries(
        {
            "studio": {DATA_COORDINATOR: studio},
            "yaml_recipes": {DATA_COORDINATOR: {"name": DATA_COORDINATOR, "pours": []}},
        }
    )
    assert _coordinators_for_call(hass, SimpleNamespace(data={})) == [studio]


def test_machines_without_the_capability_are_never_targeted():
    """An Original (no recipes) must not receive a recipe service call."""
    studio = FakeStudio()
    original = SimpleNamespace(capabilities=frozenset({Capability.GRIND}))
    hass = _hass_with_entries(
        {"studio": {DATA_COORDINATOR: studio}, "original": {DATA_COORDINATOR: original}}
    )
    assert _coordinators_for_call(hass, SimpleNamespace(data={})) == [studio]
    targeted = SimpleNamespace(data={"config_entry_id": "original"})
    assert _coordinators_for_call(hass, targeted) == []


def test_no_config_entry_id_with_one_machine_targets_it():
    coord = FakeStudio()
    hass = _hass_with_entries({"entryA": {DATA_COORDINATOR: coord}})
    assert _coordinators_for_call(hass, SimpleNamespace(data={})) == [coord]


def test_no_config_entry_id_with_several_machines_is_refused():
    """Untargeted calls used to brew on every Studio at once."""
    from homeassistant.exceptions import HomeAssistantError

    hass = _hass_with_entries(
        {
            "entryA": {DATA_COORDINATOR: FakeStudio()},
            "entryB": {DATA_COORDINATOR: FakeStudio()},
        }
    )
    with pytest.raises(HomeAssistantError):
        _coordinators_for_call(hass, SimpleNamespace(data={}))


def test_matching_config_entry_id_returns_that_coordinator():
    coord_a, coord_b = FakeStudio(), FakeStudio()
    hass = _hass_with_entries(
        {
            "entryA": {DATA_COORDINATOR: coord_a},
            "entryB": {DATA_COORDINATOR: coord_b},
        }
    )
    call = SimpleNamespace(data={"config_entry_id": "entryB"})
    assert _coordinators_for_call(hass, call) == [coord_b]


def test_config_entry_id_is_treated_as_a_single_id_not_a_list():
    # Regression: a real 26-char ULID-style entry id must not be iterated
    # character-by-character.
    coord = FakeStudio()
    entry_id = "01KXK8PT32CSRH875VJAYHZAEC"
    hass = _hass_with_entries({entry_id: {DATA_COORDINATOR: coord}})
    call = SimpleNamespace(data={"config_entry_id": entry_id})
    assert _coordinators_for_call(hass, call) == [coord]


def test_unknown_config_entry_id_returns_empty():
    hass = _hass_with_entries({"entryA": {DATA_COORDINATOR: FakeStudio()}})
    call = SimpleNamespace(data={"config_entry_id": "does-not-exist"})
    assert _coordinators_for_call(hass, call) == []
