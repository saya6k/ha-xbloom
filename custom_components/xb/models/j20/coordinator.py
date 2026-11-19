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

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ClassVar, TypeVar

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ...cloud.client import XBloomCloudClient, cloud_recipe_to_local
from ...const import (
    CONF_EMAIL,
    CONF_J20_DEVICE_ID,
    CONF_J20_PRODUCT_ID,
    CONF_J20_SERIAL_NUMBER,
    CONF_PASSWORD,
    DOMAIN,
)
from ...machine import Capability, ManualLimits
from ...recipes.schema import find_recipe, scale_pours_to_total
from ...recipes.store import RecipeStoreMixin, is_tea_recipe
from .client import (
    J20_PRODUCT_ID,
    J20_WS_URL,
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
    RECIPE_LEGACY_NO_GRIND,
    TEMPERATURE_DEFAULT,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    VOLUME_DEFAULT,
    VOLUME_MAX,
    VOLUME_MIN,
    WORK_MODE_BREWING,
    WORK_MODE_GRINDING,
    WORK_MODE_SLEEPING,
    build_bruw_curve,
    recipe_start_blocked,
    warning_state,
    work_mode_state,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

# How long a local arm (see the module docstring) waits for its confirm.
# The Studio's arm lives on the machine's screen and ends with it; this one
# has nothing to end it, so it lapses rather than firing hours later.
ARM_TIMEOUT_S = 300

_WS_HEARTBEAT_S = 30  # WsManager.HEARTBEAT_INTERVAL
_WS_BACKOFF_MIN_S = 5
_WS_BACKOFF_MAX_S = 300


# warning codes that mean the tank is short of water (values.WARNING_STATES).
_WATER_WARNINGS = frozenset({169, 190})


class J20Coordinator(RecipeStoreMixin, DataUpdateCoordinator[dict[str, Any]]):
    """Thing-shadow poller plus the confirmed desired-state commands."""

    capabilities = frozenset(
        {
            Capability.GRIND,
            Capability.POUR,
            Capability.CANCEL,
            Capability.CALIBRATE_GRINDER,
            Capability.RECIPES,
            Capability.AUTO_MODE,
        }
    )
    # The Original has no tea brewer.
    brews_tea = False
    # work_mode 5 ("ready") is ready to brew; finished is "complete".
    ready_is_idle = True
    # Descaling has no command; the panel shows the app's steps (DescaleJ20Activity).
    descale_guide = "descale_steps_j20"
    # Per-brew overrides its recipe run honours (llm/prompt.py): the run
    # command has no bypass, and the recipe's grind is sent as stored.
    recipe_overrides = frozenset({"dose_g", "ratio", "temperature_c", "pour_overrides"})
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
        "select.recipe": "select.j20_recipe",
        "button.execute_recipe": "button.j20_execute_recipe",
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
        self.grind_size: int = GRIND_SIZE_DEFAULT
        self.volume: int = VOLUME_DEFAULT
        self.temperature: float = TEMPERATURE_DEFAULT
        self.pattern: str = PATTERN_DEFAULT
        self._armed: str | None = None
        self._armed_at: float = 0.0
        # Recipe store (RecipeStoreMixin): the same xBloom account signs in
        # to the recipe cloud, which also turns a recipe into a run command.
        self.recipes: dict[str, dict] = {}
        self.selected_recipe: str | None = None
        self.cloud_client = XBloomCloudClient(async_get_clientsession(hass))
        self._cloud_email: str = self._email
        self._cloud_password: str = self._password
        # The run command fetched by the arm, sent by the confirm.
        self._armed_recipe_command: str | None = None
        # The last full shadow; WebSocket deltas are merged into it.
        self._raw_shadow: dict[str, Any] | None = None
        # The recipe on the machine's start button (auto mode), as last read;
        # None until read (or when the account has no record).
        self.auto_mode: dict[str, Any] | None = None
        self._machine_table_id: int | None = None

    @property
    def device_info(self) -> DeviceInfo:
        mcu = (self.data or {}).get("mcu_versions") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"j20_{self.device_id}")},
            name=self.machine_name,
            manufacturer="xBloom",
            model="xBloom Original (J20)",
            sw_version=mcu.get("PannelMCU"),
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
            self._armed_recipe_command = None
            self.async_update_listeners()
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
        """Run the grinder auto-zero routine (``{auto_calibrate: 1}``).

        Like the app, refuse on a fresh shadow that says the machine is
        asleep (MachineAdvancedFeaturesJ20Activity → Machine_Calibrate_Sleep).
        """
        await self.async_refresh()
        if work_mode_state(self.live.get("work_mode")) == WORK_MODE_SLEEPING:
            raise HomeAssistantError(
                "The xBloom Original is asleep — tap the xPod dock to wake it, "
                "then calibrate."
            )
        await self._send({"auto_calibrate": 1})

    # ------------------------------------------------------------------
    # Recipes (RecipeDetailActivity's J20 path)
    # ------------------------------------------------------------------

    @property
    def water_shortage(self) -> bool:
        """The shadow warns the tank is low or empty (169 / 190)."""
        return (self.data or {}).get("warning") in _WATER_WARNINGS

    def select_recipe(self, name: str | None) -> None:
        """Set the recipe the execute button / panel brews.

        Unlike the Studio, the recipe's grind size is not copied onto the
        manual grind slider: the Original's grinder uses its own 1-30 scale
        and the cloud converts the recipe's value when it builds the run
        command.
        """
        self.selected_recipe = name
        self.async_update_listeners()

    def _recipe_for_brew(
        self, overrides: dict | None, pour_overrides: list[dict] | None
    ) -> dict:
        """The selected recipe with this brew's overrides applied."""
        if not self.selected_recipe or self.selected_recipe not in self.recipes:
            raise HomeAssistantError("No recipe selected.")
        recipe = dict(self.recipes[self.selected_recipe])
        if is_tea_recipe({**recipe, **(overrides or {})}):
            raise HomeAssistantError("The xBloom Original can't brew tea recipes.")
        recipe["pours"] = [dict(p) for p in recipe.get("pours") or []]
        overrides = dict(overrides or {})
        overrides.pop("cup_type", None)
        temperature = overrides.pop("temperature_c", None)
        recipe.update(overrides)
        if "dose_g" in overrides or "ratio" in overrides:
            dose = float(recipe.get("dose_g") or 0)
            ratio = recipe.get("ratio")
            if dose > 0 and ratio:
                recipe["pours"] = scale_pours_to_total(
                    recipe["pours"], dose * float(ratio)
                )
        if temperature is not None:
            for pour in recipe["pours"]:
                pour["temperature_c"] = int(temperature)
        for override in pour_overrides or []:
            index = int(override.get("pour_index", -1))
            if not 0 <= index < len(recipe["pours"]):
                continue
            pour = recipe["pours"][index]
            if override.get("volume") is not None:
                pour["volume_ml"] = int(override["volume"])
            if override.get("flow_rate") is not None:
                pour["flow_rate"] = float(override["flow_rate"])
            if override.get("pattern") is not None:
                pour["pattern"] = int(override["pattern"])
        # The run command carries no bypass water (GetRecipeMadeCmdForm).
        recipe["bypass_volume"] = 0
        recipe["bypass_temperature"] = 0
        return recipe

    async def async_arm_recipe(
        self,
        *,
        overrides: dict | None = None,
        pour_overrides: list[dict] | None = None,
        bypass_volume: float | None = None,
        bypass_temperature: float | None = None,
    ) -> None:
        """Check the machine can take the selected recipe and fetch its run
        command — the app's tap on Start, up to its "Let's go" dialog.

        ``async_confirm_recipe`` then sends it. The Original's run command
        has no bypass water, so ``bypass_*`` are ignored.
        """
        recipe = self._recipe_for_brew(overrides, pour_overrides)
        # Decide on a fresh shadow, like the app's getJ20Info before a start.
        await self.async_refresh()
        if not self.online:
            raise HomeAssistantError("XBloom Original is offline")
        if self.water_shortage:
            raise HomeAssistantError(
                "xBloom water level is too low — refill the tank before brewing."
            )
        data = self.data or {}
        mcus = data.get("mcu_versions") or {}
        blocked = recipe_start_blocked(
            data.get("work_mode"),
            int(recipe.get("grind_size") or 0) <= 0,
            mcus.get("PowerMCU"),
            mcus.get("PannelMCU"),
        )
        if blocked == RECIPE_LEGACY_NO_GRIND:
            raise HomeAssistantError(
                "This machine's firmware can't run a recipe without grinding."
            )
        if blocked is not None:
            raise HomeAssistantError(
                "The xBloom Original isn't ready for a recipe — wake it and "
                "wait until it shows ready, then try again."
            )
        if not await self.async_ensure_cloud_login():
            raise HomeAssistantError("xBloom recipe cloud sign-in failed")
        command = await self.cloud_client.async_recipe_machine_command(
            recipe, self.device_id, J20_PRODUCT_ID
        )
        if not command:
            raise HomeAssistantError(
                "The xBloom cloud didn't return a run command for this recipe"
            )
        self._armed_recipe_command = command
        self._arm("recipe")
        self.async_update_listeners()

    async def async_confirm_recipe(self) -> bool:
        """Send the run command the arm fetched (the app's ``startJ20``).

        Returns whether it was sent; the machine then reports grinding,
        brewing and complete through the shadow.
        """
        command = self._armed_recipe_command
        if command is None or not self._take_arm("recipe"):
            return False
        self._armed_recipe_command = None
        try:
            await self._send(command)
        finally:
            self.async_update_listeners()
        return True

    async def async_execute_recipe(
        self,
        *,
        overrides: dict | None = None,
        pour_overrides: list[dict] | None = None,
        bypass_volume: float | None = None,
        bypass_temperature: float | None = None,
    ) -> None:
        """Arm and confirm in one call (the execute_recipe service)."""
        await self.async_arm_recipe(overrides=overrides, pour_overrides=pour_overrides)
        await self.async_confirm_recipe()

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
            raise UpdateFailed(f"XBloom Original cloud unreachable: {exc}") from exc
        if data is None:
            raise UpdateFailed("XBloom Original shadow request failed")
        self._raw_shadow = dict(data)
        return parse_shadow(data)

    # ------------------------------------------------------------------
    # Auto mode (EasyModeJ20Fragment / EasyModeJ20ViewModel)
    # ------------------------------------------------------------------

    async def _async_auto_mode_key(self) -> tuple[str, int]:
        """The machine's serial and account record id, signed in."""
        if not self._serial:
            raise HomeAssistantError("The xBloom Original's serial number isn't known yet.")
        if self._machine_table_id is None:
            try:
                devices = await self._async_call(self._client.async_list_devices)
            except J20ApiError as exc:
                raise HomeAssistantError(f"xBloom cloud unreachable: {exc}") from exc
            for device in devices:
                if str(device.get("deviceId")) == self.device_id and device.get("tableId"):
                    self._machine_table_id = int(device["tableId"])
                    break
        if self._machine_table_id is None:
            raise HomeAssistantError("This machine isn't on the xBloom account's machine list.")
        if not await self.async_ensure_cloud_login():
            raise HomeAssistantError("xBloom recipe cloud sign-in failed")
        return self._serial, self._machine_table_id

    def _auto_mode_from_record(self, record: dict[str, Any] | None) -> dict[str, Any] | None:
        detail = (record or {}).get("autoModeDetailVo")
        snapshot = detail.get("recipeSnapshotVo") if isinstance(detail, dict) else None
        if not isinstance(snapshot, dict):
            return None
        table_id = snapshot.get("tableId")
        local_name = next(
            (
                name
                for name, recipe in (self.recipes or {}).items()
                if table_id is not None and recipe.get("cloud_table_id") == table_id
            ),
            None,
        )
        return {
            "name": snapshot.get("theName") or cloud_recipe_to_local(snapshot).get("name"),
            "uid": self.recipes[local_name].get("uid") if local_name else None,
            "cloud_table_id": table_id,
            "scale": detail.get("scale"),
            "record_table_id": detail.get("tableId"),
        }

    async def async_refresh_auto_mode(self) -> dict[str, Any] | None:
        """Read the recipe stored on the machine's start button."""
        serial, machine_table_id = await self._async_auto_mode_key()
        record = await self.cloud_client.async_auto_mode(serial, machine_table_id)
        if record is None:
            raise HomeAssistantError("Couldn't read the xBloom Original's auto mode")
        self.auto_mode = self._auto_mode_from_record(record)
        self.async_update_listeners()
        return self.auto_mode

    async def _async_store_on_button(self, recipe: dict[str, Any]) -> None:
        command = await self.cloud_client.async_recipe_machine_command(
            recipe, self.device_id, J20_PRODUCT_ID, auto_mode=True
        )
        if not command:
            raise HomeAssistantError(
                "The xBloom cloud didn't return an auto-mode command for this recipe"
            )
        await self._send(command)

    async def async_write_auto_mode(self, identifier: str | None = None) -> dict[str, Any]:
        """Store a recipe on the machine's start button, like the app's auto
        mode: the recipe must be on the xBloom account (it is exported first
        if it isn't), the machine gets its auto-mode command, and the
        account's record is updated (tuAutoModeSync)."""
        resolved = (
            find_recipe(self.recipes or {}, identifier)
            if identifier
            else (
                (self.selected_recipe, self.recipes[self.selected_recipe])
                if self.selected_recipe in (self.recipes or {})
                else None
            )
        )
        if resolved is None:
            raise HomeAssistantError(f"No local recipe matches {identifier!r}.")
        name, recipe = resolved
        serial, machine_table_id = await self._async_auto_mode_key()
        record = await self.cloud_client.async_auto_mode(serial, machine_table_id)
        detail = (record or {}).get("autoModeDetailVo")
        if not isinstance(detail, dict) or detail.get("tableId") is None:
            raise HomeAssistantError("Couldn't read the xBloom Original's auto mode")
        recipe_table_id = recipe.get("cloud_table_id")
        if recipe_table_id is None:
            exported = await self.async_export_recipe(recipe.get("uid") or name)
            recipe_table_id = exported.get("id")
            if recipe_table_id is None:
                raise HomeAssistantError(
                    exported.get("message") or "Couldn't save the recipe to the xBloom account"
                )
            recipe = self.recipes.get(name, recipe)
        await self._async_store_on_button(recipe)
        if not await self.cloud_client.async_auto_mode_sync(
            int(detail["tableId"]), int(recipe_table_id), int(detail.get("scale") or 0)
        ):
            raise HomeAssistantError(
                "The recipe was sent to the machine, but the xBloom account "
                "couldn't record it"
            )
        await self.async_refresh_auto_mode()
        return {"success": True, "uid": recipe.get("uid"), "name": name}

    async def async_reset_auto_mode(self) -> dict[str, Any]:
        """Put the factory recipe back on the start button (tuAutoModeInit →
        its command to the machine → tuAutoModeReset), as the app does."""
        serial, machine_table_id = await self._async_auto_mode_key()
        default = await self.cloud_client.async_auto_mode_default(serial, machine_table_id)
        snapshot = ((default or {}).get("autoModeDetailVo") or {}).get("recipeSnapshotVo")
        if not isinstance(snapshot, dict):
            raise HomeAssistantError("Couldn't read the factory auto-mode recipe")
        await self._async_store_on_button(cloud_recipe_to_local(snapshot))
        if await self.cloud_client.async_auto_mode_reset(serial, machine_table_id) is None:
            raise HomeAssistantError(
                "The factory recipe was sent to the machine, but the xBloom "
                "account couldn't record it"
            )
        await self.async_refresh_auto_mode()
        return {"success": True, "name": (self.auto_mode or {}).get("name")}

    # ------------------------------------------------------------------
    # Live updates (AppWsManager / WsManager)
    # ------------------------------------------------------------------

    def handle_live_message(self, text: str) -> bool:
        """Apply one WebSocket message; True when it changed this machine.

        Messages carry ``{device_id, state: {reported: {...}}}`` with only
        the fields that changed; ones for another machine on the account
        and ``type`` messages are ignored, as in the app.
        """
        try:
            message = json.loads(text)
        except ValueError:
            return False
        if not isinstance(message, dict) or "type" in message:
            return False
        if message.get("device_id") not in (None, self.device_id):
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

    async def _send(self, desired: dict[str, Any] | str) -> None:
        # Any command acted on the machine directly, so a pending arm from
        # an earlier conversation no longer reflects what the user wants.
        self._armed = None
        self._armed_recipe_command = None
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
