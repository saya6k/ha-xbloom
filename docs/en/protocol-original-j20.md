# xBloom Original (J20) cloud protocol reference

> Source of truth — see [한국어](../ko/protocol-original-j20.md) for the Korean
> translation (may lag). Written entirely from statically decompiling the
> official Android app (`xbloom_coffee_release.apk`, package
> `com.chisalsoft.andite`, via `jadx` — inspected as a zip/dex/source dump
> only, never published or redistributed). **Unlike [`protocol.md`](./protocol.md)
> (Studio BLE), nothing here has been verified against real hardware.** The
> integration now ships a cloud-only J20 path in
> `custom_components/xbloom/j20/` (config-entry type ``original``: account
> login, thing-shadow polling, grind/stop/calibrate), but there is still no
> Original unit and no live account in this repo, so that code is unverified
> too. This document is the decompile-derived map behind it — every claim
> traces to a specific class/method; anything not cross-checked is marked
> unconfirmed.

## Why Original is a separate document

The app internally splits the two machines by a `DeviceType` int
(`constant/DeviceType.java`):

| `DeviceType` | value | product | `productId` | transport |
|---|---|---|---|---|
| `DeviceJ15` | `1` | **Studio** | — | Bluetooth LE (see [`protocol.md`](./protocol.md)) |
| `DeviceJ20` | `2` | **Original** | `"53DDY4"` | Wi-Fi + cloud IoT thing-shadow |

`DeviceJ15 == 1` lines up with the cloud API's hardcoded `adaptedModel: 1`
(Studio). The two share almost nothing at the transport layer: every J20 screen,
scan flow, and networking class is a parallel `*J20*` sibling of the Studio one
(`MachineJ20Fragment`, `PairDeviceJ20Activity`, `BrewerJ20Activity`,
`GrinderJ20Activity`, `AutoJ20Activity`, `J20ScanVM`, …). **There is no BLE
brew/grind/pour command path for Original at all** — see "Relationship to the
Studio BLE protocol" at the bottom for why none of this integration's BLE stack
is reusable.

## Architecture: Wi-Fi + AWS IoT thing shadow

Original is a Wi-Fi appliance controlled through xBloom's cloud, not directly.
The pattern is a classic AWS IoT **thing shadow** (a.k.a. device shadow):

1. The app never sends a brew/grind command to the machine directly. It `POST`s
   a **desired state** document to the cloud (`/api/device/command`).
2. The cloud relays the desired state to the machine over its own IoT/MQTT link.
3. The machine reports back its **reported state**, which the cloud fans out to
   the app over a **WebSocket** as `state.reported.{...}` JSON.

So a HA integration for Original would be a **cloud polling/streaming client**,
not a Bluetooth client — closer in shape to `_cloud_client.py` than to
`ble/` or `coordinator/connection.py`.

## Hosts and auth

The IoT backend base URL is chosen by `ServiceConfig` (global vs. China),
`AboutActivity` confirms the global default:

- **Global**: `https://api-iot.xbloom.com/`
- **China**: `https://api-iot.xbloomcoffee.cn/`
- **WebSocket (global)**: `wss://15qmk9hof3.execute-api.us-east-1.amazonaws.com/prod/`
  (`ConfigBean`); China: `wss://lx2wz1fksl.execute-api.cn-north-1.amazonaws.com.cn/prod/`

