#!/usr/bin/env python3
"""
xBloom BLE Control — send one-shot commands, grind, or save an Easy Mode slot.

Run with no args for a summary, or --help for the full command list. Every
subcommand has its own --help (e.g. `./send_command.py slot --help`).

IMPORTANT: close / disconnect the phone app before running — BLE allows only
one central at a time.
"""

import argparse
import asyncio
import json
import signal
import sys
from typing import Callable, NamedTuple

from xbloom import (
    WRITE_UUID,
    CMD_MODE_TYPE, CMD_BREW_PAUSE, CMD_BREW_STOP, CMD_BACK_TO_HOME,
    CMD_GRINDER_ENTER, CMD_GRINDER_START, CMD_GRINDER_STOP,
    CMD_TARE, CMD_UNIT_WEIGHT, CMD_UNIT_TEMP, CMD_WATER_SOURCE,
    MODE_PRO, MODE_EASY,
    build_packet_type1, build_packet_type2, build_slot_packet, slot_flags,
    encode_recipe, validate_recipe, recipe_dict_to_pours, format_pour_overrides,
    decode_notification, xbloom_session,
)


# ── Signal handling ──────────────────────────────────────────────────────────
# Only install when running as main script — importing from a host process
# must NOT hijack its signal handling.
def _handle_signal(sig, frame):
    sys.exit(0)
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


# ── Default notification handler ─────────────────────────────────────────────
def default_on_notify(sender, data: bytearray):
    raw = bytes(data)
    cmd, name, _payload = decode_notification(raw)
    label = name or "UNKNOWN"
    print(f"  << [{cmd:5d}] {label}  raw={raw.hex().upper()}")


# ── Simple-command table ─────────────────────────────────────────────────────
class SimpleCmd(NamedTuple):
    description: str
    build: Callable[[], bytes]


SIMPLE_COMMANDS: dict[str, SimpleCmd] = {
    "easy":       SimpleCmd("Switch to Auto/Easy Mode",
                            lambda: build_packet_type2(CMD_MODE_TYPE, MODE_EASY)),
    "pro":        SimpleCmd("Switch to Pro Mode",
                            lambda: build_packet_type2(CMD_MODE_TYPE, MODE_PRO)),
    "pause":      SimpleCmd("Pause brew (J15)",
                            lambda: build_packet_type1(CMD_BREW_PAUSE)),
    "stop":       SimpleCmd("Stop brew (J15)",
                            lambda: build_packet_type1(CMD_BREW_STOP)),
    "home":       SimpleCmd("Back to home screen",
                            lambda: build_packet_type1(CMD_BACK_TO_HOME)),
    "tare":       SimpleCmd("Tare / zero scale",
                            lambda: build_packet_type1(CMD_TARE)),
    "unit-g":     SimpleCmd("Weight unit → grams",
                            lambda: build_packet_type1(CMD_UNIT_WEIGHT, [0])),
    "unit-ml":    SimpleCmd("Weight unit → ml",
                            lambda: build_packet_type1(CMD_UNIT_WEIGHT, [2])),
    "unit-oz":    SimpleCmd("Weight unit → oz",
                            lambda: build_packet_type1(CMD_UNIT_WEIGHT, [1])),
    "temp-c":     SimpleCmd("Temp unit → °C",
                            lambda: build_packet_type1(CMD_UNIT_TEMP, [0])),
    "temp-f":     SimpleCmd("Temp unit → °F",
                            lambda: build_packet_type1(CMD_UNIT_TEMP, [1])),
    "water-tank": SimpleCmd("Water source → tank",
                            lambda: build_packet_type1(CMD_WATER_SOURCE, [0])),
    "water-tap":  SimpleCmd("Water source → tap",
                            lambda: build_packet_type1(CMD_WATER_SOURCE, [1])),
}


# ── Async runners ────────────────────────────────────────────────────────────
async def _run_simple(packet: bytes, name: str):
    print("\nConnecting to xBloom...")
    try:
        async with xbloom_session(on_notify=default_on_notify) as (client, info):
            print(f"  Connected! firmware={info.get('firmware')}")
            print(f"\nSending '{name}':")
            print(f"  >> Hex: {packet.hex().upper()}")
            await client.write_gatt_char(WRITE_UUID, packet, response=False)
            print("  >> Sent.")
            print("\nWaiting for response (3 s)...")
            await asyncio.sleep(3.0)
            print("Done.")
    except ConnectionError as e:
        print(f"  {e}\n\nMake sure the phone app is disconnected and the machine is on.")


async def _run_grind(seconds: float, grind_size: int, speed: int):
    enter_pkt = build_packet_type1(CMD_GRINDER_ENTER, [grind_size, speed])
    start_pkt = build_packet_type1(CMD_GRINDER_START, [1000, grind_size, speed])
    stop_pkt  = build_packet_type1(CMD_GRINDER_STOP)

    print("\nConnecting to xBloom...")
    try:
        async with xbloom_session(on_notify=default_on_notify) as (client, info):
            print(f"  Connected! firmware={info.get('firmware')}")
            print(f"\nEntering grinder mode (size={grind_size}, speed={speed})...")
            await client.write_gatt_char(WRITE_UUID, enter_pkt, response=False)
            await asyncio.sleep(0.5)
            print(f"Starting grind for {seconds}s...")
            print(f"  >> {start_pkt.hex().upper()}")
            await client.write_gatt_char(WRITE_UUID, start_pkt, response=False)
            await asyncio.sleep(seconds)
            print("Stopping grinder...")
            await client.write_gatt_char(WRITE_UUID, stop_pkt, response=False)
            await asyncio.sleep(1.5)
            print("Done.")
    except ConnectionError as e:
        print(f"  {e}")


