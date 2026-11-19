"""Wi-Fi onboarding of a factory-fresh Original over Bluetooth, as the app does.

UNVERIFIED — built from the official app 2.2.5 (``PairDeviceJ20Activity`` and
MXChip's ``com.mxchip.locklib`` SDK); no Original hardware has been tested.

The Original uses Bluetooth only to receive its Wi-Fi credentials. It
advertises the 16-bit service ``0x0922`` with manufacturer data carrying its
MAC and the product id ``53DDY4``. The exchange (BLE packets in brackets):

1. cloud ``POST /api/device/bind/code`` {product_id, mac} → bind_code, activation_url
2. cloud ``POST /api/device/ble/encryption/random/`` {product_id, mac} → random, random_key
3. [→] ``{"type": "random", "data": {"random": …}}``          plain
4. [←] ``{… "data": {"cipher": …}}``                            plain
5. cloud ``POST /api/device/ble/encryption/verify/`` {mac, product_id, cipher, random_key} → ble_key
6. [→] ``{"type": "cipher_reply", "data": {"error_code": 0}}``  plain
7. [→] ``{"data": {ssid, activate_url, binding_code, password, user_token}, "type": "apinfo"}``
       AES-128-CBC/PKCS7 with key = hex ``ble_key``, IV = sixteen ASCII "0"
8. [←] ``{"type": "wifi_reply", "data": {"error_code": 0}}``    base64 of AES-CBC
9. after 10 s, cloud ``POST /api/device/bind/status`` {mac, product_id, bind_code}
   every 3 s, ten times, until ``data.device_id`` appears.

Every BLE message is split into 19-byte pieces, each prefixed with a header
byte: 0x00 the whole message, 0x01 first, 0x10 middle, 0x11 last.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .client import J20_PRODUCT_ID, J20ApiError, J20CloudClient

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "00000922-0000-1000-8000-00805f9b34fb"
COMMAND_UUID = "0000aab1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000aab2-0000-1000-8000-00805f9b34fb"

_PIECE = 19
HEAD_SINGLE = 0x00
HEAD_FIRST = 0x01
HEAD_MIDDLE = 0x10
HEAD_LAST = 0x11
_IV = b"0" * 16

_REPLY_TIMEOUT_S = 30.0
_WIFI_REPLY_TIMEOUT_S = 60.0  # BleLockKeyCheck.UNLOCK_TIMEOUT
_BIND_FIRST_DELAY_S = 10.0
_BIND_POLL_S = 3.0
_BIND_POLLS = 10

# wifi_reply error codes (BleLockKeyCheck → Code)
_WIFI_ERRORS = {1: "wifi_not_found", 2: "wifi_weak", 3: "wifi_failed"}


class ProvisionError(Exception):
    """Onboarding stopped; ``reason`` is a config-flow abort key suffix."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def advertised_identity(manufacturer_data: dict[int, bytes]) -> str | None:
    """The machine's MAC as the cloud expects it (12 lower-case hex digits),
    or None when the advertisement isn't an Original's.

    Raw scan record bytes 16-21 are the MAC and 22-27 the ASCII product id
    (``ScanRecord.mac()`` / ``productId()``); in HA's manufacturer data,
    which drops the 2-byte company id, they sit at 1-6 and 7-12.
    """
    for payload in manufacturer_data.values():
        if len(payload) >= 13 and payload[7:13] == J20_PRODUCT_ID.encode():
            return payload[1:7].hex()
    return None


def split_message(data: bytes) -> list[bytes]:
    """``ByteUtil.StringToByte``: 19-byte pieces behind a header byte."""
    if len(data) <= _PIECE:
        return [bytes([HEAD_SINGLE]) + data]
    count = len(data) // _PIECE + 1
    pieces = []
    for index in range(count):
        chunk = data[index * _PIECE : (index + 1) * _PIECE]
        if index == 0:
            head = HEAD_FIRST
        elif index == count - 1:
            head = HEAD_LAST
        else:
            head = HEAD_MIDDLE
        pieces.append(bytes([head]) + chunk)
    return pieces


