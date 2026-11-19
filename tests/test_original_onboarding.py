"""Original Wi-Fi onboarding over Bluetooth (models/j20/provision.py).

Built from the official app 2.2.5 (MXChip ``locklib``); no Original hardware
has run it. These pin the wire format the app uses and drive the whole
exchange against a fake machine and cloud.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

pytest.importorskip("homeassistant.core")

from custom_components.xb.models.j20 import provision
from custom_components.xb.models.j20.provision import (
    COMMAND_UUID,
    ProvisionError,
    Reassembler,
    advertised_identity,
    async_provision,
    decrypt,
    encrypt,
    split_message,
)

_KEY = "00112233445566778899aabbccddeeff"
_MAC = "a1b2c3d4e5f6"


def test_advertisement_identifies_an_original():
    payload = bytes([0x05]) + bytes.fromhex(_MAC) + b"53DDY4"
    assert advertised_identity({0x1234: payload}) == _MAC


def test_other_0922_devices_are_not_originals():
    payload = bytes([0x05]) + bytes.fromhex(_MAC) + b"LOCK01"
    assert advertised_identity({0x1234: payload}) is None
    assert advertised_identity({}) is None


def test_a_short_message_is_one_piece():
    assert split_message(b"abc") == [b"\x00abc"]


def test_a_long_message_is_split_first_middle_last():
    data = bytes(range(45))
    pieces = split_message(data)
    assert [p[0] for p in pieces] == [0x01, 0x10, 0x11]
    assert [len(p) for p in pieces] == [20, 20, 8]
    assert b"".join(p[1:] for p in pieces) == data


def test_an_exact_multiple_ends_with_an_empty_last_piece():
    # ByteUtil.StringToByte sends length/19 + 1 pieces.
    pieces = split_message(bytes(38))
    assert [p[0] for p in pieces] == [0x01, 0x10, 0x11]
    assert pieces[-1] == b"\x11"


def test_reassembly_round_trips():
    for size in (5, 19, 20, 38, 100):
        data = bytes(range(size))
        reassembler = Reassembler()
        out = [reassembler.feed(p) for p in split_message(data)]
        assert out[-1] == data
        assert all(o is None for o in out[:-1])


def test_encryption_round_trips():
    plain = b'{"type":"wifi_reply","data":{"error_code":0}}'
    sealed = encrypt(_KEY, plain)
    assert len(sealed) % 16 == 0
    assert decrypt(_KEY, base64.b64encode(sealed)) == plain


class _FakeMachine:
    """Answers the app's BLE messages the way the SDK expects."""

    def __init__(self, wifi_error: int = 0) -> None:
        self.wifi_error = wifi_error
        self.callback = None
        self.reassembler = Reassembler()
        self.wifi: dict | None = None

    async def start_notify(self, _uuid, callback):
        self.callback = callback

    def _notify(self, data: bytes) -> None:
        for piece in split_message(data):
            self.callback(None, bytearray(piece))

    async def write_gatt_char(self, uuid, data, response=True):
        assert uuid == COMMAND_UUID
        message = self.reassembler.feed(bytes(data))
        if message is None:
            return
        try:
            value = json.loads(message)
        except ValueError:
            # The encrypted Wi-Fi settings.
            self.wifi = json.loads(_decrypt_raw(message))
            reply = {"type": "wifi_reply", "data": {"error_code": self.wifi_error}}
            self._notify(base64.b64encode(encrypt(_KEY, json.dumps(reply).encode())))
            return
        if value["type"] == "random":
            assert value["data"]["random"] == "R"
            self._notify(json.dumps({"type": "random_reply", "data": {"cipher": "C"}}).encode())


def _decrypt_raw(sealed: bytes) -> bytes:
    return decrypt(_KEY, base64.b64encode(sealed))


class _FakeCloud:
    project_token = "IOT-TOKEN"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def async_iot_data(self, path, body=None, *, method="POST"):
        self.calls.append((path, body))
        if path == "/api/device/bind/code":
            return {"bind_code": "B", "activation_url": "https://act"}
        if path == "/api/device/ble/encryption/random/":
            return {"random": "R", "random_key": "RK"}
        if path == "/api/device/ble/encryption/verify/":
            assert body["cipher"] == "C" and body["random_key"] == "RK"
            return {"ble_key": _KEY}
        if path == "/api/device/bind/status":
            return {"device_id": "DEV1", "product_id": "53DDY4", "mac": _MAC}
        raise AssertionError(path)


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(provision.asyncio, "sleep", _sleep)


def test_the_whole_exchange_binds_the_machine():
    machine, cloud = _FakeMachine(), _FakeCloud()
    bound = asyncio.run(async_provision(machine, cloud, _MAC, "home", "secret"))
    assert bound["device_id"] == "DEV1"
    assert machine.wifi == {
        "data": {
            "ssid": "home",
            "activate_url": "https://act",
            "binding_code": "B",
            "password": "secret",
            "user_token": "IOT-TOKEN",
        },
        "type": "apinfo",
    }
    assert [path for path, _ in cloud.calls] == [
        "/api/device/bind/code",
        "/api/device/ble/encryption/random/",
        "/api/device/ble/encryption/verify/",
        "/api/device/bind/status",
    ]


@pytest.mark.parametrize(
    ("code", "reason"),
    [(1, "wifi_not_found"), (2, "wifi_weak"), (3, "wifi_failed")],
)
def test_wifi_errors_are_reported(code, reason):
    with pytest.raises(ProvisionError) as err:
        asyncio.run(async_provision(_FakeMachine(code), _FakeCloud(), _MAC, "home", "x"))
    assert err.value.reason == reason


def test_an_unbound_machine_times_out():
    class _NeverBound(_FakeCloud):
        async def async_iot_data(self, path, body=None, *, method="POST"):
            if path == "/api/device/bind/status":
                return {}
            return await super().async_iot_data(path, body, method=method)

    with pytest.raises(ProvisionError) as err:
        asyncio.run(async_provision(_FakeMachine(), _NeverBound(), _MAC, "home", "x"))
    assert err.value.reason == "bind_failed"
