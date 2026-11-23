"""Coordinator for XBloom Original (J20) — status only.

Reads the thing shadow (polled) and merges the app's WebSocket deltas into
it. It never writes to the machine: controls stay on the repository's
``original`` branch until an Original owner has confirmed them on real
hardware. It declares no capabilities, so the shared services, LLM tools and
panel controls never target it; the panel and the status tool read it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ClassVar, TypeVar

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ...const import (
    CONF_EMAIL,
    CONF_J20_DEVICE_ID,
    CONF_J20_PRODUCT_ID,
    CONF_J20_SERIAL_NUMBER,
    CONF_PASSWORD,
    DOMAIN,
)
from .client import (
    J20_PRODUCT_ID,
    J20_WS_URL,
    J20ApiError,
    J20AuthError,
    J20CloudClient,
    parse_shadow,
)
from .values import warning_state, work_mode_state

try:  # HA 2026.12+: logs the first failure at info, not error on every poll
    from homeassistant.helpers.update_coordinator import UpdateFailedUnreachable
except ImportError:  # below 2026.12 (the floor is 2026.11)
    UpdateFailedUnreachable = UpdateFailed

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

_WS_HEARTBEAT_S = 30  # WsManager.HEARTBEAT_INTERVAL
_WS_BACKOFF_MIN_S = 5
_WS_BACKOFF_MAX_S = 300


class J20Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Thing-shadow reader with live WebSocket updates."""

    # Status only: nothing shared may send this machine a command.
    capabilities: frozenset = frozenset()
    # work_mode 5 ("ready") is ready to brew; finished is "complete".
    ready_is_idle = True
    # Descaling has no command in the app either; the panel shows its steps.
    descale_guide = "descale_steps_j20"
    unreachable_hint = (
        "Tell the user the xBloom Original could not be reached through the "
        "xBloom cloud: it is offline (powered off or not on Wi-Fi) or the "
        "cloud is unreachable. Ask them to check the machine is on and "
        "connected to Wi-Fi."
    )
    # The panel's generic entity keys -> this model's own translation keys.
    panel_entity_aliases: ClassVar[dict[str, str]] = {
        "sensor.state": "sensor.j20_state",
        "binary_sensor.online": "binary_sensor.j20_online",
        "binary_sensor.problem": "binary_sensor.j20_problem",
        "sensor.warning": "sensor.j20_warning",
        "sensor.brew_progress": "sensor.j20_brew_progress",
        "sensor.pod": "sensor.j20_pod",
        "sensor.panel_mcu": "sensor.j20_panel_mcu",
        "sensor.power_mcu": "sensor.j20_power_mcu",
    }

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: J20CloudClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} Original",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._email: str = entry.data.get(CONF_EMAIL, "")
        self._password: str = entry.data.get(CONF_PASSWORD, "")
        self.entry_id: str = entry.entry_id
        self.device_id: str = entry.data[CONF_J20_DEVICE_ID]
        self.product_id: str = entry.data.get(CONF_J20_PRODUCT_ID) or J20_PRODUCT_ID
        self._serial: str = entry.data.get(CONF_J20_SERIAL_NUMBER) or ""
        self.entry_title: str = entry.title
        # The last full shadow; WebSocket deltas are merged into it.
        self._raw_shadow: dict[str, Any] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        mcu = (self.data or {}).get("mcu_versions") or {}
        version = mcu.get("PannelMCU")
        # Pass sw_version only when known: sw_version=None would clear the
        # firmware version already in the device registry.
        extra = {"sw_version": version} if version else {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"j20_{self.device_id}")},
            name=self.machine_name,
            manufacturer="xBloom",
            model="xBloom Original (J20)",
            **extra,
        )

    @property
    def online(self) -> bool:
        return bool((self.data or {}).get("online"))

    @property
    def live(self) -> dict[str, Any]:
        """The shadow's live readings, or nothing while the machine is
        offline (they are its last reports then, not its current state)."""
        return (self.data or {}) if self.online else {}

    # ------------------------------------------------------------------
    # machine.Machine interface
    # ------------------------------------------------------------------

    @property
    def machine_label(self) -> str:
        return self._serial or self.device_id

    @property
    def machine_name(self) -> str:
        """"xBloom Original <last six serial characters>", like the Studio's
        name; the entry title when the serial isn't known."""
        return f"xBloom Original {self._serial[-6:]}" if self._serial else self.entry_title

    @property
    def machine_serial(self) -> str | None:
        return self._serial or None

    async def async_ensure_available(self) -> bool:
        """Re-read the shadow, then report whether the machine is online."""
        await self.async_refresh()
        return self.last_update_success and self.online

    def status_summary(self) -> dict[str, Any]:
        data = self.data or {}
        return {
            "model": "xBloom Original (J20)",
            "serial_number": data.get("serial_number") or self._serial,
            "online": self.online,
            "state": work_mode_state(data.get("work_mode")) or "unknown",
            "warning": warning_state(data.get("warning")),
            "brew_progress": data.get("disp_reserve"),
            "pod": data.get("pod"),
            "firmware_versions": data.get("mcu_versions") or {},
            "controls": "not supported yet — status only",
        }

    # ------------------------------------------------------------------
    # Cloud plumbing
    # ------------------------------------------------------------------

    async def _async_reauthenticate(self, rejected_token: str | None = None) -> None:
        """Get a fresh token the way the app's ``TokenInterceptor`` does.

        Refresh token first; if that fails, log in again with the stored
        account. Raises ``ConfigEntryAuthFailed`` only when the account
        itself is rejected, and ``J20ApiError`` when the cloud is
        unreachable.

        The client is shared by every Original on the account: if another
        machine already renewed the token that was rejected here, reuse it.
        """
        async with self._client.auth_lock:
            current = self._client.project_token
            if rejected_token is not None and current and current != rejected_token:
                return
            if await self._client.async_refresh_token():
                return
            if not await self._client.async_login(self._email, self._password):
                raise ConfigEntryAuthFailed("XBloom Original login failed")

    async def _async_call(self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run one cloud call, re-authenticating once on a token rejection."""
        token = self._client.project_token
        try:
            return await func()
        except J20AuthError:
            await self._async_reauthenticate(token)
        try:
            return await func()
        except J20AuthError as exc:
            raise ConfigEntryAuthFailed(
                "XBloom Original token still rejected after signing in again"
            ) from exc

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._async_call(
                lambda: self._client.async_get_shadow(self.device_id, self.product_id)
            )
        except J20ApiError as exc:
            raise UpdateFailedUnreachable(
                f"XBloom Original cloud unreachable: {exc}"
            ) from exc
        if data is None:
            raise UpdateFailed("XBloom Original shadow request failed")
        self._raw_shadow = dict(data)
        return parse_shadow(data)

    # ------------------------------------------------------------------
    # Live updates (AppWsManager / WsManager)
    # ------------------------------------------------------------------

    def handle_live_message(self, text: str) -> bool:
        """Apply one WebSocket message; True when it changed this machine.

        Messages carry ``{device_id, state: {reported: {...}}}`` with only
        the fields that changed; ones for another machine on the account
        and ``type`` messages are ignored, as in the app.

        The app applies a message without ``device_id`` to its one current
        machine; here that is only unambiguous while this is the account's
        only Original, so otherwise such a message is ignored.
        """
        try:
            message = json.loads(text)
        except ValueError:
            return False
        if not isinstance(message, dict) or "type" in message:
            return False
        device_id = message.get("device_id")
        if device_id is None:
            if len(self._client.entry_ids) > 1:
                return False
        elif device_id != self.device_id:
            return False
        reported = (message.get("state") or {}).get("reported")
        if not isinstance(reported, dict) or self._raw_shadow is None:
            return False
        self._raw_shadow.update(reported)
        self.async_set_updated_data(parse_shadow(self._raw_shadow))
        return True

    async def async_run_live_updates(self) -> None:
        """Keep the account WebSocket open (the app's 30 s ping), applying
        its deltas; reconnects with back-off, renewing a rejected token.
        Polling continues underneath as the fallback."""
        session = async_get_clientsession(self.hass)
        backoff = _WS_BACKOFF_MIN_S
        while True:
            token = self._client.project_token
            try:
                async with session.ws_connect(
                    J20_WS_URL,
                    headers={"Authorization": token or ""},
                    heartbeat=_WS_HEARTBEAT_S,
                ) as ws:
                    backoff = _WS_BACKOFF_MIN_S
                    _LOGGER.debug("XBloom Original live updates connected")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.handle_live_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except aiohttp.WSServerHandshakeError as exc:
                if exc.status in (401, 403):
                    try:
                        await self._async_reauthenticate(token)
                    except (ConfigEntryAuthFailed, J20ApiError) as err:
                        _LOGGER.debug("XBloom Original live updates: %s", err)
                else:
                    _LOGGER.debug("XBloom Original live updates refused: %s", exc)
            except (aiohttp.ClientError, TimeoutError) as exc:
                _LOGGER.debug("XBloom Original live updates dropped: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _WS_BACKOFF_MAX_S)
