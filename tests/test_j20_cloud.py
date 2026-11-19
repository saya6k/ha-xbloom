"""Pure-logic tests for the XBloom Original (J20) cloud client.

No network and no HA instance: the signed-header vector and the shadow
mapping are the pieces that can go wrong silently.
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    is_original_device,
    parse_shadow,
    signed_headers,
)
from custom_components.xb.models.j20.values import (
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
    coordinator.async_refresh = AsyncMock()
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


def test_original_is_status_only() -> None:
    """Until an owner confirms the controls on a real machine, nothing here
    may write to it: no capabilities, and no command path in the client or
    the coordinator."""
    from custom_components.xb.models.j20.coordinator import J20Coordinator

    assert J20Coordinator.capabilities == frozenset()
    for name in ("async_send_desired", "async_iot_data", "async_add_machine"):
        assert not hasattr(J20CloudClient, name)
    for name in ("async_grind", "async_pour", "async_stop", "_send", "async_cancel"):
        assert not hasattr(J20Coordinator, name)


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