class Reassembler:
    """Joins notification pieces back into whole messages."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, packet: bytes) -> bytes | None:
        if not packet:
            return None
        head, body = packet[0], packet[1:]
        if head == HEAD_SINGLE:
            self._buffer = b""
            return body
        if head == HEAD_FIRST:
            self._buffer = body
            return None
        self._buffer += body
        if head == HEAD_MIDDLE:
            return None
        message, self._buffer = self._buffer, b""
        return message


def _key(ble_key: str) -> bytes:
    if len(ble_key) % 2:
        ble_key = "0" + ble_key
    return bytes.fromhex(ble_key)


def encrypt(ble_key: str, plaintext: bytes) -> bytes:
    """``AESUtils.encrypt``: AES-CBC, PKCS7, IV of ASCII zeros; raw bytes."""
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(ble_key)), modes.CBC(_IV)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def decrypt(ble_key: str, message: bytes) -> bytes:
    """``AESUtils.decrypt``: base64, then AES-CBC without padding removal."""
    data = base64.b64decode(message)
    decryptor = Cipher(algorithms.AES(_key(ble_key)), modes.CBC(_IV)).decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    pad = plain[-1] if plain else 0
    if 0 < pad <= 16 and plain.endswith(bytes([pad]) * pad):
        return plain[:-pad]
    return plain.rstrip(b"\x00")


def _json(message: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(message.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class _Link:
    """Whole messages over the command/notify characteristics."""

    def __init__(self, ble_client: Any) -> None:
        self._client = ble_client
        self._messages: asyncio.Queue[bytes] = asyncio.Queue()
        self._reassembler = Reassembler()

    async def async_start(self) -> None:
        await self._client.start_notify(NOTIFY_UUID, self._on_notify)

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        if (message := self._reassembler.feed(bytes(data))) is not None:
            self._messages.put_nowait(message)

    async def async_send(self, data: bytes) -> None:
        for piece in split_message(data):
            await self._client.write_gatt_char(COMMAND_UUID, piece, response=True)

    async def async_send_json(self, value: dict[str, Any]) -> None:
        await self.async_send(json.dumps(value).encode())

    async def async_receive(self, timeout: float) -> bytes:
        try:
            return await asyncio.wait_for(self._messages.get(), timeout)
        except TimeoutError as err:
            raise ProvisionError("no_reply") from err


async def _async_cloud(cloud: J20CloudClient, path: str, body: dict, reason: str) -> dict:
    try:
        return await cloud.async_iot_data(path, body)
    except J20ApiError as err:
        raise ProvisionError(reason) from err


async def _async_handshake(
    link: _Link, cloud: J20CloudClient, mac: str, random: dict[str, Any]
) -> str:
    """Steps 3-6: prove the cloud knows this machine; returns ``ble_key``."""
    await link.async_send_json({"type": "random", "data": {"random": random["random"]}})
    reply = _json(await link.async_receive(_REPLY_TIMEOUT_S)) or {}
    cipher = (reply.get("data") or {}).get("cipher")
    if not cipher:
        raise ProvisionError("handshake_failed")
    verify = await _async_cloud(
        cloud,
        "/api/device/ble/encryption/verify/",
        {
            "mac": mac,
            "product_id": J20_PRODUCT_ID,
            "cipher": cipher,
            "random_key": random.get("random_key"),
        },
        "handshake_failed",
    )
    if not (ble_key := verify.get("ble_key")):
        raise ProvisionError("handshake_failed")
    await link.async_send_json({"type": "cipher_reply", "data": {"error_code": 0}})
    return str(ble_key)


async def _async_wifi_reply(link: _Link, ble_key: str) -> None:
    """Step 8: wait for the machine's verdict on joining the network."""
    while True:
        message = await link.async_receive(_WIFI_REPLY_TIMEOUT_S)
        plain = _json(message)
        if plain is not None and "type" in plain:
            if plain.get("type") == "cipher_reply" and (plain.get("data") or {}).get(
                "error_code"
            ) == 1:
                raise ProvisionError("handshake_failed")
            continue  # some other plain message; keep waiting
        try:
            reply = _json(decrypt(ble_key, message)) or {}
        except ValueError:
            continue
        if reply.get("type") != "wifi_reply":
            continue
        code = (reply.get("data") or {}).get("error_code")
        if code == 0:
            return
        raise ProvisionError(_WIFI_ERRORS.get(code, "wifi_failed"))


async def _async_wait_bound(cloud: J20CloudClient, mac: str, bind_code: str) -> dict:
    """Step 9: poll the cloud until the machine has bound to the account."""
    await asyncio.sleep(_BIND_FIRST_DELAY_S)
    for _ in range(_BIND_POLLS):
        try:
            status = await cloud.async_iot_data(
                "/api/device/bind/status",
                {"mac": mac, "product_id": J20_PRODUCT_ID, "bind_code": bind_code},
            )
        except J20ApiError:
            status = {}
        if status.get("device_id"):
            return status
        await asyncio.sleep(_BIND_POLL_S)
    raise ProvisionError("bind_failed")


async def async_provision(
    ble_client: Any, cloud: J20CloudClient, mac: str, ssid: str, password: str
) -> dict[str, Any]:
    """Hand the Wi-Fi credentials over and wait for the cloud binding.

    ``ble_client`` is a connected bleak client. Returns the bind-status
    ``data`` (``device_id``, ``product_id``, ``mac``, ``sn``, …).
    """
    body = {"product_id": J20_PRODUCT_ID, "mac": mac}
    bind = await _async_cloud(cloud, "/api/device/bind/code", body, "cloud_failed")
    random = await _async_cloud(
        cloud, "/api/device/ble/encryption/random/", body, "cloud_failed"
    )
    if not (bind_code := bind.get("bind_code")) or not random.get("random"):
        raise ProvisionError("cloud_failed")

    link = _Link(ble_client)
    await link.async_start()
    ble_key = await _async_handshake(link, cloud, mac, random)
    wifi = {
        "data": {
            "ssid": ssid,
            "activate_url": bind.get("activation_url") or "",
            "binding_code": bind_code,
            "password": password,
            "user_token": cloud.project_token or "",
        },
        "type": "apinfo",
    }
    await link.async_send(encrypt(ble_key, json.dumps(wifi).encode()))
    await _async_wifi_reply(link, ble_key)
    return await _async_wait_bound(cloud, mac, str(bind_code))
