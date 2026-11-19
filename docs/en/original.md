# xBloom Original (J20)

The Original is a Wi-Fi machine with **no local control path**. The official
app never talks to it directly: commands go to xBloom's cloud as a *desired
thing-shadow* update, and the machine's reported state comes back from the
same cloud. This integration's Original support is a client of that cloud,
not a Bluetooth or LAN client.

> **Status only (experimental).** Home Assistant shows an Original's status
> but cannot control it yet. The control features (grind, pour, stop,
> grinder calibration, saved recipes, the start-button auto mode and the
> first-time Wi-Fi setup over Bluetooth) are implemented from the official
> app's code but have never run on a real machine, so they are kept out of
> releases on the repository's
> [`original`](https://github.com/saya6k/ha-xbloom/tree/original) branch.
> If you own an Original and can help test them, see
> [issue #ISSUE](https://github.com/saya6k/ha-xbloom/issues/ISSUE).
>
> The login flow, request signing and endpoints were read from app 2.2.5 by
> decompilation; see [`protocol-original-j20.md`](./protocol-original-j20.md).

## Setup

1. Set the machine up with the official xBloom app first (Wi-Fi and account).
2. Settings → Devices & Services → **Add Integration** → **xBloom Coffee
   Machine**.
3. Choose **Original (Wi-Fi / cloud, status only)**.
4. Enter the email/password of the xBloom app account that owns the machine.
5. If the account owns more than one Original, pick one.

When the cloud rejects the token, the integration first exchanges the refresh
token for a new one, as the app does; the credentials are stored in the config
entry only so it can log in again when that also fails. Remove the entry to
remove them.

Nothing is ever written to the machine: the integration only logs in, lists
the account's machines and reads the thing shadow.

While the shadow reports the machine offline, its live readings (state,
warning, problem, grinding, brew progress, pod) read **unknown** — the
shadow still holds the last reports, which aren't the current state.
Entities are unavailable only when the xBloom cloud can't be reached at all.

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

Live changes arrive over the app's WebSocket
(`wss://15qmk9hof3.execute-api.us-east-1.amazonaws.com/prod/`, the IoT token
in an `Authorization` header, a ping every 30 s; the server pushes
`{device_id, state: {reported: {…}}}` with only the changed fields). Polling
stays on underneath as a fallback; its interval defaults to 15 s and can be
changed in the entry's options (5–300 s).

## Sidebar panel and voice / chat agents

The xBloom sidebar panel lists the Original next to any Studio. It shows the
machine's status on its **Settings** tab, along with the app's descaling
steps (descaling has no command in the app either; it is done with the
button on the back of the machine).

The Original also gets its own LLM API, named like the machine ("xBloom
Original <last six serial characters>"), selectable in a conversation
agent's settings. It offers only `xbloom__get_status`.

## Troubleshooting

- **“No xBloom Original found on this account.”** The account has no J20, or
  `tuMachineList.tuhtml` returned an unexpected shape. Check the HA log for
  the warning from `j20/client.py`.
- **Invalid email/password.** Use the xBloom app's own login, not the
  Shopify store account.
- **All values stay `unknown`.** The machine is offline, or the shadow
  request is failing; look for `XBloom Original shadow failed` or the
  network warning in the log.

## Why not local?

See [`protocol-original-j20.md`](./protocol-original-j20.md) — the machine's
only non-cloud path is the factory Wi-Fi provisioning BLE link, which is
dropped after binding. `activate_url` in that provisioning payload is a
cloud-issued one-time activation endpoint, not a server selector for control.
