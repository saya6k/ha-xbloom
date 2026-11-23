"""Cloud client for XBloom Original (J20).

The Original is a Wi-Fi appliance with no local control path: the app talks
to xBloom's cloud, and the machine talks to the same cloud over its own
IoT link (AWS IoT thing shadow). This client is the app's side of that
conversation — it is deliberately separate from ``_cloud_client.py``
(recipe sync on ``client-api.xbloom.com``) and from the BLE stack used by
Studio (J15).

Two auth schemes are involved:

* ``tMemberLogin.thtml`` returns ``token`` (recipe API) plus
  ``projectToken`` / ``projectRefreshToken`` (the IoT/device API bearer
  token and its refresh token). As in the app's ``TokenInterceptor``, a
  code-10001 rejection is answered with ``PUT /api/enduser/refresh_token/``
  first; only if that fails does the coordinator log in again.
* ``api-iot.xbloom.com`` requests carry the app's signed headers:
  ``platform``, ``appid``, ``version``, ``accept-language``, ``ts``,
  ``nonce``, ``sign``, and ``Authorization: token <projectToken>``.
  ``sign`` is ``MD5(appid,appsecret,nonce,ts)`` upper-cased; see
  ``signed_headers``. The values were read from the 2.2.5 APK's
  ``cn.com.library.net.RetrofitManager`` / ``ServiceConfig``.

It only reads (login, machine list, thing shadow); it never writes a
command to the machine. Everything here is derived from decompiling app
2.2.5 and has **not** been verified against a real Original. Token
rejections raise ``J20AuthError``; network failures and other API errors
raise ``J20ApiError`` (the shadow read logs non-auth API errors and returns
``None``), so callers can tell "bad password" from "cloud unreachable".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Any

import aiohttp

from ...cloud.client import (
    _AUTH_INTERFACE_VERSION,
    _AUTH_SKEY,
    _LANGUAGE_TYPE,
    _rsa_encrypt,
)
from .values import is_grinding

_LOGGER = logging.getLogger(__name__)

CLIENT_API_BASE = "https://client-api.xbloom.com"
J20_API_BASE = "https://api-iot.xbloom.com"
# ServiceConfig.configBeanRelease's WebSocket: live shadow deltas for every
# machine on the account, authenticated by the bare IoT token in an
# ``Authorization`` header (WsManager); no subscribe message is sent.
J20_WS_URL = "wss://15qmk9hof3.execute-api.us-east-1.amazonaws.com/prod/"

# App 2.2.5 constants (ServiceConfig.configBeanRelease).
J20_APP_ID = "94a98bca981d11eb9c0824418c29779a"
J20_APP_SECRET = "de77d4d97ecf9003eaa5f372fd9a616b"
J20_APP_VERSION = "2.2.5"
# DeviceType.getJ20ProductId(). Commands always send this constant; the
# shadow read sends the device's own productId from the machine list (the
# app's getJ20Info), falling back to this.
J20_PRODUCT_ID = "53DDY4"

# RefreshTokenService.REFRESH_TOKEN (PUT, body {"refresh_token": …}).
_REFRESH_TOKEN_PATH = "/api/enduser/refresh_token/"
_TOKEN_REJECTED_CODES = (401, 10001)

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "okhttp/4.12.0",
}


class J20AuthError(Exception):
    """The bearer token is missing or rejected — log in again."""


class J20ApiError(Exception):
    """The cloud was unreachable or answered with a non-zero code."""


def signed_headers(
    project_token: str,
    *,
    now: float | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Return the app's signed headers for an ``api-iot`` request.

    ``ts`` is epoch **seconds** (``currentTimeMillis / 1000`` in the app),
    ``nonce`` is a dash-stripped lower-case UUID, and ``sign`` is the
    upper-case MD5 of ``appid,appsecret,nonce,ts``.
    """
    timestamp = str(int(time.time() if now is None else now))
    nonce = nonce or uuid.uuid4().hex
    sign = hashlib.md5(
        f"{J20_APP_ID},{J20_APP_SECRET},{nonce},{timestamp}".encode(),
        usedforsecurity=False,
    ).hexdigest().upper()
    return {
        "platform": "android",
        "appid": J20_APP_ID,
        "version": J20_APP_VERSION,
        "accept-language": "en",
        "ts": timestamp,
        "nonce": nonce,
        "sign": sign,
        "Authorization": f"token {project_token}",
    }


