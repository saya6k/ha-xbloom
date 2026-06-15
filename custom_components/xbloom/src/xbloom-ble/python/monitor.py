#!/usr/bin/env python3
"""
xBloom BLE Live Monitor
Connects to the machine, sends the handshake, and decodes all incoming notifications.

Usage: python3 monitor.py
Press Ctrl+C to disconnect.
"""

import asyncio
import signal
import struct
import sys

from xbloom import (
    WRITE_UUID, NOTIFY_UUID, HANDSHAKE,
    CMD_NAMES, scan_and_connect,
)

# Ensure clean BLE disconnect on Ctrl+C or kill.
# Only install when running as main script — importing this module from
# the server must NOT hijack uvicorn's signal handling.
def _handle_signal(sig, frame):
    sys.exit(0)
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

# ── Notification decoder ──────────────────────────────────────────────────────
def parse_le_float(data: bytes, offset: int) -> float:
    """Read a 4-byte little-endian float from data at byte offset."""
    return struct.unpack_from('<f', data, offset)[0]

def parse_le_int(data: bytes, offset: int, size: int = 1) -> int:
    """Read a little-endian unsigned int from data."""
    val = 0
    for i in range(size):
        val |= data[offset + i] << (i * 8)
    return val

def hex_to_ascii(hex_str: str) -> str:
    """Decode hex string to printable ASCII, replacing non-printable bytes."""
    result = ""
    for i in range(0, len(hex_str), 2):
        b = int(hex_str[i:i+2], 16)
        result += chr(b) if 0x20 <= b < 0x7F else ''
    return result.strip('\x00')

def decode_machine_info(data: bytes) -> dict:
    """
    Decode RD_MachineInfo (40521) payload.
    Source: MachineInfoBleModel.java
    All field offsets are byte offsets into `data` (the payload after byte 10).
    """
    h = data.hex()  # hex string of payload bytes
    info = {}
    # Strings (hexToString = decode each byte as ASCII char)
    info['serialNumber'] = hex_to_ascii(h[0:26])    # 13 bytes
    info['theModel']     = hex_to_ascii(h[26:38])   # 6 bytes (often 0xFF = blank)
    info['theVersion']   = hex_to_ascii(h[38:58])   # 10 bytes
    # Float: stored LE, reverseHex then parse as big-endian float
    if len(h) >= 66:
        le4 = h[58:66]
        be4 = le4[6:8] + le4[4:6] + le4[2:4] + le4[0:2]
        info['areaAp'] = struct.unpack('>f', bytes.fromhex(be4))[0]
    # Single-byte ints
    def byte_at(char_offset):
        s = h[char_offset:char_offset+2]
        return int(s, 16) if s else None
    info['waterEnough']  = byte_at(66)   # 0=no water, 1=ok
    info['systemStatus'] = byte_at(68)
    info['userCount']    = byte_at(70)
    info['waterFeed']    = byte_at(72)   # 0=tank, 1=tap
    raw_grinder          = byte_at(74)
    info['grinder']      = max((raw_grinder or 30) - 30, 1)  # internal value - 30
    info['ledType']      = byte_at(76)
    info['voltage']      = byte_at(78)
    info['tempUnit']     = byte_at(80)   # 0=°C, 1=°F
    info['weightUnit']   = byte_at(82) if len(h) >= 84 else None
    # Mode type (if packet is long enough)
    if len(h) >= 110:
        mode_hex = h[102:110]
        info['modeType'] = 'EASY' if mode_hex == '91327856' else 'PRO'
    else:
        info['modeType'] = 'PRO'
    return info

