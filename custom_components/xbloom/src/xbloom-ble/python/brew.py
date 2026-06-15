#!/usr/bin/env python3
"""
xBloom Recipe Brew Script
Encodes a recipe blob, sends it to the machine, then triggers a brew.

Usage examples:
  # 3-pour recipe, grinder OFF, 93°C for all pours:
  python3 brew.py --no-grind --temp 93 \
      --pour 20 15 \
      --pour 30 10 \
      --pour 30 0

  # Save a recipe to file and brew:
  python3 brew.py --save kitasando.json --dose 17 --no-grind --temp 91 --pattern spiral \
      --pour 50 20 --pour 50 10 --pour 50 0

  # Save only (no brew):
  python3 brew.py --save kitasando.json --dry-run --dose 17 --no-grind --temp 91 \
      --pour 50 20 --pour 50 0

  # Brew from a saved recipe:
  python3 brew.py --load kitasando.json

  # Dry run -- print packets only:
  python3 brew.py --dry-run --no-grind --temp 93 --pour 40 15 --pour 40 0

Per-pour temperature control is available via the JSON recipe format (--load).
"""

import argparse
import asyncio
import json
import signal
import struct
import sys
from xbloom import (
    WRITE_UUID, NOTIFY_UUID,
    CMD_NAMES, CUP_TYPES, BrewWeightTracker,
    build_packet_type1, build_packet_type1h,
    build_bypass_packet, build_set_cup_packet,
    encode_recipe, validate_recipe, format_pour_overrides,
    recipe_dict_to_pours, recipe_to_dict,
    scan_and_connect,
)

# -- Signal handler ------------------------------------------------------------
def _handle_signal(sig, frame):
    sys.exit(0)
signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# -- Recipe file I/O -----------------------------------------------------------
def save_recipe(path, recipe_dict):
    with open(path, 'w') as f:
        json.dump(recipe_dict, f, indent=2)
    print(f"\n  Saved to {path}")