def is_original_device(device: dict[str, Any]) -> bool:
    """True for a J20 entry in ``tuMachineList.tuhtml``'s ``machineList``."""
    try:
        device_type = int(device.get("theType"))
    except (TypeError, ValueError):
        device_type = 0
    return device_type == 2 or device.get("productId") == J20_PRODUCT_ID


def _normalize_code(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_shadow(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``DeviceJ20DetailModel`` into the coordinator's data dict.

    Kept pure so the mapping can be tested without HA. Unknown / missing
    fields become ``None`` — never a placeholder string (HA localizes
    ``None`` itself; see the repo's entity-translation note).
    """
    raw_mcus = data.get("multimcuotainfo") or {}
    if not isinstance(raw_mcus, dict):
        raw_mcus = {}
    mcu_versions: dict[str, str] = {}
    for mcu in raw_mcus.get("mcu_info") or []:
        if not isinstance(mcu, dict):
            continue
        name = mcu.get("mcu_name")
        version = mcu.get("mcu_version")
        if name and version:
            mcu_versions[str(name)] = str(version)

    pod = data.get("capsule_rfid_tag")
    if pod in ("", "IF0002"):
        pod = None

    work_mode = _normalize_code(data.get("work_mode"))
    return {
        "online": bool(data.get("is_online", False)),
        "work_mode": work_mode,
        "warning": _normalize_code(data.get("warning")),
        "disp_reserve": _normalize_code(data.get("disp_reserve")),
        "pod": str(pod) if pod else None,
        "mcu_versions": mcu_versions,
        "grinding": is_grinding(work_mode),
        "serial_number": data.get("DM_SN"),
    }


class J20CloudClient:
    """Async client for one Original account/device pair."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        # One client per account is shared by every Original on it; this
        # serializes their re-authentication so a rejected token is renewed
        # once, not once per machine (each login may invalidate the last).
        self.auth_lock = asyncio.Lock()
        # Config entries (machines) sharing this client.
        self.entry_ids: set[str] = set()
        self.member_id: int | None = None
        self.token: str | None = None
        self.project_token: str | None = None
        self.refresh_token: str | None = None

    @property
    def logged_in(self) -> bool:
        return bool(self.project_token)

    async def async_login(self, email: str, password: str) -> bool:
        """Log in and cache the tokens.

        Returns False when the account rejects the credentials; raises
        ``J20ApiError`` when the cloud cannot be reached, so a network blip
        is never mistaken for a bad password.
        """
        payload = await self._request(
            "POST",
            f"{CLIENT_API_BASE}/tMemberLogin.thtml",
            body={
                "interfaceVersion": _AUTH_INTERFACE_VERSION,
                "skey": _AUTH_SKEY,
                "clientType": 2,
                "phoneType": "Android",
                "languageType": _LANGUAGE_TYPE,
                "email": email,
                "password": password,
            },
        )
        if payload is None:
            raise J20ApiError("login request failed")
        if payload.get("result") != "success":
            return False
        member = payload.get("member") or {}
        try:
            self.member_id = int(member["tableId"])
        except (KeyError, TypeError, ValueError):
            return False
        self.token = str(payload.get("token") or "")
        self.project_token = str(payload.get("projectToken") or "")
        self.refresh_token = str(payload.get("projectRefreshToken") or "") or None
        return bool(self.token and self.project_token)

    async def async_refresh_token(self) -> bool:
        """Swap the refresh token for a new access token, as the app does.

        ``RefreshTokenRep``: ``PUT /api/enduser/refresh_token/`` with
        ``{"refresh_token": …}``; a ``code == 0`` answer carries ``data``
        ``{access_token, refresh_token, …}``. Returns False on any failure —
        the caller then falls back to a full login.
        """
        if not self.refresh_token:
            return False
        payload = await self._request(
            "PUT",
            f"{J20_API_BASE}{_REFRESH_TOKEN_PATH}",
            body={"refresh_token": self.refresh_token},
            headers=signed_headers(self.project_token or ""),
        )
        if payload is None or _normalize_code(payload.get("code")) != 0:
            return False
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("access_token"):
            return False
        self.project_token = str(data["access_token"])
        self.refresh_token = str(data.get("refresh_token") or "") or None
        return True

    async def async_list_devices(self) -> list[dict[str, Any]]:
        """The account's machines from ``tuMachineList.tuhtml``.

        Uses the recipe-cloud session token plus the IoT ``accessToken``
        the app's ``MachineListForm`` sends. Raises ``J20ApiError`` when the
        list can't be fetched, so an outage is never mistaken for an
        account without machines.
        """
        if not (self.token and self.project_token and self.member_id is not None):
            raise J20ApiError("not logged in")
        body = {
            "interfaceVersion": _AUTH_INTERFACE_VERSION,
            "skey": _AUTH_SKEY,
            "phoneType": "Android",
            "memberId": self.member_id,
            "clientType": 2,
            "languageType": _LANGUAGE_TYPE,
            "token": self.token,
            "accessToken": self.project_token,
        }
        # ``BaseTransfer.post`` RSA-encrypts the whole form for the JSON
        # content type the app uses; fall back to plain JSON because we
        # cannot verify which content type ``tuMachineList`` negotiated.
        payload = await self._request(
            "POST",
            f"{CLIENT_API_BASE}/tuMachineList.tuhtml",
            body=_rsa_encrypt(body),
        )
        if not payload or payload.get("result") != "success":
            payload = await self._request(
                "POST",
                f"{CLIENT_API_BASE}/tuMachineList.tuhtml",
                body=body,
            )
        if not payload or payload.get("result") != "success":
            raise J20ApiError("machine list request failed")
        machines = payload.get("machineList")
        if not machines and isinstance(payload.get("data"), dict):
            machines = payload["data"].get("machineList")
        machines = machines or []
        return [m for m in machines if isinstance(m, dict)]

    async def async_get_shadow(
        self, device_id: str, product_id: str
    ) -> dict[str, Any] | None:
        """Fetch the full thing shadow (``POST /api/device/thing_shadow/``)."""
        if not self.project_token:
            raise J20AuthError("not logged in")
        payload = await self._signed_post(
            "/api/device/thing_shadow/",
            {"device_id": device_id, "product_id": product_id},
        )
        if payload is None:
            raise J20ApiError("shadow request failed")
        code = _normalize_code(payload.get("code"))
        if code in _TOKEN_REJECTED_CODES:
            raise J20AuthError(f"token rejected (code {code})")
        if code not in (0, 200):
            _LOGGER.debug(
                "XBloom Original shadow failed: code=%s message=%s",
                code,
                payload.get("message"),
            )
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    async def _signed_post(
        self, path: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        return await self._request(
            "POST",
            f"{J20_API_BASE}{path}",
            body=body,
            headers=signed_headers(self.project_token or ""),
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        body: object | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            async with self._session.request(
                method,
                url,
                json=body,
                params=params,
                headers={**_HEADERS, **(headers or {})},
                timeout=_TIMEOUT,
            ) as response:
                status = response.status
                try:
                    payload = await response.json(content_type=None)
                except ValueError:
                    payload = None
        except (aiohttp.ClientError, TimeoutError) as exc:
            # Debug only: polling retries every interval, and the coordinator
            # already reports the outage once (UpdateFailedUnreachable).
            _LOGGER.debug("XBloom Original cloud call to %s failed: %s", url, exc)
            return None
        if isinstance(payload, dict):
            return payload
        if status == 401:
            # A token rejection whose body isn't the usual JSON envelope:
            # report it as one, so the caller re-authenticates instead of
            # treating it as the cloud being unreachable.
            return {"code": 401}
        _LOGGER.debug("XBloom Original cloud call to %s: HTTP %s, no JSON body", url, status)
        return None
