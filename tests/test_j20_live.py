"""Original (J20) live updates, offline readings and the calibrate gate.

The WebSocket (AppWsManager / WsManager, app 2.2.5) pushes
``{device_id, state: {reported: {...changed fields}}}`` for every machine on
the account; the app ignores other machines' and ``type`` messages.
Unverified on hardware.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant.core")

from homeassistant.exceptions import HomeAssistantError

from tests.test_j20_cloud import _coordinator

_SHADOW = {
    "is_online": True,
    "work_mode": 5,
    "warning": 0,
    "disp_reserve": 0,
    "capsule_rfid_tag": "IF0002",
    "DM_SN": "J20A01B9XY123",
}


def _live_coordinator():
    coordinator = _coordinator(device_id="DEV", _raw_shadow=dict(_SHADOW))
    updates = []
    coordinator.async_set_updated_data = updates.append
    return coordinator, updates


def test_a_delta_for_this_machine_is_merged_into_the_shadow():
    coordinator, updates = _live_coordinator()
    message = {"device_id": "DEV", "state": {"reported": {"work_mode": 6, "disp_reserve": 2}}}
    assert coordinator.handle_live_message(json.dumps(message)) is True
    data = updates[-1]
    assert data["work_mode"] == 6 and data["disp_reserve"] == 2
    # Untouched fields keep their last value.
    assert data["serial_number"] == "J20A01B9XY123" and data["online"] is True


@pytest.mark.parametrize(
    "message",
    [
        {"device_id": "OTHER", "state": {"reported": {"work_mode": 6}}},
        {"type": "ping"},
        {"device_id": "DEV"},
        "not json",
    ],
)
def test_other_messages_are_ignored(message):
    coordinator, updates = _live_coordinator()
    text = message if isinstance(message, str) else json.dumps(message)
    assert coordinator.handle_live_message(text) is False
    assert updates == []


def test_a_delta_without_device_id_applies_only_to_a_sole_machine():
    """The app applies an id-less delta to its one current machine; with
    several Originals on the account it can't tell which one it is for."""
    message = json.dumps({"state": {"reported": {"work_mode": 6}}})
    coordinator, updates = _live_coordinator()
    coordinator._client = SimpleNamespace(entry_ids={"a"})
    assert coordinator.handle_live_message(message) is True
    assert updates[-1]["work_mode"] == 6

    coordinator, updates = _live_coordinator()
    coordinator._client = SimpleNamespace(entry_ids={"a", "b"})
    assert coordinator.handle_live_message(message) is False
    assert updates == []


def test_nothing_is_merged_before_the_first_shadow():
    coordinator, _updates = _live_coordinator()
    coordinator._raw_shadow = None
    message = {"device_id": "DEV", "state": {"reported": {"work_mode": 6}}}
    assert coordinator.handle_live_message(json.dumps(message)) is False


def test_an_offline_machine_has_no_live_readings():
    online = _coordinator(online=True, data={"work_mode": 5})
    offline = _coordinator(online=False, data={"work_mode": 5})
    assert online.live["work_mode"] == 5
    assert offline.live == {}


def test_offline_sensors_read_unknown_not_unavailable():
    from custom_components.xb.models.j20.binary_sensor import J20ProblemBinarySensor
    from custom_components.xb.models.j20.sensor import J20StateSensor, J20WarningSensor

    coordinator = _coordinator(online=False, data={"work_mode": 5, "warning": 190})
    entry = SimpleNamespace(entry_id="e1")
    assert J20StateSensor(coordinator, entry).native_value is None
    assert J20WarningSensor(coordinator, entry).native_value is None
    assert J20ProblemBinarySensor(coordinator, entry).is_on is None


def test_calibration_waits_for_a_woken_machine():
    client = SimpleNamespace(async_send_desired=AsyncMock(), project_token="T")
    coordinator = _coordinator(
        data={"work_mode": 9}, _client=client, device_id="DEV",
        async_request_refresh=AsyncMock(),
    )
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator.async_calibrate_grinder())
    client.async_send_desired.assert_not_awaited()

    coordinator.data["work_mode"] = 5
    asyncio.run(coordinator.async_calibrate_grinder())
    client.async_send_desired.assert_awaited_once_with("DEV", {"auto_calibrate": 1})


def _listener_hass(options: dict):
    from unittest.mock import AsyncMock

    from custom_components.xb.const import DOMAIN

    reload = AsyncMock()
    hass = SimpleNamespace(
        data={DOMAIN: {"e": {"options_snapshot": dict(options)}}},
        config_entries=SimpleNamespace(async_reload=reload),
    )
    return hass, reload


def test_store_region_change_does_not_reload_the_original():
    """The panel's store region only picks web-store links; reloading would
    sign in to the cloud again."""
    import asyncio

    from custom_components.xb.models.j20 import _async_update_j20_listener

    hass, reload = _listener_hass({"telemetry_interval": 15})
    entry = SimpleNamespace(
        entry_id="e", options={"telemetry_interval": 15, "store_region": "kr"}
    )
    asyncio.run(_async_update_j20_listener(hass, entry))
    reload.assert_not_awaited()

    entry.options = {"telemetry_interval": 30, "store_region": "kr"}
    asyncio.run(_async_update_j20_listener(hass, entry))
    reload.assert_awaited_once_with("e")


def test_recipe_store_change_rebuilds_without_reloading_the_original():
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.xb.const import DATA_COORDINATOR, DOMAIN
    from custom_components.xb.models.j20 import _async_update_j20_listener

    hass, reload = _listener_hass({"store_region": "kr"})
    coordinator = MagicMock()
    hass.data[DOMAIN]["e"][DATA_COORDINATOR] = coordinator
    entry = SimpleNamespace(entry_id="e", options={"store_region": "kr", "recipes": {"A": {}}})
    asyncio.run(_async_update_j20_listener(hass, entry))
    reload.assert_not_awaited()
    coordinator._rebuild_recipes.assert_called_once()
