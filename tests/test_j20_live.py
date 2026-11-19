"""Original (J20) live updates and offline readings.

The WebSocket (AppWsManager / WsManager, app 2.2.5) pushes
``{device_id, state: {reported: {...changed fields}}}`` for every machine on
the account; the app ignores other machines' and ``type`` messages.
Unverified on hardware.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant.core")

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

