"""Pure-logic tests for the XBloom Original (J20) cloud client.

No network and no HA instance: the signed-header vector, the shadow mapping,
and the command envelope are the pieces that can go wrong silently.
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.xb.models.j20.client import (
    J20_API_BASE,
    J20_APP_ID,
    J20_APP_SECRET,
    J20_PRODUCT_ID,
    J20ApiError,
    J20AuthError,
    J20CloudClient,
    command_payload,
    is_original_device,
    parse_shadow,
    signed_headers,
)
from custom_components.xb.models.j20.values import (
    GRIND_SIZE_DEFAULT,
    build_bruw_curve,
    warning_active,
    warning_state,
    work_mode_state,
)


def test_signed_headers_match_app_interceptor() -> None:
    headers = signed_headers("project-token", now=1_700_000_000, nonce="abc123")
    expected = hashlib.md5(
        f"{J20_APP_ID},{J20_APP_SECRET},abc123,1700000000".encode()
    ).hexdigest().upper()
    assert headers["sign"] == expected
    assert headers["ts"] == "1700000000"
    assert headers["nonce"] == "abc123"
    assert headers["appid"] == J20_APP_ID
    assert headers["platform"] == "android"
    assert headers["Authorization"] == "token project-token"


def test_command_payload_wraps_shadow_twice() -> None:
    body = command_payload("dev-1", {"grinding_start": 1, "setting_grind_size": 42})
    assert body == {
        "device_id": "dev-1",
        "product_id": J20_PRODUCT_ID,
        "desired": {
            "state": {
                "desired": {"grinding_start": 1, "setting_grind_size": 42}
            }
        },
    }
    assert "home_id" not in body


def test_is_original_device() -> None:
    assert is_original_device({"theType": 2})
    assert is_original_device({"theType": "2"})
    assert is_original_device({"productId": J20_PRODUCT_ID})
    assert not is_original_device({"theType": 1})
    assert not is_original_device({})


def test_parse_shadow_full() -> None:
    data = {
        "is_online": True,
        "work_mode": 7,
        "warning": "162",
        "capsule_rfid_tag": "ABCD1234",
        "disp_reserve": 2,
        "DM_SN": "J20-0001",
        "multimcuotainfo": {
            "mcu_info": [
                {"mcu_name": "PowerMCU", "mcu_version": "3.2.1"},
                {"mcu_name": "PannelMCU", "mcu_version": "3.2.0"},
                {"mcu_name": "", "mcu_version": "ignored"},
            ]
        },
    }
    parsed = parse_shadow(data)
    assert parsed["online"] is True
    assert parsed["work_mode"] == 7
    assert parsed["warning"] == 162
    assert parsed["disp_reserve"] == 2
    assert parsed["pod"] == "ABCD1234"
    # The shadow has no grinding flag; it comes from work_mode (isJ20Grinding).
    assert parsed["grinding"] is True
    assert "calibrating" not in parsed
    assert parsed["serial_number"] == "J20-0001"
    assert parsed["mcu_versions"] == {
        "PowerMCU": "3.2.1",
        "PannelMCU": "3.2.0",
    }


def test_parse_shadow_empty_pod_and_missing_fields() -> None:
    parsed = parse_shadow({"capsule_rfid_tag": "IF0002"})
    assert parsed["pod"] is None
    assert parsed["online"] is False
    assert parsed["work_mode"] is None
    assert parsed["warning"] is None
    assert parsed["disp_reserve"] is None
    assert parsed["mcu_versions"] == {}
    assert parsed["grinding"] is None


@pytest.mark.parametrize(
    ("work_mode", "grinding"),
    [(4, True), (7, True), (11, True), (13, True), (5, False), (6, False)],
)
def test_grinding_follows_app_work_modes(work_mode, grinding) -> None:
    assert parse_shadow({"work_mode": work_mode})["grinding"] is grinding


class _FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def json(self, content_type: str | None = None) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


_NETWORK_DOWN = object()


class _FakeSession:
    def __init__(self, payloads: list[object]) -> None:
        self._payloads = list(payloads)
        self.requests: list[dict] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        payload = self._payloads.pop(0)
        if payload is _NETWORK_DOWN:
            raise aiohttp.ClientConnectionError("down")
        if isinstance(payload, _FakeResponse):
            return payload
        return _FakeResponse(payload)


def test_401_without_json_body_is_a_token_rejection() -> None:
    """Some gateways answer an expired token with a bare 401 (HTML/empty
    body); that must lead to re-auth, not to 'cloud unreachable'."""
    session = _FakeSession([_FakeResponse(ValueError("not json"), status=401)])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    client.project_token = "expired"
    with pytest.raises(J20AuthError):
        asyncio.run(client.async_get_shadow("dev-1", J20_PRODUCT_ID))


def test_machine_list_failure_is_an_error_not_an_empty_account() -> None:
    session = _FakeSession([_NETWORK_DOWN, _NETWORK_DOWN])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    client.token, client.project_token, client.member_id = "t", "p", 1
    with pytest.raises(J20ApiError):
        asyncio.run(client.async_list_devices())


def test_login_extracts_all_tokens() -> None:
    session = _FakeSession([
        {
            "result": "success",
            "token": "recipe-token",
            "projectToken": "project-token",
            "projectRefreshToken": "refresh-token",
            "member": {"tableId": 7},
        }
    ])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    assert asyncio.run(client.async_login("a@b.c", "pw")) is True
    assert client.token == "recipe-token"
    assert client.project_token == "project-token"
    assert client.refresh_token == "refresh-token"
    assert client.member_id == 7
    assert session.requests[0]["url"].endswith("/tMemberLogin.thtml")
    assert session.requests[0]["json"]["email"] == "a@b.c"


def test_login_rejected_returns_false() -> None:
    session = _FakeSession([{"result": "fail", "info": "wrong password"}])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    assert asyncio.run(client.async_login("a@b.c", "pw")) is False


def test_login_network_error_is_not_a_bad_password() -> None:
    client = J20CloudClient(_FakeSession([_NETWORK_DOWN]))  # type: ignore[arg-type]
    with pytest.raises(J20ApiError):
        asyncio.run(client.async_login("a@b.c", "pw"))


def test_refresh_token_matches_app_request() -> None:
    session = _FakeSession([
        {"code": 0, "data": {"access_token": "new-access", "refresh_token": "new-refresh"}}
    ])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    client.project_token = "old-access"
    client.refresh_token = "old-refresh"
    assert asyncio.run(client.async_refresh_token()) is True
    request = session.requests[0]
    assert request["method"] == "PUT"
    assert request["url"] == f"{J20_API_BASE}/api/enduser/refresh_token/"
    assert request["json"] == {"refresh_token": "old-refresh"}
    assert request["headers"]["sign"]
    assert client.project_token == "new-access"
    assert client.refresh_token == "new-refresh"


def test_refresh_token_failure_keeps_tokens() -> None:
    session = _FakeSession([{"code": 10002, "message": "expired"}])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    client.project_token = "old-access"
    client.refresh_token = "old-refresh"
    assert asyncio.run(client.async_refresh_token()) is False
    assert client.project_token == "old-access"


def test_refresh_without_refresh_token_sends_nothing() -> None:
    session = _FakeSession([])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    assert asyncio.run(client.async_refresh_token()) is False
    assert session.requests == []


def test_list_devices_ignores_non_original() -> None:
    session = _FakeSession([
        {
            "result": "success",
            "machineList": [
                {"deviceId": "studio", "theType": 1},
                {"deviceId": "original", "theType": 2},
            ],
        }
    ])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    client.token = "recipe-token"
    client.project_token = "project-token"
    client.member_id = 1
    devices = asyncio.run(client.async_list_devices())
    assert [d["deviceId"] for d in devices] == ["studio", "original"]
    assert session.requests[0]["url"].endswith("/tuMachineList.tuhtml")


def test_shadow_request_is_signed_and_targets_iot_host() -> None:
    session = _FakeSession([{"code": 0, "data": {"is_online": True}}])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    client.project_token = "project-token"
    data = asyncio.run(client.async_get_shadow("dev-1", "OTHER1"))
    assert data == {"is_online": True}
    request = session.requests[0]
    assert request["url"] == f"{J20_API_BASE}/api/device/thing_shadow/"
    # getJ20Info sends the device's own productId, not the constant.
    assert request["json"] == {"device_id": "dev-1", "product_id": "OTHER1"}
    assert request["headers"]["sign"]
    assert request["headers"]["Authorization"] == "token project-token"


def test_shadow_network_error_raises_api_error() -> None:
    client = J20CloudClient(_FakeSession([_NETWORK_DOWN]))  # type: ignore[arg-type]
    client.project_token = "project-token"
    with pytest.raises(J20ApiError):
        asyncio.run(client.async_get_shadow("dev-1", J20_PRODUCT_ID))


def test_token_rejection_raises_auth_error() -> None:
    client = J20CloudClient(_FakeSession([{"code": 10001}]))  # type: ignore[arg-type]
    client.project_token = "project-token"
    with pytest.raises(J20AuthError):
        asyncio.run(client.async_get_shadow("dev-1", J20_PRODUCT_ID))


def test_bruw_curve_matches_app_builder() -> None:
    # 225 ml = 0xE1 -> "00E1"; 93 C = 0x5D; center pattern "00".
    assert build_bruw_curve(225, 93, "center") == "FFFF100500E15D1E00"
    # 30 ml = 0x1E -> "001E"; 85 C is the RT constant 0x55; spiral "02".
    assert build_bruw_curve(30, 85, "spiral") == "FFFF1005001E551E02"
    # 500 ml = 0x1F4 -> "01F4"; 96 C is the BP constant 95 = 0x5F.
    assert build_bruw_curve(500, 96, "circular") == "FFFF100501F45F1E01"
    # Out-of-range inputs clamp to the app's seekbar limits.
    assert build_bruw_curve(1, 20, "center") == "FFFF1005001E551E00"
    assert build_bruw_curve(1000, 200, "center") == "FFFF100501F45F1E00"


def test_grind_default_is_app_value() -> None:
    assert GRIND_SIZE_DEFAULT == 15


def test_work_mode_and_warning_tables() -> None:
    assert work_mode_state(0) == "idle"
    assert work_mode_state(5) == "ready"
    assert work_mode_state(6) == "brewing"
    assert work_mode_state(9) == "sleeping"
    assert work_mode_state(13) == "grinding"
    # None (HA's own localized Unknown), never a placeholder string.
    assert work_mode_state(None) is None
    assert work_mode_state(99) is None

    assert warning_state(None) == "none"
    assert warning_state(0) == "none"
    assert warning_state(162) == "grind_setting"
    assert warning_state(169) == "water_low"
    assert warning_state(171) == "dock"
    assert warning_state(182) == "no_beans"
    assert warning_state(190) == "water_shortage"
    assert warning_state(191) == "voltage"
    assert warning_state(150) == "unknown"


@pytest.mark.parametrize("code", [166, 183, 185])
def test_app_ignored_warnings_are_not_problems(code) -> None:
    assert warning_state(code) == "none"
    assert warning_active(code) is False


def test_unmapped_warning_is_still_a_problem() -> None:
    assert warning_active(150) is True
    assert warning_active(0) is False
    assert warning_active(None) is False


# --- coordinator: re-auth order and the offline gate ------------------------


def _coordinator(*, online=True, data=None, **attrs):
    """A real J20Coordinator without DataUpdateCoordinator.__init__ (no hass):
    the methods under test only touch the attributes set here."""
    from custom_components.xb.models.j20.coordinator import J20Coordinator

    coordinator = object.__new__(J20Coordinator)
    coordinator.data = {"online": online, **(data or {})}
    coordinator._armed = None
    coordinator._armed_at = 0.0
    coordinator.grind_size = 15
    coordinator.async_refresh = AsyncMock()
    coordinator.async_update_listeners = lambda: None
    coordinator._armed_recipe_command = None
    client = attrs.get("_client")
    if client is not None and not hasattr(client, "auth_lock"):
        client.auth_lock = asyncio.Lock()
        client.project_token = getattr(client, "project_token", None)
    for name, value in attrs.items():
        setattr(coordinator, name, value)
    return coordinator


def test_machines_sharing_an_account_renew_its_token_once() -> None:
    """Two Originals on one account share a client: when both see the same
    token rejected, only the first logs in again (a second login could
    invalidate the first's new token)."""
    async def login(email, password):
        client.project_token = "new"
        return True

    client = SimpleNamespace(
        project_token="old",
        async_refresh_token=AsyncMock(return_value=False),
        async_login=AsyncMock(side_effect=login),
    )
    first = _coordinator(_client=client, _email="e", _password="p")
    second = _coordinator(_client=client, _email="e", _password="p")

    async def both():
        await first._async_reauthenticate("old")
        await second._async_reauthenticate("old")

    asyncio.run(both())
    client.async_login.assert_awaited_once()


def test_an_arm_lapses_instead_of_firing_later():
    from custom_components.xb.models.j20 import coordinator as module

    coordinator = _coordinator(async_grind=AsyncMock())
    with patch.object(module.time, "monotonic", return_value=1000.0):
        assert asyncio.run(coordinator.async_arm_grind()) is True
    later = 1000.0 + module.ARM_TIMEOUT_S + 1
    with patch.object(module.time, "monotonic", return_value=later):
        assert coordinator.armed_operation is None
        assert asyncio.run(coordinator.async_confirm_grind()) is False
    coordinator.async_grind.assert_not_awaited()


def test_a_direct_command_clears_a_pending_arm():
    """A button press (or any other command) supersedes an LLM arm."""
    client = SimpleNamespace(async_send_desired=AsyncMock())
    coordinator = _coordinator(
        _client=client, device_id="d", async_request_refresh=AsyncMock()
    )
    asyncio.run(coordinator.async_arm_pour())
    asyncio.run(coordinator.async_grind())
    assert coordinator.armed_operation is None


def test_arm_only_records_then_confirm_sends():
    """No machine-side arm page: arming sends nothing, confirming starts."""
    coordinator = _coordinator(async_grind=AsyncMock())
    assert asyncio.run(coordinator.async_arm_grind()) is True
    coordinator.async_grind.assert_not_awaited()
    assert coordinator._armed == "grind"
    assert asyncio.run(coordinator.async_confirm_grind()) is True
    coordinator.async_grind.assert_awaited_once()
    assert coordinator._armed is None


def test_confirm_without_arm_sends_nothing():
    coordinator = _coordinator(async_pour=AsyncMock())
    assert asyncio.run(coordinator.async_confirm_pour()) is False
    coordinator.async_pour.assert_not_awaited()


def test_offline_machine_cannot_be_armed():
    coordinator = _coordinator(online=False)
    assert asyncio.run(coordinator.async_arm_pour()) is False
    assert coordinator._armed is None


def test_cancel_drops_an_arm_without_sending():
    coordinator = _coordinator(data={"work_mode": 6}, async_stop=AsyncMock())
    asyncio.run(coordinator.async_arm_pour())
    asyncio.run(coordinator.async_cancel())
    assert coordinator._armed is None
    coordinator.async_stop.assert_not_awaited()


def test_cancel_stops_a_running_pour_but_not_a_grind():
    from homeassistant.exceptions import HomeAssistantError

    brewing = _coordinator(data={"work_mode": 6}, async_stop=AsyncMock())
    asyncio.run(brewing.async_cancel())
    brewing.async_refresh.assert_awaited_once()  # decides on a fresh shadow
    brewing.async_stop.assert_awaited_once()

    idle = _coordinator(data={"work_mode": 5}, async_stop=AsyncMock())
    asyncio.run(idle.async_cancel())
    idle.async_stop.assert_not_awaited()

    grinding = _coordinator(data={"work_mode": 4}, async_stop=AsyncMock())
    with pytest.raises(HomeAssistantError):
        asyncio.run(grinding.async_cancel())
    grinding.async_stop.assert_not_awaited()


def test_reauth_prefers_the_refresh_token() -> None:
    client = SimpleNamespace(
        async_refresh_token=AsyncMock(return_value=True),
        async_login=AsyncMock(return_value=True),
    )
    coordinator = _coordinator(_client=client, _email="e", _password="p")
    asyncio.run(coordinator._async_reauthenticate())
    client.async_login.assert_not_awaited()


def test_reauth_falls_back_to_login_then_fails_on_rejection() -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed

    client = SimpleNamespace(
        async_refresh_token=AsyncMock(return_value=False),
        async_login=AsyncMock(return_value=False),
    )
    coordinator = _coordinator(_client=client, _email="e", _password="p")
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coordinator._async_reauthenticate())
    client.async_login.assert_awaited_once_with("e", "p")


