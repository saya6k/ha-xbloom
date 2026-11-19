"""Coordinator for XBloom Original (J20).

Polling the thing shadow is the read path. The app additionally opens a
WebSocket for live deltas (``AppWsManager``); v1 deliberately polls only —
the WS subscribe/auth handshake is not documented and polling is enough
for HA's state model. The command path is the app's ``desired`` shadow
write.

Implements ``machine.ManualControlMachine`` so the shared LLM tools and
panel can drive it. The Original has no on-machine "armed" page, so the
two-stage arm/confirm the shared tools use is kept locally: arming only
records the operation, confirming sends it.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ClassVar, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
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
from ...machine import Capability, ManualLimits
from .client import (
    J20_PRODUCT_ID,
    J20ApiError,
    J20AuthError,
    J20CloudClient,
    parse_shadow,
)
from .values import (
    GRIND_SIZE_DEFAULT,
    GRIND_SIZE_MAX,
    GRIND_SIZE_MIN,
    PATTERN_DEFAULT,
    TEMPERATURE_DEFAULT,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    VOLUME_DEFAULT,
    VOLUME_MAX,
    VOLUME_MIN,
    WORK_MODE_BREWING,
    WORK_MODE_GRINDING,
    build_bruw_curve,
    warning_state,
    work_mode_state,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

# How long a local arm (see the module docstring) waits for its confirm.
# The Studio's arm lives on the machine's screen and ends with it; this one
# has nothing to end it, so it lapses rather than firing hours later.
ARM_TIMEOUT_S = 300


class J20Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Thing-shadow poller plus the confirmed desired-state commands."""

    capabilities = frozenset(
        {Capability.GRIND, Capability.POUR, Capability.CANCEL, Capability.CALIBRATE_GRINDER}
    )
    # Seekbar ranges from the app; the temperature seekbar's top end (96)
    # is its BP position, sent as the 95 °C constant (values.temperature_code).
    limits = ManualLimits(
        grind_size=(GRIND_SIZE_MIN, GRIND_SIZE_MAX),
        temperature_c=(TEMPERATURE_MIN, TEMPERATURE_MAX - 1),
        boiling_c=TEMPERATURE_MAX,
        volume_ml=(VOLUME_MIN, VOLUME_MAX),
    )
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
        "number.grind_size": "number.j20_grind_size",
        "number.volume": "number.j20_volume",
        "number.temperature": "number.j20_temperature",
        "select.pour_pattern": "select.j20_pattern",
        "button.grind": "button.j20_grind",
        "button.pour": "button.j20_pour",
        "button.cancel": "button.j20_stop",
        "button.calibrate_grinder": "button.j20_calibrate",
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
        self.grind_size: int = GRIND_SIZE_DEFAULT
        self.volume: int = VOLUME_DEFAULT
        self.temperature: float = TEMPERATURE_DEFAULT
        self.pattern: str = PATTERN_DEFAULT
        self._armed: str | None = None
        self._armed_at: float = 0.0

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

    # ------------------------------------------------------------------
    # machine.Machine interface
    # ------------------------------------------------------------------

    @property
    def machine_label(self) -> str:
        return self._serial or self.device_id

    @property
    def armed_operation(self) -> str | None:
        if self._armed is not None and time.monotonic() - self._armed_at > ARM_TIMEOUT_S:
            self._armed = None
        return self._armed

    def _arm(self, operation: str) -> bool:
        if not self.online:
            return False
        self._armed = operation
        self._armed_at = time.monotonic()
        return True

    def _take_arm(self, operation: str) -> bool:
        """Consume a standing (unexpired) arm of ``operation``."""
        if self.armed_operation != operation:
            return False
        self._armed = None
        return True

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
        }

    async def async_arm_grind(self) -> bool:
        return self._arm("grind")

    async def async_confirm_grind(self) -> bool:
        if not self._take_arm("grind"):
            return False
        await self.async_grind()
        return True

    async def async_arm_pour(self) -> bool:
        return self._arm("pour")

    async def async_confirm_pour(self) -> bool:
        if not self._take_arm("pour"):
            return False
        await self.async_pour()
        return True

    async def async_cancel(self) -> None:
        """Drop a pending arm, or stop a running pour (the Stop button and
        the panel's Cancel use this too).

        The app has no command to stop the grinder (only
        ``grinding_start: 1``), so a running grind can't be cancelled.
        """
        if self.armed_operation is not None:
            self._armed = None
            return
        # The polled shadow may be up to a poll interval old; decide on a
        # fresh one rather than stop an idle machine or miss a pour.
        await self.async_refresh()
        state = work_mode_state((self.data or {}).get("work_mode"))
        if state == WORK_MODE_GRINDING:
            raise HomeAssistantError(
                "The xBloom Original's grinder can't be stopped remotely"
            )
        if state == WORK_MODE_BREWING:
            await self.async_stop()

    async def async_calibrate_grinder(self) -> None:
        """Run the grinder auto-zero routine (``{auto_calibrate: 1}``)."""
        await self._send({"auto_calibrate": 1})

    # ------------------------------------------------------------------
    # Cloud plumbing
    # ------------------------------------------------------------------

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
                    self.volume, self.temperature, self.pattern
                )
            }
        )

    async def async_stop(self) -> None:
        """Stop pouring (``{bruw_curve: "FFFF11"}``, the app's sentinel)."""
        await self._send({"bruw_curve": "FFFF11"})

    async def _send(self, desired: dict[str, Any]) -> None:
        # Any command acted on the machine directly, so a pending arm from
        # an earlier conversation no longer reflects what the user wants.
        self._armed = None
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
