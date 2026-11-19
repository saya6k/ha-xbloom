# xBloom Original (J20)

The Original is a Wi-Fi machine with **no local control path**. The official
app never talks to it directly: commands go to xBloom's cloud as a *desired
thing-shadow* update, and the machine's reported state comes back from the
same cloud. This integration's Original support is a client of that cloud,
not a Bluetooth or LAN client.

> **Experimental.** There is no Original hardware and no live account in this
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

### A new machine (first-time Wi-Fi setup)

An Original that hasn't been set up yet waits for its Wi-Fi settings over
Bluetooth. If Home Assistant has Bluetooth in range, the machine shows up in
**Discovered** as *xBloom Original*. Configure it, enter the xBloom account and
the Wi-Fi network (2.4 GHz only), and Home Assistant does what the app does:
it sends the Wi-Fi settings to the machine, waits for it to link to the
account, adds it to the account's machine list and creates the entry. This
uses the app's onboarding protocol
([`protocol-original-j20.md`](./protocol-original-j20.md#wire-protocol-mxchip-locklib-implemented-in-modelsj20provisionpy))
and has not been tried on a real machine.

When the cloud rejects the token, the integration first exchanges the refresh
token for a new one, as the app does; the credentials are stored in the config
entry only so it can log in again when that also fails. Remove the entry to
remove them.

While the shadow reports the machine offline, its live readings (state,
warning, problem, grinding, brew progress, pod) read **unknown** — the
shadow still holds the last reports, which aren't the current state — and
the buttons refuse to send anything (the app disables its start buttons too):
a command written then would wait in the shadow and run when the machine
reconnects. Entities are unavailable only when the xBloom cloud can't be
reached at all.

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
| `select.…_recipe` | the saved recipe the run button brews |
| `button.…_execute_recipe` | run the selected recipe — first press checks the machine and prepares the run, second press starts it |

Manual pour uses the app's exact encoding:
`FFFF1005` + volume (4 hex) + temperature (2 hex) + `1E` + pattern
(center `00` / circular `01` / spiral `02`). Volume is 30–500 ml
(default 225); temperature is 85–96 °C (default 85), with the fixed RT 85 /
BP 95 constants at the ends.

Live changes arrive over the app's WebSocket
(`wss://15qmk9hof3.execute-api.us-east-1.amazonaws.com/prod/`, the IoT token
in an `Authorization` header, a ping every 30 s; the server pushes
`{device_id, state: {reported: {…}}}` with only the changed fields). Polling
stays on underneath as a fallback; its interval defaults to 15 s and can be changed in the entry's options
(5–300 s).

## Recipes

The Original keeps its own local recipe store, like a Studio: on first setup
it is seeded once with the xBloom account's recipes, and recipes can be
created, edited, imported from a share link or the community hub, and
exported from the panel, the `xb.*` services or a conversation agent. Tea
recipes are left out — the Original has no tea brewer.

Running a recipe follows the app (`RecipeDetailActivity`, unverified on
hardware):

1. **Check.** The machine must be online, without a water warning, and
   ready to brew (`work_mode` 5), or just woken (1) on current firmware.
   On legacy firmware (an MCU outside 3.0.0–8.0.0) a recipe that skips the
   grinder is refused.
2. **Prepare.** The xBloom cloud turns the recipe (name, grind, ratio, dose,
   pours) into the machine's run command (`tGetRecipeMadeCmd.thtml`).
3. **Start.** After a second press (or `confirmed=true`), that command goes
   to the machine unchanged (`POST /api/device/command`); grinding, brewing
   and complete then show up in the state sensor.

The `xb.execute_recipe` service does all three in one call. Per-brew
overrides work as on the Studio for dose, ratio, temperature and single
pours; the Original's run command has no bypass water, and a recipe's grind
size is sent as stored (the cloud converts it to the Original's 1–30
scale), so `grind_size`/`rpm` overrides don't apply.

## Sidebar panel and voice / chat agents

The xBloom sidebar panel lists the Original next to any Studio, with the
**Brew**, **Recipes**, **Manual** and **Settings** tabs. Brew runs the
selected recipe in two presses (prepare, then start) and follows the
machine through grinding and brewing; Easy slots and the scale only appear
for a Studio.

The Original also gets its own LLM API ("XBloom Coffee Machine (<serial>)"),
selectable in a conversation agent's settings. It offers only the tools the
machine supports: status, the recipe tools (list, get, create, edit, delete,
execute, import, export, community search), `xbloom__grind` (1–30),
`xbloom__pour` (85–95 °C or boiling, 30–500 ml), `xbloom__cancel` and
`xbloom__calibrate_grinder`. Recipes, grind and pour ask for confirmation
first, as on the Studio; the Original has no "armed" page, so nothing
reaches the machine until the confirmed call. `xbloom__cancel` stops a
pour, but a running grind can't be stopped — the app has no command for it
either.

## What is not implemented

- **Firmware updates.** The app has no update path for the Original either:
  its firmware is updated by the machine and the cloud themselves. The
  power and panel MCU versions are shown as sensors.
- **Local control.** The app never talks to the Original on the LAN; after
  the Wi-Fi setup every command goes through the cloud, so there is nothing
  local to discover or control.

## Auto mode (the machine's start button)

Like the app, one recipe can be stored on the Original's own start button so
it brews without Home Assistant: the panel's Settings tab (**Auto mode**),
or the `xb.write_recipe_to_auto_mode` / `xb.reset_auto_mode` services.
Writing follows `EasyModeJ20ViewModel` (unverified on hardware):

1. the machine's record is read (`tuAutoMode.tuhtml`, keyed by its serial,
   its account machine id and the member's country);
2. a recipe that isn't on the xBloom account yet is exported first;
3. the cloud builds the auto-mode command (`tGetAutoModeRecipeMadeCmd.thtml`,
   `xid: "IF0002"`), which goes to the machine like any command;
4. the account's record is updated (`tuAutoModeSync.tuhtml`).

Reset takes the factory recipe from `tuAutoModeInit.tuhtml`, sends its
command and records it with `tuAutoModeReset.tuhtml`.

## Maintenance

- **Grinder calibration** — `button.…_calibrate` or the panel. As in the
  app, it's refused while the machine is asleep (tap the xPod dock to wake
  it).
- **Descaling** has no command in the app; it is done on the machine with
  the button on the back. The panel's Settings tab shows the app's steps.

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