def test_call_retries_once_then_reports_auth_failure() -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed

    client = SimpleNamespace(
        async_refresh_token=AsyncMock(return_value=True),
        async_login=AsyncMock(return_value=True),
    )
    coordinator = _coordinator(_client=client, _email="e", _password="p")
    func = AsyncMock(side_effect=J20AuthError("rejected"))
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coordinator._async_call(func))
    assert func.await_count == 2


def test_offline_machine_gets_no_command() -> None:
    from homeassistant.exceptions import HomeAssistantError

    client = SimpleNamespace(async_send_desired=AsyncMock())
    coordinator = _coordinator(_client=client, online=False, device_id="dev-1")
    asyncio.run(coordinator.async_arm_pour())  # no-op: offline
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator._send({"grinding_start": 1}))
    client.async_send_desired.assert_not_awaited()


def test_command_auth_failure_starts_reauth() -> None:
    from homeassistant.exceptions import HomeAssistantError

    client = SimpleNamespace(
        async_send_desired=AsyncMock(side_effect=J20AuthError("rejected")),
        async_refresh_token=AsyncMock(return_value=False),
        async_login=AsyncMock(return_value=False),
    )
    entry = MagicMock()
    coordinator = _coordinator(
        _client=client, device_id="dev-1",
        _email="e", _password="p", config_entry=entry, hass=object(),
    )
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator._send({"grinding_start": 1}))
    entry.async_start_reauth.assert_called_once()


