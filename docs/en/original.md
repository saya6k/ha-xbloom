# xBloom Original (J20)

The Original is a Wi-Fi machine with **no local control path**. The official
app never talks to it directly: commands go to xBloom's cloud as a *desired
thing-shadow* update, and the machine's reported state comes back from the
same cloud. This integration's Original support is a client of that cloud,
not a Bluetooth or LAN client.

> **Unverified.** There is no Original hardware and no live account in this
> repository. The login flow, request signing, and endpoints were read from
> app 2.2.5 by decompilation; see
> [`protocol-original-j20.md`](./protocol-original-j20.md). Everything below
> may need adjustment once it is exercised against a real machine.

## Setup

1. Settings → Devices & Services → **Add Integration** → **xBloom Coffee
   Machine**.
2. Choose **Original (Wi-Fi / cloud)**.
3. Enter the email/password of the xBloom app account that owns the machine.
4. If the account owns more than one Original, pick one.

When the cloud rejects the token, the integration first exchanges the refresh
token for a new one, as the app does; the credentials are stored in the config
entry only so it can log in again when that also fails. Remove the entry to
remove them.

While the shadow reports the machine offline, every entity except
`binary_sensor.…_online` is unavailable and the buttons refuse to send
anything (the app disables its start buttons too) — a command written then
would wait in the shadow and run when the machine reconnects.

## Entities

| Entity | Meaning |
|---|---|
| `binary_sensor.…_online` | `is_online` from the thing shadow |
| `binary_sensor.…_problem` | a `warning` the app would show (166/183/185 are ignored, as in the app) |
| `binary_sensor.…_grinding` | `work_mode` is 4/7/11/13 (the app's `isJ20Grinding`) |
| `sensor.…_state` | `work_mode` mapped to idle/awake/ready/grinding/brewing/complete/sleeping |
| `sensor.…_warning` | `warning` mapped to grind/water/dock/beans/voltage |
| `sensor.…_brew_progress` | `disp_reserve` |
| `sensor.…_pod` | `capsule_rfid_tag`; `IF0002` is treated as “no pod” |
| `sensor.…_power_mcu` / `…_panel_mcu` | per-MCU firmware versions |
| `button.…_grind` | grind at the current grind-size number (1–30) |
| `button.…_pour` | manual pour with the current volume/temperature/pattern |
| `button.…_stop` | stop pouring (`bruw_curve: "FFFF11"`) |
| `button.…_calibrate` | grinder auto-zero (`auto_calibrate: 1`) |
| `number.…_grind_size` | grind size sent by the grind button (1–30, default 15) |
| `number.…_volume` | pour volume in ml (30–500, default 225) |
| `number.…_temperature` | pour temperature in °C (85–96) |
| `select.…_pattern` | pour pattern: center / circular / spiral |

Manual pour uses the app's exact encoding:
`FFFF1005` + volume (4 hex) + temperature (2 hex) + `1E` + pattern
(center `00` / circular `01` / spiral `02`). Volume is 30–500 ml
(default 225); temperature is 85–96 °C (default 85), with the fixed RT 85 /
BP 95 constants at the ends.

The poll interval defaults to 15 s and can be changed in the entry's options
(5–300 s).

## What is not implemented

- **Recipes.** Starting a saved recipe goes through a server-generated
  `getRecipeMadeCmd` payload; only manual pour and stop are exposed.
- **The WebSocket.** The app opens `wss://…execute-api…/prod/` for live
  deltas; the subscribe/auth handshake is undocumented, so this integration
  polls the thing shadow instead.
- **Local discovery.** Port scanning and LAN control were not attempted; the
  firmware is closed and the machine is cloud-bound.
- **Services and LLM tools.** The J20 entry exposes only the entities above;
  the Studio services and Assist tools are not wired to it.

## Troubleshooting

- **“No xBloom Original found on this account.”** The account has no J20, or
  `tuMachineList.tuhtml` returned an unexpected shape. Check the HA log for
  the warning from `j20/client.py`.
- **Invalid email/password.** Use the xBloom app's own login, not the
  Shopify store account.
- **All values stay `unknown`.** The shadow request is failing; look for
  `XBloom Original shadow failed` or the network warning in the log.
- **Commands time out.** The cloud may be rejecting the signed headers.
  `sign` is MD5(`appid,appsecret,nonce,ts`) upper-cased; see
  `signed_headers()`.

## Why not local?

See [`protocol-original-j20.md`](./protocol-original-j20.md) — the machine's
only non-cloud path is the factory Wi-Fi provisioning BLE link, which is
dropped after binding. `activate_url` in that provisioning payload is a
cloud-issued one-time activation endpoint, not a server selector for control.