async def _run_slot(args):
    slot_index = {"A": 0, "B": 1, "C": 2}[args.slot]
    scale_on = args.scale == "on"

    if args.load:
        with open(args.load) as f:
            recipe = json.load(f)
        errors = validate_recipe(recipe)
        if errors:
            print(f"\nInvalid recipe in {args.load}:")
            for e in errors:
                print(f"  - {e}")
            return
        temperature = recipe.get("temperature", 91)
        grinder = recipe.get("grinder")
        grinder_size = grinder["size"] if grinder else 0
        pattern = recipe.get("pattern", "center")
        vibration = recipe.get("vibration", "none")
        flow_rate = recipe.get("flow_rate", 3.0)
        pours = recipe_dict_to_pours(recipe)
        print(f"\n  Loaded from {args.load}")
    else:
        if not args.pour:
            print("Error: specify at least one --pour (or use --load)")
            return
        temperature = args.temp
        grinder_size = args.grind if args.grind else 0
        pattern = args.pattern
        vibration = args.vibration
        flow_rate = 3.0
        vib_before = vibration in ("before", "both")
        vib_after  = vibration in ("after", "both")
        pours = []
        for vol, post_wait in args.pour:
            pours.append({
                "volume":           vol,
                "temperature":      temperature,
                "post_wait":        int(post_wait),
                "pattern":          pattern,
                "vibration_before": vib_before,
                "vibration_after":  vib_after,
                "flow_rate":        flow_rate,
            })

    recipe_hex = encode_recipe(pours, grinder_size=grinder_size)
    flags = slot_flags(scale_on, grinder_size > 0)
    packet = build_slot_packet(slot_index, flags, recipe_hex)

    print(f"\n--- Slot {args.slot} -------------------------------------------------")
    print(f"  Scale:     {'ON' if scale_on else 'OFF'}")
    print(f"  Grinder:   {'OFF' if grinder_size == 0 else grinder_size}")
    print(f"  Temp:      {temperature:.0f} C")
    print(f"  Total:     {sum(p['volume'] for p in pours):.0f} ml")
    defaults = {"temperature": temperature, "pattern": pattern,
                "vibration": vibration, "flow_rate": flow_rate}
    for i, p in enumerate(pours):
        label = "Bloom" if i == 0 else f"Pour {i+1}"
        overrides = format_pour_overrides(p, defaults)
        suffix = f"  {overrides}" if overrides else ""
        print(f"  {label:<8}  vol={p['volume']:.0f}ml  wait={p['post_wait']}s{suffix}")
    print(f"  Flags:     0x{flags:02X}")
    print(f"  Packet:    {packet.hex().upper()}")

    await _run_simple(packet, f"Slot {args.slot} recipe")


# ── Argparse ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="send_command.py",
        description="Send one-shot commands to the xBloom coffee machine over BLE.",
        epilog=(
            "IMPORTANT: close / disconnect the phone app first — BLE allows only\n"
            "one central at a time, so the machine will appear offline otherwise."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    for name, spec in SIMPLE_COMMANDS.items():
        sp = subparsers.add_parser(name, help=spec.description, description=spec.description)
        sp.set_defaults(
            _runner=lambda args, s=spec, n=name: asyncio.run(_run_simple(s.build(), n)),
        )

    grind_p = subparsers.add_parser(
        "grind",
        help="Run the grinder for N seconds",
        description="Run the grinder for N seconds at a given size/speed.",
    )
    grind_p.add_argument("seconds", type=float, help="Grind duration in seconds")
    grind_p.add_argument("--size", type=int, default=63,
                         help="Grinder size 1-100 (default: 63)")
    grind_p.add_argument("--speed", type=int, default=100,
                         help="Grinder speed RPM (default: 100)")
    grind_p.set_defaults(
        _runner=lambda args: asyncio.run(_run_grind(args.seconds, args.size, args.speed)),
    )

    slot_p = subparsers.add_parser(
        "slot",
        help="Save a recipe to an Easy Mode slot (A/B/C)",
        description="Save a recipe to an Easy Mode slot (A/B/C).",
    )
    slot_p.add_argument("slot", choices=["A", "B", "C"], help="Slot to save to")
    slot_p.add_argument("--scale", choices=["on", "off"], default="on",
                        help="Scale display for this slot (default: on)")
    slot_p.add_argument("--load", metavar="FILE",
                        help="Load recipe from JSON file (as saved by brew.py --save)")
    slot_grind = slot_p.add_mutually_exclusive_group()
    slot_grind.add_argument("--grind", type=int, metavar="SIZE",
                            help="Grinder size 1-100")
    slot_grind.add_argument("--no-grind", action="store_true",
                            help="Grinder OFF (default)")
    slot_p.add_argument("--temp", type=float, default=91,
                        help="Brew temperature in C for all pours (default: 91)")
    slot_p.add_argument("--pattern", choices=["center", "circular", "spiral"],
                        default="center", help="Pour pattern (default: center)")
    slot_p.add_argument("--vibration", choices=["none", "before", "after", "both"],
                        default="none", help="Vibration mode (default: none)")
    slot_p.add_argument("--pour", nargs=2, metavar=("VOL", "WAIT"),
                        type=float, action="append", default=[],
                        help="Pour: volume ml, post-wait seconds (repeatable)")
    slot_p.set_defaults(_runner=lambda args: asyncio.run(_run_slot(args)))

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        print("xBloom BLE Control")
        print("Usage: ./send_command.py <command>")
        print("Run `./send_command.py --help` for the full command list.")
        print()
        print("IMPORTANT: close / disconnect the phone app first.")
        return

    args._runner(args)


if __name__ == "__main__":
    main()
