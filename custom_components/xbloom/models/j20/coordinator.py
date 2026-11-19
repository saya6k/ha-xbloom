"""Coordinator for XBloom Original (J20).

Polling the thing shadow is the read path. The app additionally opens a
WebSocket for live deltas (``AppWsManager``); v1 deliberately polls only —
the WS subscribe/auth handshake is not documented and polling is enough
for HA's state model. The command path is the app's ``desired`` shadow
write.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ...const import (
    CONF_EMAIL,
    CONF_J20_DEVICE_ID,
    CONF_J20_PRODUCT_ID,
    CONF_PASSWORD,
    DOMAIN,
)
from .client import (
    J20_PRODUCT_ID,
    J20ApiError,
    J20AuthError,
    J20CloudClient,
    parse_shadow,
)
from .values import (
    GRIND_SIZE_DEFAULT,
    PATTERN_DEFAULT,
    TEMPERATURE_DEFAULT,
    VOLUME_DEFAULT,
    build_bruw_curve,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


class J20Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Thing-shadow poller plus the confirmed desired-state commands."""

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
        self.device_id: str = entry.data[CONF_J20_DEVICE_ID]
        self.product_id: str = entry.data.get(CONF_J20_PRODUCT_ID) or J20_PRODUCT_ID
        self.entry_title: str = entry.title
        self.grind_size: int = GRIND_SIZE_DEFAULT
        self.volume_ml: int = VOLUME_DEFAULT
        self.temperature_c: float = TEMPERATURE_DEFAULT
        self.pattern: str = PATTERN_DEFAULT

    @property
    def device_info(self) -> DeviceInfo:
        mcu = (self.data or {}).get("mcu_versions") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"j20_{self.device_id}")},
            name=self.entry_title,
            manufacturer="xBloom",
            model="xBloom Original (J20)",
            sw_version=mcu.get("PannelMCU"),
        )

    @property
    def online(self) -> bool:
        return bool((self.data or {}).get("online"))

    async def _async_reauthenticate(self) -> None:
        """Get a fresh token the way the app's ``TokenInterceptor`` does.

        Refresh token first; if that fails, log in again with the stored
        account. Raises ``ConfigEntryAuthFailed`` only when the account
        itself is rejected, and ``J20ApiError`` when the cloud is
        unreachable.
        """
        if await self._client.async_refresh_token():
            return
        if not await self._client.async_login(self._email, self._password):
            raise ConfigEntryAuthFailed("XBloom Original login failed")

    async def _async_call(self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run one cloud call, re-authenticating once on a token rejection."""
        try:
            return await func()
        except J20AuthError:
            await self._async_reauthenticate()
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
            raise UpdateFailed(f"XBloom Original cloud unreachable: {exc}") from exc
        if data is None:
            raise UpdateFailed("XBloom Original shadow request failed")
        return parse_shadow(data)

    async def async_grind(self, size: int | None = None) -> None:
        """Start the grinder (``{grinding_start: 1, setting_grind_size: N}``)."""
        self.grind_size = int(self.grind_size if size is None else size)
        await self._send(
            {"grinding_start": 1, "setting_grind_size": self.grind_size}
        )

    async def async_pour(self) -> None:
        """Start a manual pour with the current volume/temperature/pattern."""
        await self._send(
            {
                "bruw_curve": build_bruw_curve(
                    self.volume_ml, self.temperature_c, self.pattern
                )
            }
        )

    async def async_stop(self) -> None:
        """Stop pouring (``{bruw_curve: "FFFF11"}``, the app's sentinel)."""
        await self._send({"bruw_curve": "FFFF11"})

    async def async_calibrate(self) -> None:
        """Run the grinder auto-zero routine (``{auto_calibrate: 1}``)."""
        await self._send({"auto_calibrate": 1})

    async def _send(self, desired: dict[str, Any]) -> None:
        # The app disables its start buttons while the shadow reports the
        # machine offline (refreshOnLineView). A desired state written now
        # would sit in the shadow and run whenever the machine reconnects.
        if not self.online:
            raise HomeAssistantError("XBloom Original is offline")
        try:
            await self._async_call(
                lambda: self._client.async_send_desired(self.device_id, desired)
            )
        except ConfigEntryAuthFailed as exc:
            # Raised from a button press, this would not start a reauth
            # flow on its own; start it, then fail the press.
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "XBloom Original sign-in failed; reauthenticate the account"
            ) from exc
        except J20ApiError as exc:
            raise HomeAssistantError(
                f"XBloom Original command failed: {exc}"
            ) from exc
        await self.async_request_refresh()
