# xbloom-ble

<video src="https://github.com/user-attachments/assets/a2315d00-7131-4653-a454-45319d76ead4" controls width="600"></video>

Unofficial, reverse-engineered BLE protocol spec for the xBloom J15 pour-over
coffee machine, with a working Python reference client. The [protocol
documentation](./PROTOCOL.md) is the canonical artifact; the Python code in
[`python/`](./python/) is one implementation of it. Ports to other languages
are welcome as sibling directories.

> **Disclaimer.** This project is not affiliated with, endorsed by, or sponsored
> by xBloom. It is an independent reverse-engineering effort based on publicly
> observable BLE traffic and APK decompilation of the official app. Use it at
> your own risk; the protocol may change in future firmware releases.

## What's here

| Path | Purpose |
| --- | --- |
| [`PROTOCOL.md`](./PROTOCOL.md) | **Canonical wire-protocol documentation.** Packet framing, CRC, command codes, recipe encoding, notification decoding, capture methodology. The reason this repo exists. |
| [`python/`](./python/) | Reference client: `brew.py` (run a recipe), `send_command.py` (one-shot commands + Easy Mode slot writes), `monitor.py` (live notification decoder), and `xbloom.py` (shared module). |
| [`python/recipe-examples/`](./python/recipe-examples/) | Ready-to-brew JSON recipes: `simple.json` (minimal; quickest "does it work?") and `example2.json` (full-feature showcase — grinder, per-pour temperature/pattern/vibration/flow-rate overrides). Good starting points for `brew.py --load` and `send_command.py slot --load`. |
| [`IMPLEMENTATIONS.md`](./IMPLEMENTATIONS.md) | Registry of language ports. Currently Python only. PRs welcome. |

## Try it

```bash
git clone https://github.com/brAzzi64/xbloom-ble.git
cd xbloom-ble/python
pip install -r requirements.txt

# Dry run (no machine needed; just prints the packets):
python3 brew.py --load recipe-examples/simple.json --dry-run

# For real (make sure the machine is on and the phone app disconnected; see note below):
python3 brew.py --load recipe-examples/simple.json
```

> **Heads-up.** BLE only allows one central at a time. Before running against real hardware, open the official xBloom app and explicitly **Disconnect** from the machine (not "Forget device"). Otherwise the scripts will time out looking for the machine.

`recipe-examples/simple.json` is a 15g dose, 1:16 ratio, 5-pour recipe at 93°C:

```json
{
  "dose": 15,
  "temperature": 93,
  "pours": [
    { "volume": 40, "post_wait": 30 },
    { "volume": 50, "post_wait": 20 },
    { "volume": 50, "post_wait": 20 },
    { "volume": 50, "post_wait": 20 },
    { "volume": 50, "post_wait": 0 }
  ]
}
```

See [`python/README.md`](./python/README.md) for the full CLI reference, Easy Mode slot writes, and the live monitor.

## Status

Covered by the reference implementation:
- Handshake + machine info decoding
- Brewing recipes (grinder on/off, multi-pour, per-pour temperature/pattern/vibration, post-pour wait, flow rate)
- One-shot commands: mode switch, pause/stop, tare, unit changes, water source, back-to-home, standalone grind
- Easy Mode slot writes (A / B / C)
- Live monitoring & machine-info decoding

Not yet covered:
- Firmware-update flow
- Cloud-API-dependent cup-type ranges (approximated with observed HCI values; see "Cup type" in `PROTOCOL.md`)

## Contributing

- **Port to another language?** Add a sibling directory (`rust/`, `swift/`, `node/`, ...) and update `IMPLEMENTATIONS.md`.
- **Found a protocol detail that's wrong?** Open an issue with a raw HCI capture or an APK-decompilation snippet. Concrete evidence makes the doc better.
- **Firmware-specific behavior?** Note your firmware version (visible via `monitor.py`) in the report.

## License

[MIT](./LICENSE).
