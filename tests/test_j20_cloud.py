"""Pure-logic tests for the XBloom Original (J20) cloud client.

No network and no HA instance: the signed-header vector, the shadow mapping,
and the command envelope are the pieces that can go wrong silently.
"""
from __future__ import annotations

import asyncio
import hashlib

from custom_components.xbloom.j20.client import (
    J20_API_BASE,
    J20_APP_ID,
    J20_APP_SECRET,
    J20_PRODUCT_ID,
    J20CloudClient,
    command_payload,
    is_original_device,
    parse_shadow,
    signed_headers,
)
from custom_components.xbloom.j20.values import (
    GRIND_SIZE_DEFAULT,
    build_bruw_curve,
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
        "work_mode": 5,
        "warning": "162",
        "capsule_rfid_tag": "ABCD1234",
        "disp_reserve": 2,
        "grinding_start": 1,
        "auto_calibrate": 0,
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
    assert parsed["work_mode"] == 5
    assert parsed["warning"] == 162
    assert parsed["disp_reserve"] == 2
    assert parsed["pod"] == "ABCD1234"
    assert parsed["grinding"] is True
    assert parsed["calibrating"] is False
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
    assert parsed["calibrating"] is None


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self, content_type: str | None = None) -> dict:
        return self._payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.requests: list[dict] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(self._payloads.pop(0))


def test_login_extracts_both_tokens() -> None:
    session = _FakeSession([
        {
            "result": "success",
            "token": "recipe-token",
            "projectToken": "project-token",
            "member": {"tableId": 7},
        }
    ])
    client = J20CloudClient(session)  # type: ignore[arg-type]
    assert asyncio.run(client.async_login("a@b.c", "pw")) is True
    assert client.token == "recipe-token"
    assert client.project_token == "project-token"
    assert client.member_id == 7
    assert session.requests[0]["url"].endswith("/tMemberLogin.thtml")
    assert session.requests[0]["json"]["email"] == "a@b.c"


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
    data = asyncio.run(client.async_get_shadow("dev-1"))
    assert data == {"is_online": True}
    request = session.requests[0]
    assert request["url"] == f"{J20_API_BASE}/api/device/thing_shadow/"
    assert request["headers"]["sign"]
    assert request["headers"]["Authorization"] == "token project-token"


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
    assert work_mode_state(None) == "unknown"
    assert work_mode_state(99) == "unknown"

    assert warning_state(None) == "none"
    assert warning_state(0) == "none"
    assert warning_state(162) == "grind_setting"
    assert warning_state(169) == "water_low"
    assert warning_state(171) == "dock"
    assert warning_state(182) == "no_beans"
    assert warning_state(190) == "water_shortage"
    assert warning_state(191) == "voltage"
    assert warning_state(166) == "unknown"