def test_reauth_account_must_own_the_machine() -> None:
    from custom_components.xb.config_flow import _async_original_owns

    def client(result):
        return SimpleNamespace(async_list_devices=AsyncMock(**result))

    owned = client({"return_value": [{"deviceId": "dev-1"}]})
    assert asyncio.run(_async_original_owns(owned, "dev-1")) is None
    other = client({"return_value": [{"deviceId": "dev-2"}]})
    assert asyncio.run(_async_original_owns(other, "dev-1")) == "original_wrong_account"
    down = client({"side_effect": J20ApiError("down")})
    assert asyncio.run(_async_original_owns(down, "dev-1")) == "original_cannot_connect"


def test_original_is_named_by_its_serial_suffix() -> None:
    """Like the Studio ("xBloom Studio 4CV030"): the panel header and the
    device use "xBloom Original <last six serial characters>"."""
    named = _coordinator(_serial="J20A01B9XY123", entry_title="xBloom Original — x")
    assert named.machine_name == "xBloom Original 9XY123"
    assert named.machine_serial == "J20A01B9XY123"
    unnamed = _coordinator(_serial="", entry_title="xBloom Original — x")
    assert unnamed.machine_name == "xBloom Original — x"
    assert unnamed.machine_serial is None


def _sw_version(info):
    """sw_version of a DeviceInfo: a dataclass since HA 2026.11, a TypedDict
    before. Returns ``None`` only when it was explicitly set to ``None``."""
    if isinstance(info, dict):
        return info.get("sw_version", "unset")
    from homeassistant.helpers.typing import UNDEFINED

    return "unset" if info.sw_version is UNDEFINED else info.sw_version


def test_unknown_firmware_leaves_sw_version_unset() -> None:
    """A shadow without mcu_versions must not clear the registry's firmware
    version (sw_version=None would)."""
    pytest.importorskip("homeassistant.core")
    unknown = _coordinator(device_id="d", _serial="", entry_title="x")
    assert _sw_version(unknown.device_info) == "unset"
    known = _coordinator(
        device_id="d", _serial="", entry_title="x",
        data={"mcu_versions": {"PannelMCU": "1.2"}},
    )
    assert _sw_version(known.device_info) == "1.2"


def test_unreachable_cloud_is_an_unreachable_update_failure() -> None:
    """An unreachable cloud raises UpdateFailedUnreachable (HA 2026.12+; plain
    UpdateFailed below it), so only the first failure is logged, at info."""
    pytest.importorskip("homeassistant.core")
    from homeassistant.helpers import update_coordinator

    expected = getattr(
        update_coordinator, "UpdateFailedUnreachable", update_coordinator.UpdateFailed
    )
    coordinator = _coordinator(
        device_id="d", product_id="p",
        _async_call=AsyncMock(side_effect=J20ApiError("down")),
    )
    with pytest.raises(expected):
        asyncio.run(coordinator._async_update_data())
