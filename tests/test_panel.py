"""Sidebar panel contract — panel.py's payload and the JS bundle's entity keys.

The panel addresses entities as ``"<platform>.<translation_key>"`` (the map
``xbloom/machines`` builds from the entity registry), so a renamed
translation key would silently blank a control. Pin every key the bundle
references against strings.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.xb import panel
from custom_components.xb.models.j15 import machine_color, machine_photo
from tests.machine_fakes import FakeStudio

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "xb"
JS = (PKG / "panel" / "frontend" / "xbloom-panel.js").read_text()
PLATFORMS = ("binary_sensor", "button", "event", "number", "select", "sensor", "switch", "update")


def test_panel_entity_keys_exist():
    """Every key the panel reads is a real entity key, or a generic key some
    model maps onto its own entity (panel_entity_aliases) whose target is
    real."""
    from custom_components.xb.models.j20.coordinator import J20Coordinator

    strings = json.loads((PKG / "strings.json").read_text())["entity"]

    def real(key: str) -> bool:
        domain, name = key.split(".")
        return name in strings.get(domain, {})

    aliases = J20Coordinator.panel_entity_aliases
    assert all(real(target) for target in aliases.values())
    pattern = re.compile(rf'"((?:{"|".join(PLATFORMS)})\.[a-z0-9_]+)"')
    keys = set(pattern.findall(JS))
    assert keys, "no entity keys found in the panel bundle"
    missing = sorted(k for k in keys if not real(k) and k not in aliases)
    assert not missing, f"panel references unknown entity keys: {missing}"


def test_panel_registration_constants():
    assert panel.PANEL_ICON == "mdi:coffee-maker"
    assert (PKG / "panel" / "frontend" / panel._PANEL_FILE).is_file()
    deps = json.loads((PKG / "manifest.json").read_text())["dependencies"]
    for dep in ("frontend", "http", "panel_custom", "websocket_api"):
        assert dep in deps


def test_machine_payload_maps_entities_and_full_recipes():
    entry = SimpleNamespace(title="Studio", data={"mac_address": "AA:BB"}, options={})
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    coordinator = FakeStudio(
        recipes={
            "Daily": {"uid": "u1", "pours": [{"volume_ml": 200}]},
            "Gone": None,  # YAML tombstone — never shown
        },
        selected_recipe="Daily",
        mac_address="AA:BB",
        cloud_login_configured=False,
        machine_cover_version=None,
        easy_slot_contents=lambda s: {"uid": "u1", "name": "Daily"} if s == "A" else None,
        data={"serial_number": "J15A02B4CV030"},
        machine_name="xBloom Studio 4CV030",
    )
    entries = [
        SimpleNamespace(domain="sensor", translation_key="state",
                        entity_id="sensor.xbloom_state", disabled_by=None),
        SimpleNamespace(domain="button", translation_key="grind",
                        entity_id="button.xbloom_grind", disabled_by="user"),
        SimpleNamespace(domain="sensor", translation_key=None,
                        entity_id="sensor.other", disabled_by=None),
    ]
    with patch.object(panel.er, "async_get"), patch.object(
        panel.er, "async_entries_for_config_entry", return_value=entries
    ), patch.object(
        panel, "async_sign_path", side_effect=lambda h, path, ttl: f"{path}&authSig=x"
    ) as sign:
        payload = panel._machine_payload(hass, "entry1", coordinator)

    assert payload["entities"] == {"sensor.state": "sensor.xbloom_state"}
    assert payload["recipes"] == [
        {"uid": "u1", "pours": [{"volume_ml": 200}], "name": "Daily"}
    ]
    assert payload["easy_slots"]["A"] == {"uid": "u1", "name": "Daily"}
    assert payload["easy_slots"]["B"] is None
    assert payload["title"] == "Studio"
    # The header shows the device name, not the entry title ("xBloom (MAC)").
    assert payload["name"] == "xBloom Studio 4CV030"
    assert payload["mac"] == "AA:BB"
    # Serial "J15A02…" → the app's white-machine code; nothing picked yet.
    assert payload["detected_color"] == "moonlight_white"
    # Signed, so the photo view can require auth while an <img> loads it.
    assert payload["photo_url"] == "/api/xbloom/machine_photo/entry1?v=02-None&authSig=x"
    sign.assert_called_once_with(
        hass, "/api/xbloom/machine_photo/entry1?v=02-None", panel._PHOTO_URL_TTL
    )
    assert panel.MachinePhotoView.requires_auth is True


def test_machine_payload_for_a_model_without_recipes():
    """An Original-like machine: capability-gated fields stay empty and its
    own entity keys are aliased onto the panel's generic ones."""
    from custom_components.xb.models.j20.coordinator import J20Coordinator

    entry = SimpleNamespace(title="Original", data={}, options={})
    hass = MagicMock()
    hass.config.country = "US"
    hass.config_entries.async_get_entry.return_value = entry
    from custom_components.xb.machine import Capability

    machine = SimpleNamespace(
        capabilities=J20Coordinator.capabilities - {Capability.RECIPES},
        panel_entity_aliases=J20Coordinator.panel_entity_aliases,
        machine_label="SN1",
    )
    entries = [
        SimpleNamespace(domain="number", translation_key="j20_volume",
                        entity_id="number.original_volume", disabled_by=None),
    ]
    with patch.object(panel.er, "async_get"), patch.object(
        panel.er, "async_entries_for_config_entry", return_value=entries
    ):
        payload = panel._machine_payload(hass, "o1", machine)

    assert payload["entities"]["number.volume"] == "number.original_volume"
    assert payload["capabilities"] == sorted(machine.capabilities)
    assert payload["recipes"] == [] and payload["easy_slots"] == {}
    assert payload["photo_url"] is None and payload["arm_buttons"] is False
    assert payload["mac"] == "SN1"
    # No device name of its own: the header falls back to the entry title.
    assert payload["name"] == "Original"


