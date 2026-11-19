"""Moving a pre-3.0 install (domain ``xbloom``) over to ``xb``.

The full path (old integration running → migrate flow → restart → adoption)
was boot-tested against v2.3.0-rc.0 on HA 2026.10.0b0: entity ids, the main
device id and user-set device/child-device names survive. These pin the
pure pieces.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant.core")

from custom_components.xb.const import CONF_LEGACY_ENTRY_ID
from custom_components.xb.legacy import (
    LEGACY_DOMAIN,
    _new_identifier,
    pending_legacy_entries,
)


def test_studio_identifiers_follow_the_new_entry_id():
    assert _new_identifier(("xbloom", "OLD"), "OLD", "NEW") == ("xb", "NEW")
    assert _new_identifier(("xbloom", "OLD_grinder"), "OLD", "NEW") == ("xb", "NEW_grinder")


def test_original_identifiers_keep_their_cloud_id():
    assert _new_identifier(("xbloom", "j20_123"), "OLD", "NEW") == ("xb", "j20_123")


def test_foreign_identifiers_are_left_alone():
    assert _new_identifier(("bluetooth", "AA:BB"), "OLD", "NEW") == ("bluetooth", "AA:BB")


def _hass(old_ids, claimed):
    entries = {
        LEGACY_DOMAIN: [SimpleNamespace(entry_id=i) for i in old_ids],
        "xb": [SimpleNamespace(data={CONF_LEGACY_ENTRY_ID: i}) for i in claimed]
        + [SimpleNamespace(data={})],
    }
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: entries.get(domain, []))
    )


def test_only_unclaimed_old_entries_are_pending():
    pending = pending_legacy_entries(_hass(["a", "b"], ["a"]))
    assert [e.entry_id for e in pending] == ["b"]


def test_nothing_pending_without_an_old_install():
    assert pending_legacy_entries(_hass([], [])) == []