def load_recipe(path):
    with open(path) as f:
        recipe = json.load(f)
    errors = validate_recipe(recipe)
    if errors:
        print(f"\nInvalid recipe in {path}:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    return recipe

# -- BLE brew flow -------------------------------------------------------------
async def brew(recipe_hex: str, grinder_size: int, dose: float,
               total_water: float, dry_run: bool, cup_type: str = "other"):
    """Build all packets, optionally connect and send."""
    cmd_recipe = 8001 if grinder_size > 0 else 8004
    packets = [
        ("Handshake (8100)",          build_packet_type1(8100, [185, 1])),
        ("Bypass + Dose (8102)",      build_bypass_packet(dose)),
        ("Set Cup (8104)",            build_set_cup_packet(cup_type)),
        (f"Recipe hex ({cmd_recipe})", build_packet_type1h(cmd_recipe, recipe_hex)),
        ("Brew start (8002)",         build_packet_type1(8002, [])),
    ]

    print("\n--- Packets ------------------------------------------------")
    for name, pkt in packets:
        print(f"  {name}")
        print(f"    {pkt.hex().upper()}")
    print()

    if dry_run:
        print("(dry-run mode -- not connecting)")
        return

    import time as _time

    print("Connecting to xBloom...")
    try:
        client, info = await scan_and_connect()
    except ConnectionError as e:
        print(f"  {e}")
        return

    print(f"Connected! firmware={info.get('firmware')}  mode={info.get('mode')}")

    try:
        weight_tracker = BrewWeightTracker()
        brew_state = {
            'last_weight': None,
            'pour_count': 0,
            'start_time': None,
        }

        def _on_notify_track(sender, data: bytearray):
            raw = bytes(data)
            if len(raw) < 5:
                return
            cmd = struct.unpack_from('<H', raw, 3)[0]
            name = CMD_NAMES.get(cmd)

            if cmd == 20501 and len(raw) >= 14:
                raw_w = struct.unpack_from('<f', raw, 10)[0]
                brew_state['last_weight'] = weight_tracker.update(raw_w, debug=True)
            elif cmd == 40502:
                brew_state['start_time'] = _time.time()
                weight_tracker.reset()
                print(f"  <- [{cmd:5d}] Coffee Starting")
            elif cmd == 40510:
                brew_state['pour_count'] += 1
                label = "Bloom" if brew_state['pour_count'] == 1 else f"Pour {brew_state['pour_count']}"
                elapsed = ""
                if brew_state['start_time']:
                    s = _time.time() - brew_state['start_time']
                    elapsed = f"  (t={int(s)}s)"
                print(f"  <- [{cmd:5d}] {label}{elapsed}")
            elif name:
                if cmd == 40515 and len(raw) >= 14:
                    w = struct.unpack_from('<f', raw, 10)[0]
                    print(f"  <- [{cmd:5d}] {name}: {w:.1f} g")
                elif cmd not in (20501, 40523):
                    print(f"  <- [{cmd:5d}] {name}")
            else:
                print(f"  <- [{cmd:5d}] UNKNOWN  raw={raw.hex().upper()}")

        await client.stop_notify(NOTIFY_UUID)
        await client.start_notify(NOTIFY_UUID, _on_notify_track)

        for name, pkt in packets:
            print(f"  -> {name}")
            await client.write_gatt_char(WRITE_UUID, pkt, response=False)
            await asyncio.sleep(2.0)

        print("\nBrew started! Waiting for completion (Ctrl+C to abort)...")
        brew_done = asyncio.Event()

        def _on_notify_done(sender, data: bytearray):
            _on_notify_track(sender, data)
            raw = bytes(data)
            if len(raw) >= 5:
                cmd = struct.unpack_from('<H', raw, 3)[0]
                if cmd in (40511, 40512, 40513):
                    brew_done.set()

        await client.stop_notify(NOTIFY_UUID)
        await client.start_notify(NOTIFY_UUID, _on_notify_done)

        try:
            await asyncio.wait_for(brew_done.wait(), timeout=600)
            elapsed_s = _time.time() - brew_state['start_time'] if brew_state['start_time'] else 0
            mins, secs = divmod(int(elapsed_s), 60)
            print(f"\n--- Brew Summary -------------------------------------------")
            print(f"  Total time:     {mins}:{secs:02d}")
            print(f"  Water programmed: {total_water:.0f} ml")
            if brew_state['last_weight'] is not None:
                print(f"  Final weight:   {brew_state['last_weight']:.1f} g")
                diff = brew_state['last_weight'] - total_water
                print(f"  Difference:     {diff:+.1f} g")
            print(f"  Pours completed: {brew_state['pour_count']}")
        except asyncio.TimeoutError:
            print("\nTimed out waiting for brew to complete.")
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
    finally:
        await client.stop_notify(NOTIFY_UUID)
        await client.disconnect()
        print("Disconnected.")

# -- CLI -----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Send a recipe to xBloom and start a brew.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--pour", nargs=2, metavar=("VOLUME", "POST_WAIT"),
        type=float, action="append", default=[],
        help="Pour: volume ml, post-pour wait seconds. "
             "Repeat for multiple pours. First --pour is the bloom phase."
    )

    parser.add_argument(
        "--dose", type=float, default=0,
        help="Coffee dose in grams (sent to machine via cmd 8102). Default: 0"
    )

    grind_group = parser.add_mutually_exclusive_group()
    grind_group.add_argument("--grind", type=int, metavar="SIZE",
                             help="Grinder size 1-100")
    grind_group.add_argument("--no-grind", action="store_true",
                             help="Grinder OFF (default)")

    parser.add_argument("--rpm", type=int, default=0,
                        help="Grinder speed RPM (60-120, step 10). Default: 0")

    parser.add_argument("--temp", type=float, default=91,
                        help="Brew temperature in C for all pours (default: 91). "
                             "For per-pour temperatures, use --load with a JSON recipe.")
    parser.add_argument("--pattern", choices=["center", "circular", "spiral"],
                        default="center",
                        help="Pour pattern (default: center)")
    parser.add_argument("--vibration", choices=["none", "before", "after", "both"],
                        default="none",
                        help="Vibration mode (default: none)")
    parser.add_argument("--flow-rate", type=float, default=3.0,
                        help="Water flow speed 3.0-3.5 (default: 3.0)")
    parser.add_argument("--cup-type", choices=CUP_TYPES, default="other",
                        help="Cup type (default: other)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print packets only, do not connect")
    parser.add_argument("--save", metavar="FILE",
                        help="Save recipe to JSON file")
    parser.add_argument("--load", metavar="FILE",
                        help="Load recipe from JSON file (ignores --pour/--dose/etc)")

    args = parser.parse_args()

    # Load from file or build from CLI args
    if args.load:
        recipe = load_recipe(args.load)
        print(f"\n  Loaded from {args.load}")
        cup_type = recipe.get("cup_type", "other")
        dose = recipe.get("dose", 0)
        grinder = recipe.get("grinder")  # None or {"size": N, "rpm": N}
        grinder_size = grinder["size"] if grinder else 0
        rpm = grinder["rpm"] if grinder else 0
        temperature = recipe.get("temperature", 91)
        pattern = recipe.get("pattern", "center")
        vibration = recipe.get("vibration", "none")
        flow_rate = recipe.get("flow_rate", 3.0)
        pours = recipe_dict_to_pours(recipe)
    else:
        if not args.pour:
            parser.error("Specify at least one --pour (or use --load)")

        cup_type = args.cup_type
        dose = args.dose
        grinder_size = args.grind if args.grind else 0
        rpm = args.rpm
        temperature = args.temp
        pattern = args.pattern
        vibration = args.vibration
        flow_rate = args.flow_rate

        vib_before = vibration in ("before", "both")
        vib_after  = vibration in ("after", "both")
        pours = []
        for vol, post_wait in args.pour:
            pours.append({
                "volume":            vol,
                "temperature":       temperature,
                "post_wait":         int(post_wait),
                "pattern":           pattern,
                "vibration_before":  vib_before,
                "vibration_after":   vib_after,
                "flow_rate":         flow_rate,
            })

    # Print recipe summary
    total_water = sum(p['volume'] for p in pours)
    print("\n--- Recipe -------------------------------------------------")
    print(f"  Cup type:    {cup_type}")
    print(f"  Dose:        {dose:.0f} g" if dose > 0 else "  Dose:        (not set)")
    print(f"  Grinder:     {'OFF' if grinder_size == 0 else grinder_size}")
    print(f"  Temperature: {temperature:.0f} C")
    print(f"  Total water: {total_water:.0f} ml")
    print(f"  Pattern:     {pattern}")
    print(f"  Vibration:   {vibration}")
    print(f"  Flow rate:   {flow_rate}")
    defaults = {"temperature": temperature, "pattern": pattern,
                "vibration": vibration, "flow_rate": flow_rate}
    for i, p in enumerate(pours):
        label = "Bloom" if i == 0 else f"Pour {i+1}"
        overrides = format_pour_overrides(p, defaults)
        suffix = f"  {overrides}" if overrides else ""
        print(f"  {label:<8} vol={p['volume']:.0f}ml  post_wait={p['post_wait']}s{suffix}")

    recipe_hex = encode_recipe(pours, grinder_size=grinder_size, dose=dose, rpm=rpm)
    print(f"\n  Recipe blob ({len(recipe_hex)//2} bytes): {recipe_hex.upper()}")

    # Save if requested
    if args.save:
        recipe_dict = recipe_to_dict(pours, dose, grinder_size, temperature, pattern, vibration, flow_rate, cup_type, rpm=rpm)
        errors = validate_recipe(recipe_dict)
        if errors:
            print(f"\nRecipe validation failed:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        save_recipe(args.save, recipe_dict)

    # Brew (unless dry-run)
    try:
        asyncio.run(brew(recipe_hex, grinder_size, dose, total_water, args.dry_run, cup_type))
    except (KeyboardInterrupt, SystemExit):
        print("\nStopped.")

if __name__ == "__main__":
    main()