def test_color_from_serial_follows_the_official_app():
    """Device.getHomeImageRes (app 2.2.5): serialNumber[4:6] 02 white,
    03 green, 04 gold, 05 twilight, anything else black."""
    assert machine_color.color_from_serial("J15A01B4CV030") == "midnight_black"
    assert machine_color.color_from_serial("J15A02B4CV030") == "moonlight_white"
    assert machine_color.color_from_serial("J15A03B4CV030") == "sage_green_green"
    assert machine_color.color_from_serial("J15A04B4CV030") == "sage_green_gold"
    assert machine_color.color_from_serial("J15A05B4CV030") == "twilight"
    assert machine_color.color_from_serial("J15A07B4CV030") is None
    assert machine_color.color_from_serial("J15") is None
    assert machine_color.color_from_serial(None) is None


def test_unmapped_colours_are_placeholders_never_matched():
    """Offline Blue has no code in the app yet, so no serial can resolve
    to it by accident."""
    known = {c: code for c, code in machine_color.MACHINE_COLOR_CODES.items() if code}
    assert known == {
        "midnight_black": "01",
        "moonlight_white": "02",
        "sage_green_green": "03",
        "sage_green_gold": "04",
        "twilight": "05",
    }
    for code in ("00", "06", "99"):
        assert machine_color.color_from_serial(f"J15A{code}B4CV030") is None


def test_store_photo_keys_match_machine_colors():
    assert tuple(machine_photo.STORE_PHOTOS) == machine_color.MACHINE_COLORS


def test_recipe_cup_type_is_escaped():
    """cup_type is a free string in RECIPE_SCHEMA and _t() returns unknown
    keys as-is, so the badge must escape it — else a recipe saved with HTML
    in cup_type runs script in every viewer's panel."""
    assert "${this._t(`cup_${r.cup_type}`)}" not in JS
    assert JS.count("${esc(this._t(`cup_${r.cup_type}`))}") == 2


def test_panel_loads_the_machine_photo_from_ha_only():
    """Offline use: the panel never points an <img> at the internet; HA
    serves the cached photo (machine_photo.py)."""
    assert "cdn.shopify.com" not in JS
    assert "const machineImage = (m) => m?.photo_url" in JS


def _run_cloud_search(coordinator, msg):
    """Drive ws_cloud_search's coroutine directly (async_response hands it to
    hass.async_create_background_task, captured here)."""
    import asyncio

    from custom_components.xb.const import DATA_COORDINATOR, DOMAIN

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry1": {DATA_COORDINATOR: coordinator}}} if coordinator else {}
    connection = MagicMock()
    panel.ws_cloud_search(hass, connection, {"id": 7, "sort": "likes", "page": 1, **msg})
    coro = hass.async_create_background_task.call_args.args[0]
    asyncio.run(coro)
    return connection


def test_cloud_search_passes_filters_and_page():
    seen = {}

    async def search(**kwargs):
        seen.update(kwargs)
        return {"success": True, "list": [{"name": "A"}], "page_index": 2,
                "total_page": 5, "total": 42}

    coordinator = FakeStudio(async_search_collective_recipes=search)
    conn = _run_cloud_search(
        coordinator, {"entry_id": "entry1", "keyword": "kenya", "src": "official", "page": 2}
    )
    assert seen == {"keyword": "kenya", "category": None, "src": "official",
                    "sort": "likes", "sort_direction": "desc", "page": 2}
    conn.send_result.assert_called_once_with(
        7, {"recipes": [{"name": "A"}], "page": 2, "total_page": 5, "total": 42}
    )


def test_cloud_search_errors():
    async def failing(**kwargs):
        return {"success": False, "message": "down"}

    conn = _run_cloud_search(
        FakeStudio(async_search_collective_recipes=failing), {"entry_id": "entry1"}
    )
    conn.send_error.assert_called_once_with(7, "search_failed", "down")

    conn = _run_cloud_search(None, {"entry_id": "missing"})
    assert conn.send_error.call_args.args[1] == "not_found"


def test_collective_summary_carries_cover_image():
    from custom_components.xb.cloud.client import _collective_result_to_summary

    row = {"communityRecipeId": 1, "recipeName": "Legacy",
           "imageUrl": "https://tbdprodpic.s3.us-east-1.amazonaws.com/x.png"}
    assert _collective_result_to_summary(row, {})["image_url"] == row["imageUrl"]
    assert _collective_result_to_summary({}, {})["image_url"] is None


def test_maintenance_guides_have_the_same_steps_in_every_language():
    """Scale calibration and descaling have no BLE command, so the panel
    shows their steps; each locale must list the same number of them."""
    for key in ("scale_cal_steps", "descale_steps"):
        blocks = re.findall(rf"{key}: \[(.*?)\n    \],", JS, re.S)
        counts = {len(re.findall(r'^\s*"', b, re.M)) for b in blocks}
        assert len(blocks) == 2 and counts == {6}, (key, len(blocks), counts)
    assert '_maintGuideRow("calibrate_scale", "calibrating_scale", "scale_cal_steps")' in JS
    assert '_maintGuideRow("descale", "descaling", "descale_steps")' in JS


def test_panel_calls_this_integrations_services():
    """Found 2026-11-19: after the domain became ``xb`` the panel still
    called ``xbloom.<service>`` — every recipe brew/edit from it failed."""
    from custom_components.xb.const import DOMAIN

    calls = set(re.findall(r'this\._call\("([a-z_]+)", service', JS))
    assert calls == {DOMAIN}
