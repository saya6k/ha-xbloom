"""Original (J20) recipe execution — the app's RecipeDetailActivity J20 path.

Unverified on hardware: read from app 2.2.5. A start is gated like
J20RecipeExecution.evaluate, the cloud turns the recipe into a run command
(tGetRecipeMadeCmd.thtml), and the app sends that string unchanged as
/api/device/command's ``desired``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant.core")

from homeassistant.exceptions import HomeAssistantError

from custom_components.xb.models.j20.values import (
    RECIPE_LEGACY_NO_GRIND,
    RECIPE_NOT_READY,
    is_legacy_firmware,
    recipe_start_blocked,
)
from tests.test_j20_cloud import _coordinator

_RECIPE = {
    "name": "Daily",
    "cup_type": "omni_dripper",
    "grind_size": 40,
    "dose_g": 15,
    "ratio": 16,
    "bypass_volume": 20,
    "pours": [
        {"volume_ml": 40, "temperature_c": 93, "pattern": 2, "flow_rate": 3.0},
        {"volume_ml": 200, "temperature_c": 93, "pattern": 1, "flow_rate": 3.0},
    ],
}
_MCUS = {"PowerMCU": "3.2.0", "PannelMCU": "3.2.0"}


def test_firmware_window_follows_the_app():
    assert is_legacy_firmware(None, "3.2.0") is False
    assert is_legacy_firmware("3.2.0", "3.2.0") is False
    assert is_legacy_firmware("2.9.9", "3.2.0") is True
    assert is_legacy_firmware("3.2.0", "8.0.1") is True


def test_recipe_start_gate():
    # Ready to brew (5) on current firmware: go.
    assert recipe_start_blocked(5, False, "3.2.0", "3.2.0") is None
    # Just woken (1) is fine on current firmware, not on legacy.
    assert recipe_start_blocked(1, False, "3.2.0", "3.2.0") is None
    assert recipe_start_blocked(1, False, "2.0.0", "3.2.0") == RECIPE_NOT_READY
    # Busy or asleep: wait.
    assert recipe_start_blocked(6, False, "3.2.0", "3.2.0") == RECIPE_NOT_READY
    assert recipe_start_blocked(9, False, None, None) == RECIPE_NOT_READY
    # A no-grind recipe can't run on legacy firmware.
    assert recipe_start_blocked(5, True, "2.0.0", "3.2.0") == RECIPE_LEGACY_NO_GRIND


def _recipe_coordinator(*, work_mode=5, warning=None, command="CMD", **attrs):
    cloud = SimpleNamespace(
        logged_in=True,
        async_recipe_machine_command=AsyncMock(return_value=command),
    )
    client = SimpleNamespace(async_send_desired=AsyncMock(), project_token="T")
    coordinator = _coordinator(
        data={"work_mode": work_mode, "warning": warning, "mcu_versions": _MCUS},
        recipes={"Daily": _RECIPE},
        selected_recipe="Daily",
        cloud_client=cloud,
        _client=client,
        _cloud_email="a@b.c",
        _cloud_password="pw",
        device_id="DEV",
        async_request_refresh=AsyncMock(),
        **attrs,
    )
    return coordinator, cloud, client


def test_arm_fetches_the_run_command_and_confirm_sends_it_unchanged():
    coordinator, cloud, client = _recipe_coordinator()
    asyncio.run(coordinator.async_arm_recipe())
    assert coordinator.armed_operation == "recipe"
    client.async_send_desired.assert_not_awaited()
    sent_recipe, device_id, product_id = cloud.async_recipe_machine_command.await_args.args
    assert (device_id, product_id) == ("DEV", "53DDY4")
    # The run command has no bypass water.
    assert sent_recipe["bypass_volume"] == 0

    assert asyncio.run(coordinator.async_confirm_recipe()) is True
    client.async_send_desired.assert_awaited_once_with("DEV", "CMD")
    assert coordinator.armed_operation is None


def test_overrides_apply_to_this_brew_only():
    coordinator, cloud, _client = _recipe_coordinator()
    asyncio.run(
        coordinator.async_arm_recipe(
            overrides={"dose_g": 20, "temperature_c": 90},
            pour_overrides=[{"pour_index": 1, "pattern": 0}],
        )
    )
    sent = cloud.async_recipe_machine_command.await_args.args[0]
    assert sum(p["volume_ml"] for p in sent["pours"]) == 320  # 20 g × 16
    assert {p["temperature_c"] for p in sent["pours"]} == {90}
    assert sent["pours"][1]["pattern"] == 0
    assert _RECIPE["dose_g"] == 15 and _RECIPE["pours"][1]["pattern"] == 1


@pytest.mark.parametrize(
    ("work_mode", "warning"),
    [(6, None), (9, None), (5, 190)],
)
def test_arm_refuses_when_the_machine_cant_start(work_mode, warning):
    coordinator, cloud, _client = _recipe_coordinator(work_mode=work_mode, warning=warning)
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator.async_arm_recipe())
    cloud.async_recipe_machine_command.assert_not_awaited()
    assert coordinator.armed_operation is None


def test_tea_is_refused():
    coordinator, _cloud, _client = _recipe_coordinator()
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator.async_arm_recipe(overrides={"cup_type": "tea"}))


def test_no_run_command_means_no_arm():
    coordinator, _cloud, _client = _recipe_coordinator(command=None)
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator.async_arm_recipe())
    assert coordinator.armed_operation is None


def test_cancel_drops_an_armed_recipe():
    coordinator, _cloud, client = _recipe_coordinator()
    asyncio.run(coordinator.async_arm_recipe())
    asyncio.run(coordinator.async_cancel())
    assert coordinator.armed_operation is None
    assert asyncio.run(coordinator.async_confirm_recipe()) is False
    client.async_send_desired.assert_not_awaited()


def test_a_string_command_goes_out_as_desired():
    from custom_components.xb.models.j20.client import J20CloudClient

    client = J20CloudClient(SimpleNamespace())
    client.project_token = "T"
    client._signed_post = AsyncMock(return_value={"code": 0})
    asyncio.run(client.async_send_desired("DEV", '{"state":{}}'))
    path, body = client._signed_post.await_args.args
    assert path == "/api/device/command"
    assert body == {"device_id": "DEV", "product_id": "53DDY4", "desired": '{"state":{}}'}


def test_the_cloud_builds_the_command_from_the_recipe_fields():
    from custom_components.xb.cloud.client import XBloomCloudClient

    cloud = XBloomCloudClient(SimpleNamespace())
    cloud.member_id, cloud.token = 7, "TOK"
    cloud._post_encrypted = AsyncMock(return_value={"result": "success", "desired": "CMD"})
    assert asyncio.run(cloud.async_recipe_machine_command(_RECIPE, "DEV", "53DDY4")) == "CMD"
    endpoint, payload = cloud._post_encrypted.await_args.args
    assert endpoint == "tGetRecipeMadeCmd.thtml"
    assert payload["device_id"] == "DEV" and payload["product_id"] == "53DDY4"
    assert payload["theName"] == "Daily" and payload["grandWater"] == 16
    assert payload["isSetGrinderSize"] == 1 and payload["grinderSize"] == 40
    assert payload["memberId"] == 7 and payload["token"] == "TOK"
    assert "pourDataJSONStr" in payload


def test_tea_recipes_are_hidden_from_a_machine_without_a_tea_brewer():
    from custom_components.xb.recipes.store import RecipeStoreMixin

    original = SimpleNamespace(brews_tea=False)
    allowed = RecipeStoreMixin._recipe_allowed
    assert allowed(original, {"cup_type": "omni_dripper"}) is True
    assert allowed(original, {"cup_type": "tea"}) is False
    assert allowed(SimpleNamespace(brews_tea=True), {"cup_type": "tea"}) is True
