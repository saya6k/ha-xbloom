# Implementations

Language ports of the xBloom BLE protocol. Each port lives in its own
top-level directory and is independently versioned.

| Language | Path | Coverage | Notes |
| --- | --- | --- | --- |
| Python | [`./python/`](./python/) | Brewing, one-shot commands, Easy Mode slots, live monitoring, machine-info decoding | Reference implementation. Depends on [`bleak`](https://github.com/hbldh/bleak) for cross-platform BLE. |

## Adding a port

Ports are welcome in any language. The ideal PR:

1. Adds a sibling directory (`rust/`, `swift/`, `node/`, ...).
2. Implements at least: handshake, machine-info decoding, one-shot commands,
   and recipe encoding for a basic multi-pour brew. Easy Mode slots and live
   monitoring are nice-to-have.
3. Includes a short `README.md` in that directory showing how to install and
   run. Mirror the structure of `./python/README.md` where it makes sense.
4. Adds a row to the table above. Be honest about coverage.
5. Does not re-document the wire protocol; `PROTOCOL.md` is the single
   source of truth. Cross-reference packet builders to the relevant
   `PROTOCOL.md` section in code comments.

No test harness or reference machine is required for the port to be merged;
community review of packet bytes against captures in `PROTOCOL.md` is the
bar. A note on which firmware / unit the port has been tested against is
appreciated.