Auth is the same signed-header scheme the rest of the app uses
(`cn.com.library.net.RetrofitManager`, an interceptor on the shared OkHttp
client — J20's `ApiDevice` rides the same manager):

```
platform: android
appid: 94a98bca981d11eb9c0824418c29779a
version: 2.2.5
accept-language: en
ts: <epoch SECONDS>            # currentTimeMillis / 1000
nonce: <uuid4 without dashes, lowercase>
sign: <MD5(appid,secret,nonce,ts) upper-case hex>
Authorization: token <projectToken>
```

The secret is `de77d4d97ecf9003eaa5f372fd9a616b` (`ServiceConfig.configBeanRelease`);
the signature input is the comma-joined `appId,appSecret,nonce,ts`. Login is
`tMemberLogin.thtml` on `client-api.xbloom.com`, whose response carries
`token` (recipe API), `projectToken` (this header's bearer) and
`projectRefreshToken`.

When any `api-iot` call answers `code: 10001`, `TokenInterceptor` calls
`PUT /api/enduser/refresh_token/` with `{"refresh_token": <projectRefreshToken>}`;
a `code: 0` answer's `data` carries a new `access_token` / `refresh_token`
pair, and the original request is retried with the new token. The app logs
out only when there is no refresh token or the refresh fails.

## Onboarding / pairing (the only BLE that Original uses)

BLE appears exactly once for Original: handing Wi-Fi credentials to a factory-
fresh machine. `SetWifiActivity` collects SSID + password, then
`PairDeviceJ20Activity.startConn()` pushes them over BLE using MXChip's lock
SDK (`com.mxchip.locklib.BleLockManager`), **not** this app's own BLE stack:

```java
// PairDeviceJ20Activity.java ~line 207
WifiBean wifiBean = new WifiBean(
    new WifiInfoBean(wifiSSID, "", "", wifiPassword, token), null, 2, null);
BleLockManager.getInstance().connect(retrofit, deviceFindBean.getBean(), wifiBean, cb);
```

A cloud `token` is bundled with the credentials so the device can authenticate
to the IoT backend once it joins Wi-Fi. After provisioning, BLE is never used
again — all control is cloud-mediated.

Those two empty strings are **constructor placeholders, not the wire values**.
`BleLockManager.AnonymousClass3` overwrites both fields from SharedPreferences
immediately before serializing the JSON (`setActivate_url(SP.ACTIVATION_URL)` /
`setBinding_code(SP.BIND_CODE)`), and those preference values are written from
the `POST /api/device/bind/code` response (`BindDeviceInfoBean.activation_url` /
`.bind_code`, set in `LockViewModel`). In other words, `activate_url` is a
**cloud-issued one-time activation/binding endpoint**, not a server the app or a
user picks. It is not the ongoing control channel either — control always goes
through the thing-shadow endpoints below. A custom activation URL would at most
intercept the one-time activation call, not redirect the machine's control path.

## Firmware / OTA

**No app-side Original firmware download exists.** The `.bin` upgrade path —
`tUpToDateFirmwareVersion.thtml` → `resourceLinks` → local
`FW_J15<version>.bin` → `AppBleManager.initYModem` → BLE `8101` — is gated on
`device.isJ15()` in `MachinesActivity.getUpToDateFirmwareVersionTransfer()` and
is only reachable from `MachineAdvancedFeaturesJ15Activity` / `FwUpgradeActivity`.
The J20 side has no upgrade screen and no OTA call: it only reads
`multimcuotainfo.mcu_info[].{mcu_name, mcu_version}` (PowerMCU / PannelMCU) out
of the thing shadow and uses `J20Firmware.isLegacyFirmware()` to hide features
on old firmware. Original firmware updates are therefore device/cloud-initiated;
the app's upgrade path cannot be used to fetch an Original image.

## REST endpoints (`http/j20/ApiDevice.java`)

```
POST /api/device/command        body: DeviceCommandReq          → BaseResp<Object>
GET  /api/device/detail/        query: device_id                → BaseResp<DeviceJ20InfoDetailModel>
POST /api/device/thing_shadow/  body: { device_id, product_id } → BaseResp<DeviceJ20DetailModel>
```

- **`/command`** — push a desired-state shadow update (the write path).
- **`/detail/`** — one-shot device metadata (`sn`, `device_id`).
- **`/thing_shadow/`** — full current shadow (the reported-state read path,
  `deviceFullProperties`); used for the initial snapshot before the WebSocket
  takes over for live updates. Unlike `/command`, its `product_id` is the
  device's own `productId` from the machine list (`getJ20Info`).

## Command model (the write path)

`req/DeviceCommandReq.java` — the POST body for `/api/device/command`:

```
DeviceCommandReq {
  device_id:  <device.deviceId>
  product_id: "53DDY4"            // DeviceType.getJ20ProductId(), constant
  desired:    <shadow document>   // see below
  home_id:    null                // observed null at every call site
}
```

The `desired` object is a nested AWS-IoT shadow document —
`model/Desired.java` → `State` → `BaseDesiredInside` — serializing to:

```json
{ "state": { "desired": { <one of the *DesiredInside payloads> } } }
```

Confirmed payload shapes (each `*DesiredInside` extends `BaseDesiredInside`), with
the call site that builds them:

| Action | Desired payload | Built at |
|---|---|---|
| **Grind** | `{ "grinding_start": 1, "setting_grind_size": <1–30, default 15> }` | `GrinderJ20Activity` L374 |
| **Grinder calibrate** (auto-zero) | `{ "auto_calibrate": 1 }` | `CalibrateGrinderJ20Activity` L84 |
| **Pour / brew** | `{ "bruw_curve": "FFFF1005<vol:4><temp:2>1E<pattern>" }` | `BrewerJ20Activity` L852 |
| **Stop water** | `{ "bruw_curve": "FFFF11" }` | `BrewerJ20Activity` L878 |

Notes:

- `setting_grind_size` is the grind number parsed straight from the UI text
  field. `CoffeeConstantUtil.getGrindSizeJ20` fixes the seekbar at **1–30,
  default 15** (not Studio's 1–80).
- `bruw_curve` (sic — the app's own typo for "brew curve") is a hex string, not
  a struct. `BrewerJ20Activity.startWater` builds it exactly as
  `"FFFF1005" + volumeHex4 + temperatureHex2 + "1E" + patternCode`:
  - volume is the seekbar value in **ml**, range **30–500**, default **225**,
    upper-case hex, zero-padded to 4 digits;
  - temperature is the seekbar value in **°C**, range **85–96**, default
    **85**; at/below 85 the fixed `RTJ20 = 85` is sent, at/above 96 the fixed
    `BPJ20 = 95`, otherwise the integer itself, upper-case hex, zero-padded
    to 2 digits;
  - `"1E"` is the fixed literal 30;
  - pattern: center `"00"`, circular `"01"`, spiral `"02"`.
  Example: 225 ml at 93 °C center → `FFFF100500E15D1E00`.
  `"FFFF11"` is the stop sentinel.
- `home_id` is a data-class field but null at all traced call sites.

## State / telemetry (the read path)

`manager/AppWsManager.java` opens the WebSocket and parses each frame's
`state.reported` object, re-broadcasting fields onto the app's `RxBus`. The
reported fields it handles (the de-facto Original telemetry schema):

| `state.reported.*` | meaning | notes |
|---|---|---|
| `is_online` | machine online flag | → `DeviceJ20OnlineEvent` |
| `work_mode` | power/wake + activity state | `0` idle, `1` awake, `4/7/11/13` grinding, `5` ready (pod inserted), `6/8/14/15` brewing, `9` sleeping, `10/12` complete |
| `warning` | error/warning code | `162/176` grind-setting alert, `169` water low, `171` xPod dock alert, `182` no beans, `190` water shortage, `191–193` incompatible voltage; `166/183/185` ignored by the app |
| `grinding_start` | grinder run state | → `DeviceJ20GrinderStartEvent` |
| `auto_calibrate` | calibration state | mirrors the calibrate command field |
| `capsule_rfid_tag` | inserted pod's RFID/`xid` | `"IF0002"` treated as "no pod"; other values open the pod detail page |
| `disp_reserve` | dispense/brew progress | `<3` brew-progress (0/1/2), `3` recipe-changed, `4` start Easy-Mode recipe, `6` idle/ignore |

- The reported state is the reliable status source — same lesson as Studio's raw
  status heartbeat vs. cmd-tagged frames (`[[xbloom-raw-state-heartbeat-vs-cmd-tagged]]`).
- `thing_shadow/` gives the initial full snapshot; the WebSocket gives deltas.
- Corresponding models: `model/DeviceJ20DetailModel.java`
  (`capsule_rfid_tag`, `is_online`, `warning`, `work_mode`, `DM_SN`,
  `multimcuotainfo` → per-MCU `mcu_name`/`mcu_version`), `DeviceJ20Model.java`
  (`product_id`, `mac`, `device_id`, `enduser_id`, `sn`).

## Relationship to the Studio BLE protocol — none of it ports

For anyone tempted to add Original as a flag on the existing code path: the two
share **no transport surface**.

| Concern | Studio (J15, this integration) | Original (J20) |
|---|---|---|
| Link | Bluetooth LE via HA Bluetooth stack | Wi-Fi, cloud-mediated |
| Framing | `0x58 0x02` packets, CRC, MTU `8100` handshake | JSON over HTTPS + WebSocket |
| Command table | `APP_*`/`RD_*` ids, type-1/type-2 markers | shadow `desired` documents |
| State | raw BLE status heartbeat | cloud `state.reported` push |
| Auth | none (local BLE) | signed cloud token per request |
| HA building block | `bluetooth` matcher + `coordinator/connection.py` | a cloud stream client (`DataUpdateCoordinator` polling `thing_shadow` + WS) |

So Original support is effectively a **second, cloud-only integration** sharing
only the recipe store and the `_cloud_client.py`-style HTTP plumbing — not a
small branch on the BLE coordinator. It now lives in `j20/` as a separate
config-entry type; this document is the decompile-derived map behind it.

## Open questions (blocked without Original hardware)

- The WebSocket URL, subprotocol, subscribe/auth handshake, and reconnect/ping
  cadence (`WsManager` is a vendored `com.mixchip.websocketclient`).
- Whether tea recipes have a J20 desired-state form at all.
