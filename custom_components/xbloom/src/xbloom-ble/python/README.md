# xbloom-ble — Python reference client

Reference implementation of the xBloom BLE protocol. See [`../PROTOCOL.md`](../PROTOCOL.md)
for the wire-level details these scripts encode.

## Install

```bash
pip install -r requirements.txt
```

Tested on macOS (CoreBluetooth via [`bleak`](https://github.com/hbldh/bleak))
and Linux (BlueZ). Windows should also work since `bleak` abstracts the
backend, but it has not been tested.

## Usage

**Before every run**: disconnect the machine in the official app (see [the
top-level note](../README.md#try-it)), otherwise the scripts below will time
out.

### Brew a recipe — `brew.py`

```bash
# 3-pour recipe, grinder OFF, 93°C for all pours:
python3 brew.py --no-grind --temp 93 \
    --pour 20 15 \
    --pour 30 10 \
    --pour 30 0

# Save the recipe to JSON and brew:
python3 brew.py --save ./my-recipe.json --dose 17 --no-grind --temp 91 \
    --pattern spiral --pour 50 20 --pour 50 10 --pour 50 0

# Brew from a saved recipe (two examples ship with the repo):
python3 brew.py --load recipe-examples/simple.json
python3 brew.py --load recipe-examples/example2.json

# Dry run — print the encoded packets without connecting:
python3 brew.py --dry-run --no-grind --temp 93 --pour 40 15 --pour 40 0
```

Each `--pour VOLUME POST_WAIT` is a pour in ml plus a post-pour wait in
seconds. The first pour is the bloom. Per-pour temperature / pattern /
vibration overrides are supported via the JSON recipe format.

### One-shot commands — `send_command.py`

```bash
python3 send_command.py --help          # list all subcommands
python3 send_command.py home            # return the UI to the home screen
python3 send_command.py tare            # zero the scale
python3 send_command.py easy            # switch machine to Auto/Easy Mode
python3 send_command.py pro             # switch machine to Pro Mode
python3 send_command.py temp-c          # display temperature in °C
python3 send_command.py grind 4         # grind for 4 seconds (default size/speed)
python3 send_command.py grind 4 --size 55 --speed 110
```

Write an Easy Mode slot (A / B / C) from an inline pour list or a JSON
recipe:

```bash
python3 send_command.py slot A --scale on --no-grind --temp 91 \
    --pour 50 20 --pour 50 10 --pour 50 0
python3 send_command.py slot B --load recipe-examples/example2.json --scale on
```

`send_command.py slot --help` and `send_command.py grind --help` show the
full argument sets.

### Live monitor — `monitor.py`

Connects, sends the handshake, and decodes every incoming BLE notification
(weights, pour events, errors, mode changes) until you press Ctrl+C. Useful
for watching the machine during a brew or for protocol debugging.

```bash
python3 monitor.py
```

## Env vars

- `XBLOOM_BLE_ADDRESS` — optional. If set, `scan_and_connect()` falls back
  to this address when BLE scan misses the device. Typical value on macOS is
  the peripheral's CoreBluetooth UUID; on Linux / Windows it's the BLE MAC
  address. Useful on macOS when CoreBluetooth has cached the peripheral and
  `discover()` returns empty on a subsequent scan.

## Module layout

- `xbloom.py` — shared module: packet builders, CRC16, recipe encoder,
  constants (`CMD_*`, `MODE_*`), notification decoder, and a
  `scan_and_connect` / `xbloom_session` context manager.
- `brew.py` — CLI for brewing a recipe.
- `send_command.py` — CLI for one-shot commands, grinder, Easy Mode slots.
- `monitor.py` — live BLE notification decoder.
- `recipe-schema.json` — JSON schema used by `validate_recipe()` in
  `xbloom.py`. Kept alongside it so the loader (`os.path.dirname(__file__)`)
  finds it.
- `recipe-examples/` — ready-to-brew JSON recipes:
  `simple.json` (5-pour, 15 g, 1:16, all defaults — the quickest "does it work?" recipe) and
  `example2.json` (6-pour spiral, 17 g, 1:16, xDripper cup, grinder on, with per-pour
  temperature / pattern / vibration / flow-rate overrides — a tour of every recipe field).

See [`../PROTOCOL.md`](../PROTOCOL.md) for what's actually being sent over
the wire.