def decode_notification(raw: bytes):
    """
    Parse a raw FFE2 notification and return a human-readable dict.
    Format: 58 02 07  [cmd 2B LE]  [len 4B LE]  [status 1B]  [data NB]  [crc 2B]
    """
    if len(raw) < 12:
        return {'raw': raw.hex().upper(), 'note': 'too short'}

    func_code  = raw[2]
    cmd_code   = struct.unpack_from('<H', raw, 3)[0]
    status     = raw[9]
    payload    = raw[10:-2]   # everything between status byte and CRC

    name = CMD_NAMES.get(cmd_code, f'Unknown({cmd_code})')
    result = {'cmd': cmd_code, 'name': name, 'status': f'{status:02X}', 'raw': raw.hex().upper()}

    # Decode known payloads
    if cmd_code in (20501, 10507) and len(payload) >= 4:  # weight
        result['weight_g'] = round(parse_le_float(payload, 0), 2)

    elif cmd_code == 40523 and len(payload) >= 4:  # water volume
        result['water_ml'] = round(parse_le_float(payload, 0), 1)

    elif cmd_code == 40521 and len(payload) >= 29:  # machine info
        result['info'] = decode_machine_info(payload)

    elif cmd_code == 8023 and len(payload) >= 4:  # machine activity
        result['activity'] = parse_le_int(payload, 0, 4)

    elif cmd_code == 11511 and len(payload) >= 4:  # mode ACK
        mode_hex = payload.hex()[:8]
        result['mode'] = 'EASY/Auto' if mode_hex == '91327856' else 'PRO'

    elif cmd_code == 8108 and len(payload) >= 4:  # brewer temperature
        result['temp'] = parse_le_float(payload, 0)

    return result

# ── Display ───────────────────────────────────────────────────────────────────
SUPPRESS_REPEAT = {20501, 40523}  # suppress repeated identical prints for these

last_seen = {}

def display(decoded: dict):
    cmd  = decoded['cmd']
    name = decoded['name']

    # Suppress repeated weight/water spam unless value changed
    if cmd in SUPPRESS_REPEAT:
        key = decoded.get('weight_g', decoded.get('water_ml'))
        if last_seen.get(cmd) == key:
            return
        last_seen[cmd] = key

    if cmd in (20501, 10507):
        print(f"  ⚖️  Weight:  {decoded['weight_g']:7.2f} g")
    elif cmd == 40523:
        print(f"  💧 Water:   {decoded['water_ml']:7.1f} mL")
    elif cmd == 40521:
        info = decoded['info']
        print(f"\n  ── Machine Info ──────────────────────────────")
        print(f"     Serial:    {info.get('serialNumber', '?')}")
        print(f"     Model:     {info.get('theModel', '?') or '(blank)'}")
        print(f"     Firmware:  {info.get('theVersion', '?')}")
        print(f"     Mode:      {info.get('modeType', '?')}")
        print(f"     Water:     {'OK' if info.get('waterEnough') else 'LOW'} | source={'tap' if info.get('waterFeed') else 'tank'}")
        print(f"     Grinder:   {info.get('grinder', '?')}")
        print(f"     LED:       {info.get('ledType', '?')}")
        print(f"     Temp unit: {'°F' if info.get('tempUnit') else '°C'}")
        print(f"     Weight u.: {'oz' if info.get('weightUnit') else 'g'}")
        print(f"     Area Ap:   {info.get('areaAp', '?'):.1f}")
        print(f"  ──────────────────────────────────────────────\n")
    elif cmd == 11511:
        print(f"  ✓  Mode ACK: {decoded.get('mode', '?')}")
    elif cmd == 8011:
        print(f"  ✓  Machine is awake")
    elif cmd == 8023:
        print(f"  ▸  Activity: {decoded.get('activity', '?')}")
    elif cmd == 8100:
        print(f"  ✓  Handshake ACK")
    else:
        print(f"  [{name}] status={decoded['status']}  raw={decoded['raw']}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def monitor():
    def on_notify(sender, data: bytearray):
        decoded = decode_notification(bytes(data))
        display(decoded)

    print("Connecting to xBloom...")
    try:
        client, info = await scan_and_connect(on_notify=on_notify)
    except ConnectionError as e:
        print(f"  {e}")
        print("Is the machine on? Disconnect phone app and try again.")
        return

    print(f"Connected! firmware={info.get('firmware')}  mode={info.get('mode')}")
    print("Ready. Press Ctrl+C to quit.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await client.stop_notify(NOTIFY_UUID)
        await client.disconnect()
        print("\nDisconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(monitor())
    except (KeyboardInterrupt, SystemExit):
        print("\nStopped.")
