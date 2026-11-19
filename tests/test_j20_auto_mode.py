"""Original (J20) auto mode — one recipe on the machine's start button.

From app 2.2.5 (EasyModeJ20Fragment / EasyModeJ20ViewModel), unverified on
hardware: tuAutoMode reads the record, the recipe's auto-mode command
(tGetAutoModeRecipeMadeCmd, xid IF0002) goes to the machine, tuAutoModeSync
records the recipe; reset = tuAutoModeInit's factory recipe → machine →
tuAutoModeReset.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant.core")

from homeassistant.exceptions import HomeAssistantError

from tests.test_j20_cloud import _coordinator

_RECIPE = {
    "uid": "u1", "name": "Daily", "cup_type": "omni_dripper", "grind_size": 40,
    "dose_g": 15, "ratio": 16, "cloud_table_id": 77,
    "pours": [{"volume_ml": 240, "temperature_c": 93, "pattern": 2, "flow_rate": 3.0}],
}
_SNAPSHOT = {"tableId": 77, "theName": "Daily", "dose": 15, "grandWater": 16, "pourList": []}
_RECORD = {
    "result": "success",
    "autoModeDetailVo": {"tableId": 5, "scale": 2, "recipeSnapshotVo": _SNAPSHOT},
}


def _auto_coordinator(*, recipe=_RECIPE, record=_RECORD):
    cloud = SimpleNamespace(
        logged_in=True,
        async_auto_mode=AsyncMock(return_value=record),
        async_auto_mode_default=AsyncMock(return_value=record),
        async_auto_mode_reset=AsyncMock(return_value={"result": "success"}),
        async_auto_mode_sync=AsyncMock(return_value=True),
        async_recipe_machine_command=AsyncMock(return_value="AUTO-CMD"),
    )
    client = SimpleNamespace(
        async_send_desired=AsyncMock(),
        async_list_devices=AsyncMock(return_value=[{"deviceId": "DEV", "tableId": 9}]),
        project_token="T",
    )
    coordinator = _coordinator(
        data={"work_mode": 5},
        recipes={"Daily": dict(recipe)},
        selected_recipe="Daily",
        cloud_client=cloud,
        _client=client,
        _cloud_email="a@b.c",
        _cloud_password="pw",
        _serial="J20A01B9XY123",
        device_id="DEV",
        auto_mode=None,
        _machine_table_id=None,
        async_request_refresh=AsyncMock(),
    )
    return coordinator, cloud, client


def test_writing_sends_the_auto_mode_command_then_records_it():
    coordinator, cloud, client = _auto_coordinator()
    result = asyncio.run(coordinator.async_write_auto_mode("u1"))
    assert result["name"] == "Daily"
    # The machine record is looked up by serial and the account's machine id.
    cloud.async_auto_mode.assert_awaited_with("J20A01B9XY123", 9)
    _recipe, device_id, _pid = cloud.async_recipe_machine_command.await_args.args
    assert device_id == "DEV"
    assert cloud.async_recipe_machine_command.await_args.kwargs == {"auto_mode": True}
    client.async_send_desired.assert_awaited_once_with("DEV", "AUTO-CMD")
    cloud.async_auto_mode_sync.assert_awaited_once_with(5, 77, 2)
    assert coordinator.auto_mode["uid"] == "u1"


def test_a_recipe_not_on_the_account_is_exported_first():
    local = {k: v for k, v in _RECIPE.items() if k != "cloud_table_id"}
    coordinator, cloud, _client = _auto_coordinator(recipe=local)
    coordinator.async_export_recipe = AsyncMock(return_value={"success": True, "id": 88})
    asyncio.run(coordinator.async_write_auto_mode("u1"))
    coordinator.async_export_recipe.assert_awaited_once()
    assert cloud.async_auto_mode_sync.await_args.args[1] == 88


def test_nothing_is_sent_while_offline():
    coordinator, cloud, client = _auto_coordinator()
    coordinator.data["online"] = False
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator.async_write_auto_mode("u1"))
    client.async_send_desired.assert_not_awaited()
    cloud.async_auto_mode_sync.assert_not_awaited()


def test_reset_restores_the_factory_recipe():
    coordinator, cloud, client = _auto_coordinator()
    asyncio.run(coordinator.async_reset_auto_mode())
    cloud.async_auto_mode_default.assert_awaited_once()
    client.async_send_desired.assert_awaited_once_with("DEV", "AUTO-CMD")
    cloud.async_auto_mode_reset.assert_awaited_once_with("J20A01B9XY123", 9)


def test_reading_matches_the_record_to_a_local_recipe():
    coordinator, _cloud, _client = _auto_coordinator()
    asyncio.run(coordinator.async_refresh_auto_mode())
    assert coordinator.auto_mode == {
        "name": "Daily", "uid": "u1", "cloud_table_id": 77, "scale": 2, "record_table_id": 5,
    }


def test_the_cloud_sends_the_auto_mode_command_request():
    from custom_components.xb.cloud.client import XBloomCloudClient

    cloud = XBloomCloudClient(SimpleNamespace())
    cloud.member_id, cloud.token = 7, "TOK"
    cloud._post_encrypted = AsyncMock(return_value={"result": "success", "desired": "D"})
    asyncio.run(cloud.async_recipe_machine_command(_RECIPE, "DEV", "53DDY4", auto_mode=True))
    endpoint, payload = cloud._post_encrypted.await_args.args
    assert endpoint == "tGetAutoModeRecipeMadeCmd.thtml" and payload["xid"] == "IF0002"
