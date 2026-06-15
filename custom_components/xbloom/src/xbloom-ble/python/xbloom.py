"""
xBloom BLE Protocol — Shared module

Packet builders, CRC, recipe encoder, and BLE constants used by
brew.py, send_command.py, and monitor.py.
"""

import asyncio
import json
import os
import struct
import time
from contextlib import asynccontextmanager

# -- Recipe validation ---------------------------------------------------------
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "recipe-schema.json")

def _check_temperature(val, prefix):
    if not isinstance(val, (int, float)) or val < 40 or val > 98:
        return [f"{prefix}: temperature must be 40-98, got {val!r}"]
    return []

def _check_pattern(val, prefix):
    if val not in ("center", "circular", "spiral"):
        return [f"{prefix}: pattern must be center/circular/spiral, got {val!r}"]
    return []

def _check_vibration(val, prefix):
    if val not in ("none", "before", "after", "both"):
        return [f"{prefix}: vibration must be none/before/after/both, got {val!r}"]
    return []

def _check_flow_rate(val, prefix):
    if not isinstance(val, (int, float)) or val < 3.0 or val > 3.5:
        return [f"{prefix}: flow_rate must be 3.0-3.5, got {val!r}"]
    return []

def validate_recipe(recipe: dict) -> list[str]:
    """Validate a recipe dict against the schema. Returns a list of error strings (empty = valid)."""
    errors = []

    if not isinstance(recipe, dict):
        return ["recipe must be a JSON object"]

    _KNOWN_KEYS = {"cup_type", "dose", "grinder", "rpm", "temperature", "pattern", "vibration", "flow_rate", "pours"}
    for k in recipe:
        if k not in _KNOWN_KEYS:
            errors.append(f"unknown top-level key: {k!r}")

    if "cup_type" in recipe:
        ct = recipe["cup_type"]
        if ct not in ("xpod", "xdripper", "other", "tea"):
            errors.append(f"cup_type must be one of xpod/xdripper/other/tea, got {ct!r}")

    if "dose" in recipe:
        d = recipe["dose"]
        if not isinstance(d, (int, float)) or d < 0:
            errors.append(f"dose must be a number >= 0, got {d!r}")

    if "grinder" in recipe:
        g = recipe["grinder"]
        if g is not None:
            if not isinstance(g, dict):
                errors.append(f"grinder must be null or {{size, rpm}}, got {g!r}")
            else:
                s = g.get("size")
                if not isinstance(s, int) or s < 1 or s > 80:
                    errors.append(f"grinder.size must be 1-80, got {s!r}")
                r = g.get("rpm")
                if not isinstance(r, int) or r < 60 or r > 120:
                    errors.append(f"grinder.rpm must be 60-120, got {r!r}")

    if "temperature" in recipe:
        errors.extend(_check_temperature(recipe["temperature"], "temperature"))
    if "pattern" in recipe:
        errors.extend(_check_pattern(recipe["pattern"], "pattern"))
    if "vibration" in recipe:
        errors.extend(_check_vibration(recipe["vibration"], "vibration"))
    if "flow_rate" in recipe:
        errors.extend(_check_flow_rate(recipe["flow_rate"], "flow_rate"))

    if "pours" not in recipe:
        errors.append("missing required key: pours")
        return errors

    pours = recipe["pours"]
    if not isinstance(pours, list) or len(pours) < 1:
        errors.append("pours must be a non-empty array")
        return errors

    _POUR_KEYS = {"volume", "post_wait", "temperature", "vibration", "pattern", "flow_rate"}
    for i, p in enumerate(pours):
        prefix = f"pours[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{prefix} must be an object")
            continue

        for k in p:
            if k not in _POUR_KEYS:
                errors.append(f"{prefix}: unknown key {k!r}")

        if "volume" not in p:
            errors.append(f"{prefix}: missing required key 'volume'")
        elif not isinstance(p["volume"], (int, float)) or p["volume"] <= 0:
            errors.append(f"{prefix}: volume must be > 0, got {p['volume']!r}")

        if "post_wait" not in p:
            errors.append(f"{prefix}: missing required key 'post_wait'")
        elif not isinstance(p["post_wait"], (int, float)) or int(p["post_wait"]) < 0 or int(p["post_wait"]) > 59:
            errors.append(f"{prefix}: post_wait must be 0-59, got {p['post_wait']!r}")

        if "temperature" in p:
            errors.extend(_check_temperature(p["temperature"], prefix))
        if "vibration" in p:
            errors.extend(_check_vibration(p["vibration"], prefix))
        if "pattern" in p:
            errors.extend(_check_pattern(p["pattern"], prefix))
        if "flow_rate" in p:
            errors.extend(_check_flow_rate(p["flow_rate"], prefix))

    return errors


# -- BLE UUIDs -----------------------------------------------------------------
WRITE_UUID  = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

# -- Command codes -------------------------------------------------------------
# Symbolic names for the BLE command codes used by brew.py / send_command.py.
# CMD_NAMES below maps *every* known code (including notifications the machine
# emits); this block is for commands we *send*.
CMD_HANDSHAKE      = 8100
CMD_MODE_TYPE      = 11511
CMD_BREW_PAUSE     = 40518
CMD_BREW_STOP      = 40519
CMD_BACK_TO_HOME   = 8022
CMD_GRINDER_ENTER  = 8006
CMD_GRINDER_START  = 3500
CMD_GRINDER_STOP   = 3505
CMD_TARE           = 8500
CMD_UNIT_WEIGHT    = 8005
CMD_UNIT_TEMP      = 8010
CMD_WATER_SOURCE   = 4508

MODE_PRO  = "00000000"
MODE_EASY = "91327856"

# -- CRC16 ---------------------------------------------------------------------
def crc16(data: bytes) -> int:
    """CRC16 matching com.leonxtp.library.CRC16.calcCRC().
    Polynomial 0x8408 (reversed CCITT 0x1021), initial value 0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF

# -- Packet builders -----------------------------------------------------------
def _le_hex(value: int, n: int) -> str:
    return ''.join(f'{(value >> (i*8)) & 0xFF:02x}' for i in range(n))

def build_packet_type1(cmd: int, data_ints: list = None) -> bytes:
    """buildCommandString(cmd, int... data) -- funcCode 0x01"""
    data_ints = data_ints or []
    hex_str  = "580101"
    hex_str += _le_hex(cmd, 2)
    hex_str += _le_hex(len(data_ints) * 4 + 12, 4)
    hex_str += "01"
    for v in data_ints:
        hex_str += _le_hex(v, 4)
    payload = bytes.fromhex(hex_str)
    return payload + struct.pack('<H', crc16(payload))

def build_packet_type1h(cmd: int, hex_data: str) -> bytes:
    """buildCommandString(cmd, hexString) -- funcCode 0x01, hex string payload.
    Used for cmd 8001 / 8004 (send recipe blob on-the-fly)."""
    data_bytes   = len(hex_data) // 2
    total_length = data_bytes + 12
    hex_str  = "580101"
    hex_str += _le_hex(cmd, 2)
    hex_str += _le_hex(total_length, 4)
    hex_str += "01"
    hex_str += hex_data
    payload = bytes.fromhex(hex_str)
    return payload + struct.pack('<H', crc16(payload))

def build_packet_type2(cmd: int, hex_data: str) -> bytes:
    """buildCommandString2(cmd, hexData) -- funcCode 0x02.
    Used for mode switch, Easy Mode slot commands (11510, 11511, 11512)."""
    data_bytes   = len(hex_data) // 2
    total_length = data_bytes + 12
    hex_str  = "580102"
    hex_str += _le_hex(cmd, 2)
    hex_str += _le_hex(total_length, 4)
    hex_str += "01"
    hex_str += hex_data
    payload = bytes.fromhex(hex_str)
    return payload + struct.pack('<H', crc16(payload))

# -- Float helpers -------------------------------------------------------------
def float_to_int_bits(f: float) -> int:
    """Java's Float.floatToIntBits() — pack float as its raw IEEE 754 int."""
    return struct.unpack('<I', struct.pack('<f', f))[0]

# -- Brew weight tare compensation ---------------------------------------------
class BrewWeightTracker:
    """Compensates for firmware-triggered scale tares during bloom.

    The xBloom firmware resets the scale to 0g 2-3 times during the first
    ~4 seconds of the bloom pour (no-grinder path, cmd 8004). After that,
    no more tares occur for the rest of the brew.

    Strategy: during a short window after Coffee Starting, any weight drop
    >1g is treated as a firmware tare and compensated. After the window
    closes, raw weight is passed through unchanged.

    Usage:
        tracker = BrewWeightTracker()
        tracker.reset()           # on Coffee Starting (40502)
        corrected = tracker.update(raw_weight)  # on each weight (20501)
    """
    TARE_WINDOW_S = 10.0  # seconds — tares observed within first ~4s; 10s for margin
    DROP_THRESHOLD = 1.0  # g — minimum drop to count as a tare within the window

    def __init__(self, logger=None):
        self._offset = 0.0
        self._last_raw = 0.0
        self._last_corrected = 0.0
        self._active = False
        self._start_time = 0.0
        self._log = logger  # callable(msg) for server-side logging

    def reset(self):
        """Call on Coffee Starting (40502) — opens the tare detection window."""
        self._offset = 0.0
        self._last_raw = 0.0
        self._last_corrected = 0.0
        self._active = True
        self._start_time = time.time()

    def stop(self):
        """Call when brew finishes — stops logging stale weight-back warnings."""
        self._active = False

    def update(self, raw_weight: float, debug: bool = False) -> float:
        """Feed a raw weight reading, get back the corrected cumulative weight."""
        in_window = self._active and (time.time() - self._start_time) < self.TARE_WINDOW_S
        if in_window and raw_weight < self._last_raw - self.DROP_THRESHOLD:
            self._offset += self._last_raw
            if debug:
                print(f"  ** TARE: raw={raw_weight:.1f}, prev={self._last_raw:.1f}, offset now={self._offset:.1f}")
            if self._log:
                self._log(f"TARE: raw={raw_weight:.2f}, prev={self._last_raw:.2f}, offset={self._offset:.2f}")
        prev_raw = self._last_raw
        self._last_raw = raw_weight
        corrected = self._offset + raw_weight
        if self._log and self._active and corrected < self._last_corrected - 0.5:
            self._log(f"WEIGHT BACK: {self._last_corrected:.2f} -> {corrected:.2f} "
                      f"(raw={raw_weight:.2f}, prev_raw={prev_raw:.2f}, offset={self._offset:.2f})")
        corrected = max(corrected, self._last_corrected)
        self._last_corrected = corrected
        return corrected


# -- Recipe encoder ------------------------------------------------------------
_PATTERN_CODE = {'center': 0, 'circular': 1, 'spiral': 2}

def _vibration_code(before: bool, after: bool) -> int:
    if before and after: return 3
    if before:           return 1
    if after:            return 2
    return 0

def encode_recipe(pours: list, grinder_size: int = 0, dose: float = 0,
                   rpm: int = 0) -> str:
    """
    Encode a recipe into the xBloom BLE hex string.

    Source: GetRecipeCodeManager.sendData2Hex()
    Returns the hex string payload for build_packet_type1h(8004, ...) or (8001, ...).

    pours: list of dicts with keys:
        volume        float  ml per pour
        temperature   int    C (40-98; official app sends 98 for "BP")
        post_wait     int    seconds to pause AFTER this pour ends
        pattern       str    'center' | 'spiral' | 'circular'  (default 'center')
        flow_rate     float  water flow speed (default 3.0; encoded x10)
        vibration_before  bool  (default False)
        vibration_after   bool  (default False)

    grinder_size: int   0 = grinder OFF; 1-100 = grinder enabled at this size
    dose: float         coffee dose in grams
    rpm: int            grinder RPM speed (0 when grinder is off)

    Tail bytes: [grinder_size, ratio × 10]
    The ratio (coffee:water) is computed as total_water / dose.
    Source: GetRecipeCodeService.executeClient() passes
    [grinderSize, grandWater × mulNumber] where grandWater is the ratio
    (confirmed: Recipe model validates dose × grandWater == totalPourVolume).
    HCI capture: 0xA0 (160) for a 16g dose / 1:16 / 256ml recipe → 16 × 10 = 160.

    RPM byte: stored in byte[2] of the first pour's timing block. The decompiled
    app passes recipe.getRpm() here. Confirmed identical (90) across all three
    HCI captures. Subsequent pours get 0.
    Timing block layout: [post_wait_neg, 0x00, rpm, flow_rate].
    """
    total_water = sum(p['volume'] for p in pours)
    ratio = (total_water / dose) if dose > 0 else 0

    parts = []
    for i, pour in enumerate(pours):
        volume    = int(pour['volume'])
        temp      = int(pour['temperature'])
        pattern   = _PATTERN_CODE.get(pour.get('pattern', 'center'), 0)
        vib       = _vibration_code(pour.get('vibration_before', False),
                                    pour.get('vibration_after', False))
        flow_enc  = int(pour.get('flow_rate', 3.0) * 10) & 0xFF
        post_wait = int(pour.get('post_wait', 0))

        # SubStep: encode volume in <=127 ml chunks
        substep = []
        if volume > 127:
            for _ in range(volume // 127):
                substep.extend([127, temp, pattern, vib])
            remainder = volume % 127
            if remainder:
                substep.extend([remainder, temp, pattern, vib])
        else:
            substep.extend([volume, temp, pattern, vib])
        substep_hex = bytes(substep).hex()

        # Timing block: [post_wait (negated), 0x00, rpm, flow_rate]
        # RPM goes in byte[2] of the first pour only (from recipe.getRpm())
        post_wait_neg = (-post_wait) & 0xFF
        rpm_byte = (rpm & 0xFF) if i == 0 else 0
        timing_hex = bytes([post_wait_neg, 0, rpm_byte, flow_enc]).hex()

        parts.append(substep_hex)
        parts.append(timing_hex)

    data_hex    = ''.join(parts)
    length_byte = len(data_hex) // 2
    # Tail bytes: [grinder_size, ratio × 10]
    # The grinder byte is metadata — the recipe command (8001 vs 8004) controls
    # whether the machine actually grinds, not this byte.
    grinder_byte = int(grinder_size) & 0xFF
    ratio_byte   = int(ratio * 10) & 0xFF

    return f"{length_byte:02x}{data_hex}{grinder_byte:02x}{ratio_byte:02x}"

# -- Brew helper packets -------------------------------------------------------
def build_bypass_packet(dose: float) -> bytes:
    """Command 8102 — Bypass water + dose info.
    App sends this even with bypass disabled to communicate the dose weight."""
    return build_packet_type1(8102, [0, 0, int(dose)])

# -- Cup type ------------------------------------------------------------------
# The xBloom app defines cupType as an integer enum:
#   xPod=1, xDripper=2, Other=3, Tea=4
#
# When starting a brew, the app sends the pour data + cupType to xBloom's
# cloud API (getRecipeCodeJ15 → GetRecipeCodeTransfer), which returns:
#   theCode  — the encoded recipe hex
#   theMax   — cup max weight (float)
#   theMin   — cup min weight (float)
#
# These (theMax, theMin) values are then sent to the machine via BLE command
# 8104 (setCup). We don't have access to the cloud API, so the actual
# per-cup-type weight ranges are unknown. In practice the machine brews
# correctly regardless of these values — they likely configure the scale's
# expected range or display limits. We use 200.0/80.0 (the defaults observed
# in HCI captures) for all cup types until we can sniff the real mappings.
CUP_TYPES = ("xpod", "xdripper", "other", "tea")
# Cup weight ranges (theMax, theMin) sent to the machine via command 8104.
# The official app gets these from its cloud API (getRecipeCodeJ15), which
# we don't have access to. Values below are captured from HCI snoop logs.
CUP_TYPE_RANGES = {
    "xpod":     (200.0, 80.0),   # no HCI capture available; using default
    "xdripper": (110.0, 90.0),   # Omni dripper — HCI confirmed 2026-04-04
    "other":    (200.0, 80.0),   # Free Solo — HCI confirmed 2026-04-04
    "tea":      (200.0, 80.0),   # no HCI capture available; using default
}

def build_set_cup_packet(cup_type: str = "other") -> bytes:
    """Command 8104 — Set cup weight range (theMax, theMin as float bits).
    See CUP_TYPE_RANGES for the per-cup-type values."""
    cup_max, cup_min = CUP_TYPE_RANGES.get(cup_type, (200.0, 80.0))
    return build_packet_type1(8104, [float_to_int_bits(cup_max),
                                     float_to_int_bits(cup_min)])

HANDSHAKE = build_packet_type1(8100, [185, 1])

# -- Easy Mode slot helpers ----------------------------------------------------
# Flags byte: bit 4 (0x10) = scale ON, lower nibble = grinder (0x02=ON, 0x04=OFF)
SLOT_GRINDER_OFF = 0x04
SLOT_GRINDER_ON  = 0x02

def slot_flags(scale_on: bool, grinder_on: bool) -> int:
    """Build the flags byte for an Easy Mode slot."""
    flags = SLOT_GRINDER_ON if grinder_on else SLOT_GRINDER_OFF
    if scale_on:
        flags |= 0x10
    return flags

def build_slot_packet(slot_index: int, flags: int, recipe_hex: str) -> bytes:
    """Command 11510 — Easy Recipe Send. Type 2 packet.
    slot_index: 0=A, 1=B, 2=C
    flags: slot_flags() result
    recipe_hex: encode_recipe() result"""
    hex_data = f"{slot_index:02x}{flags:02x}{recipe_hex}"
    return build_packet_type2(11510, hex_data)

# -- Notification codes --------------------------------------------------------
CMD_NAMES = {
    8100:  "Handshake ACK",
    20501: "Scale Weight",
    10507: "Scale Weight (alt)",
    40523: "Water Volume",
    40521: "Machine Info",
    8011:  "Machine Awake",
    8009:  "Machine Sleeping",
    8010:  "Temp Unit",
    8023:  "Machine Activity",
    11511: "Mode Switch ACK",
    8022:  "Back to Home",
    8107:  "Brewer Mode",
    8108:  "Brewer Temp",
    8105:  "Grinder Size",
    8106:  "Grinder Speed",
    8103:  "LED Type",
    8015:  "Unit Change",
    9000:  "In Grinder",
    9001:  "In Brewer",
    9002:  "In Scale",
    9003:  "Grinder Begin",
    9004:  "Grinder Out",
    9005:  "Brewer Begin",
    9006:  "Brewer Out",
    9008:  "Scale Out",
    9009:  "Grinder Paused",
    9010:  "Brewer Paused",
    8001:  "Recipe Send ACK (grinder)",
    8002:  "Execute Recipe ACK",
    8004:  "Recipe Send ACK",
    40502: "Coffee Starting",
    40510: "Bloom",
    40511: "Brewer Stop",
    40507: "Grinder Stop",
    40512: "Enjoy!",
    40513: "Enjoy (2)",
    40515: "Pour Volume ACK",
    40516: "Pour Transition",
    40517: "Error: Idling",
    40522: "Error: No Water",
    8203:  "Error: Gear Pos",
    8204:  "Error: Dose/Water",
    11510: "Easy Recipe Send ACK",
    11512: "Recipe Order ACK",
}

def decode_notification(raw: bytes):
    """Decode an xBloom BLE notification packet header.

    Returns (cmd_code: int, name: str | None, payload: bytes) where:
      - cmd_code is the little-endian uint16 at offset 3
      - name is CMD_NAMES[cmd_code] or None if unknown
      - payload is the bytes between the 10-byte header and the 2-byte CRC

    Returns (0, None, b"") if the packet is too short to parse.
    """
    if len(raw) < 5:
        return 0, None, b""
    cmd = struct.unpack_from('<H', raw, 3)[0]
    payload = raw[10:-2] if len(raw) >= 12 else b""
    return cmd, CMD_NAMES.get(cmd), payload

# -- Recipe dict helpers (shared by CLI and server) ----------------------------
def recipe_dict_to_pours(recipe):
    """Convert a loaded recipe dict into the pours list format encode_recipe expects."""
    temperature = recipe.get("temperature", 91)
    pattern = recipe.get("pattern", "center")
    vibration = recipe.get("vibration", "none")
    flow_rate = recipe.get("flow_rate", 3.0)
    pours = []
    for p in recipe["pours"]:
        p_vib = p.get("vibration", vibration)
        pours.append({
            "volume":           p["volume"],
            "temperature":      p.get("temperature", temperature),
            "post_wait":        int(p["post_wait"]),
            "pattern":          p.get("pattern", pattern),
            "vibration_before": p_vib in ("before", "both"),
            "vibration_after":  p_vib in ("after", "both"),
            "flow_rate":        p.get("flow_rate", flow_rate),
        })
    return pours


def format_pour_overrides(pour: dict, defaults: dict) -> str:
    """Return a compact string listing this pour's overrides vs the recipe defaults.

    e.g. "temp=95C  pattern=center  vibration=before"; empty string when the pour
    matches every default. Used by CLI summaries to surface per-pour tweaks.

    `pour` must contain: temperature, pattern, vibration_before, vibration_after,
    flow_rate (the shape produced by recipe_dict_to_pours).
    `defaults` must contain: temperature, pattern, vibration (string: none/before/
    after/both), flow_rate.
    """
    notes = []
    if pour["temperature"] != defaults["temperature"]:
        notes.append(f"temp={pour['temperature']:.0f}C")
    if pour["pattern"] != defaults["pattern"]:
        notes.append(f"pattern={pour['pattern']}")
    p_vib = ("both"   if pour["vibration_before"] and pour["vibration_after"]
             else "before" if pour["vibration_before"]
             else "after"  if pour["vibration_after"]
             else "none")
    if p_vib != defaults["vibration"]:
        notes.append(f"vibration={p_vib}")
    if pour["flow_rate"] != defaults["flow_rate"]:
        notes.append(f"flow={pour['flow_rate']}")
    return "  ".join(notes)


def recipe_to_dict(pours, dose, grinder_size, temperature, pattern, vibration, flow_rate,
                   cup_type="other", rpm=0):
    """Build a recipe dict suitable for JSON serialization."""
    d = {
        "cup_type": cup_type,
        "dose": dose,
        "grinder": {"size": grinder_size, "rpm": rpm} if grinder_size > 0 else None,
        "temperature": temperature,
        "pattern": pattern,
        "vibration": vibration,
        "pours": [
            {
                "volume": p["volume"],
                "post_wait": p["post_wait"],
            }
            for p in pours
        ],
    }
    if flow_rate != 3.0:
        d["flow_rate"] = flow_rate
    # Include per-pour overrides only when they differ from the global defaults
    vib_before = vibration in ("before", "both")
    vib_after  = vibration in ("after", "both")
    for i, p in enumerate(pours):
        if p["temperature"] != temperature:
            d["pours"][i]["temperature"] = p["temperature"]
        p_vib_before = p.get("vibration_before", False)
        p_vib_after  = p.get("vibration_after", False)
        if p_vib_before != vib_before or p_vib_after != vib_after:
            if p_vib_before and p_vib_after:
                d["pours"][i]["vibration"] = "both"
            elif p_vib_before:
                d["pours"][i]["vibration"] = "before"
            elif p_vib_after:
                d["pours"][i]["vibration"] = "after"
            else:
                d["pours"][i]["vibration"] = "none"
        if p.get("pattern", pattern) != pattern:
            d["pours"][i]["pattern"] = p["pattern"]
        if p.get("flow_rate", flow_rate) != flow_rate:
            d["pours"][i]["flow_rate"] = p["flow_rate"]
    return d


# -- BLE connection ------------------------------------------------------------
# Optional: set XBLOOM_BLE_ADDRESS to a known peripheral address (e.g. a macOS
# CoreBluetooth UUID, or a MAC on Linux/Windows) to bypass scanning when
# discover() misses the device. When unset, scan_and_connect() relies on
# BleakScanner alone.
KNOWN_ADDRESS = os.environ.get("XBLOOM_BLE_ADDRESS")


_NOT_FOUND_HINT = (
    "xBloom not found. Make sure the machine is on and the official xBloom app "
    "is disconnected (not just closed; BLE allows only one central at a time). "
    "Set XBLOOM_BLE_ADDRESS to bypass scanning if discover() keeps missing it."
)
_HANDSHAKE_HINT = (
    "xBloom found but not responding to the handshake. Usually caused by the "
    "phone app still holding the BLE link; disconnect it in the app, then retry."
)


async def scan_and_connect(on_notify=None, scan_timeout=10.0, info_timeout=5.0,
                           disconnected_callback=None, retries=1):
    """Scan for xBloom, connect, handshake, and wait for machine info.

    Returns (client, machine_info_dict) on success.
    Raises ConnectionError if the machine is not found or handshake fails.

    on_notify:    optional notification callback (sender, data: bytearray).
                  If provided, it receives ALL notifications including the ones
                  during the handshake phase.
    scan_timeout: seconds to spend scanning for the machine.
    info_timeout: seconds to wait for the 40521 Machine Info notification after
                  handshake. If it doesn't arrive, the connection is torn down.
    retries:      extra full scan+connect+handshake attempts if the first fails.
                  Default 1 (= up to 2 total attempts). Cleans up transient BLE
                  cache / advertising glitches that occasionally bite first scans.
    disconnected_callback: optional BleakClient disconnected_callback.
    """
    last_error = None
    for attempt in range(retries + 1):
        try:
            return await _connect_once(on_notify, scan_timeout, info_timeout,
                                       disconnected_callback)
        except ConnectionError as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(2.0)
    raise last_error


async def _connect_once(on_notify, scan_timeout, info_timeout,
                        disconnected_callback):
    """One scan+connect+handshake attempt. See scan_and_connect() for params."""
    from bleak import BleakClient, BleakScanner

    devices = await BleakScanner.discover(timeout=scan_timeout)
    device = next((d for d in devices if "xbloom" in (d.name or "").lower()), None)

    if not device:
        # Optional fallback: if the caller set XBLOOM_BLE_ADDRESS, try that
        # address directly. Useful on macOS when CoreBluetooth has cached the
        # peripheral and discover() misses it on the next scan.
        if KNOWN_ADDRESS:
            try:
                client = BleakClient(KNOWN_ADDRESS, timeout=10.0,
                                     disconnected_callback=disconnected_callback)
                await client.connect()
                if client.is_connected:
                    device = type("D", (), {"address": KNOWN_ADDRESS, "name": "xBLOOM"})()
                else:
                    raise ConnectionError(_NOT_FOUND_HINT)
            except Exception:
                raise ConnectionError(_NOT_FOUND_HINT)
        else:
            raise ConnectionError(_NOT_FOUND_HINT)
    else:
        client = BleakClient(device.address,
                             disconnected_callback=disconnected_callback)
        await client.connect()

    if not client.is_connected:
        raise ConnectionError(f"Failed to connect to {device.name}")

    # Wait for machine info (40521) after handshake
    info_received = asyncio.Event()
    machine_info = {}

    def _handshake_notify(sender, data: bytearray):
        raw = bytes(data)
        if len(raw) >= 5:
            cmd = struct.unpack_from('<H', raw, 3)[0]
            if cmd == 40521 and len(raw) >= 39:
                # Decode inline to avoid circular import with monitor.py
                payload = raw[10:-2]
                h = payload.hex()
                def _hex_ascii(s):
                    return "".join(chr(int(s[i:i+2], 16))
                                  for i in range(0, len(s), 2)
                                  if 0x20 <= int(s[i:i+2], 16) < 0x7F).strip("\x00")
                def _byte_at(off):
                    return int(h[off:off+2], 16) if len(h) > off+1 else None

                machine_info["serial"] = _hex_ascii(h[0:26])
                machine_info["firmware"] = _hex_ascii(h[38:58])
                machine_info["water_ok"] = bool(_byte_at(66))
                machine_info["water_source"] = "tap" if _byte_at(72) else "tank"
                raw_grinder = _byte_at(74)
                machine_info["grinder_size"] = max((raw_grinder or 30) - 30, 1)
                machine_info["temp_unit"] = "F" if _byte_at(80) else "C"
                if len(h) >= 110:
                    machine_info["mode"] = "EASY" if h[102:110] == "91327856" else "PRO"
                else:
                    machine_info["mode"] = "PRO"
                info_received.set()
        # Forward to caller's handler
        if on_notify:
            on_notify(sender, data)

    await client.start_notify(NOTIFY_UUID, _handshake_notify)

    # The xBloom silently consumes the first handshake after a GATT
    # rediscovery (which BlueZ does on every reconnect). Retry once
    # before giving up.
    for attempt in range(2):
        await client.write_gatt_char(WRITE_UUID, HANDSHAKE, response=False)
        try:
            await asyncio.wait_for(info_received.wait(), timeout=info_timeout)
            break
        except asyncio.TimeoutError:
            if attempt == 1:
                try:
                    await client.stop_notify(NOTIFY_UUID)
                    await client.disconnect()
                except Exception:
                    pass
                raise ConnectionError(_HANDSHAKE_HINT)

    # Swap to the caller's notification handler (if different)
    if on_notify:
        await client.stop_notify(NOTIFY_UUID)
        await client.start_notify(NOTIFY_UUID, on_notify)

    return client, machine_info


# -- Async BLE operations for callers managing their own connection -----------
async def send_brew_packets(client, recipe_dict: dict) -> float:
    """Send the BLE packet sequence to start a brew. Returns total water (ml).

    This only sends commands — it does NOT install a notification handler.
    Use this when the caller already has its own notification pipeline
    (e.g. a long-running server process with its own event dispatch).
    """
    pours = recipe_dict_to_pours(recipe_dict)
    grinder = recipe_dict.get("grinder")  # None or {"size": N, "rpm": N}
    grinder_size = grinder["size"] if grinder else 0
    rpm = grinder["rpm"] if grinder else 0
    dose = recipe_dict.get("dose", 0)
    cup_type = recipe_dict.get("cup_type", "other")
    total_water = sum(p["volume"] for p in pours)

    recipe_hex = encode_recipe(pours, grinder_size=grinder_size, dose=dose, rpm=rpm)
    cmd_recipe = 8001 if grinder_size > 0 else 8004

    packets = [
        build_packet_type1(8100, [185, 1]),     # handshake
        build_packet_type1(8022, []),            # back to home (reset machine UI state)
        build_bypass_packet(dose),               # bypass + dose
        build_set_cup_packet(cup_type),          # cup range
        build_packet_type1h(cmd_recipe, recipe_hex),  # recipe
        build_packet_type1(8002, []),            # brew start
    ]
    for pkt in packets:
        await client.write_gatt_char(WRITE_UUID, pkt, response=False)
        await asyncio.sleep(2.0)
    return total_water


async def run_brew(client, recipe_dict: dict, on_event=None):
    """Execute a brew from a recipe dict over an existing BLE connection.

    client:      connected BleakClient
    recipe_dict: validated recipe dict (same format as JSON files)
    on_event:    async callback(event_dict) called for each BLE notification

    Returns on completion (40511/40512/40513) or raises asyncio.TimeoutError.
    """
    pours = recipe_dict_to_pours(recipe_dict)
    grinder = recipe_dict.get("grinder")  # None or {"size": N, "rpm": N}
    grinder_size = grinder["size"] if grinder else 0
    rpm = grinder["rpm"] if grinder else 0
    dose = recipe_dict.get("dose", 0)
    cup_type = recipe_dict.get("cup_type", "other")
    total_water = sum(p["volume"] for p in pours)

    recipe_hex = encode_recipe(pours, grinder_size=grinder_size, dose=dose, rpm=rpm)
    cmd_recipe = 8001 if grinder_size > 0 else 8004

    packets = [
        build_packet_type1(8100, [185, 1]),     # handshake
        build_bypass_packet(dose),               # bypass + dose
        build_set_cup_packet(cup_type),          # cup range
        build_packet_type1h(cmd_recipe, recipe_hex),  # recipe
        build_packet_type1(8002, []),            # brew start
    ]

    # Send packet sequence
    for pkt in packets:
        await client.write_gatt_char(WRITE_UUID, pkt, response=False)
        await asyncio.sleep(2.0)

    # Wait for completion via notifications
    brew_done = asyncio.Event()
    brew_state = {"pour_count": 0, "start_time": time.time(), "last_weight": None}

    def _on_notify(sender, data: bytearray):
        raw = bytes(data)
        if len(raw) < 5:
            return
        cmd = struct.unpack_from('<H', raw, 3)[0]

        if cmd == 20501 and len(raw) >= 14:
            brew_state["last_weight"] = round(struct.unpack_from('<f', raw, 10)[0], 2)
            if on_event:
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.ensure_future(on_event({
                        "event": "weight", "weight_g": brew_state["last_weight"]
                    })) if asyncio.iscoroutinefunction(on_event) else on_event({
                        "event": "weight", "weight_g": brew_state["last_weight"]
                    })
                )
        elif cmd == 40502:
            brew_state["start_time"] = time.time()
            if on_event:
                _fire_event(on_event, {"event": "brew_start"})
        elif cmd == 40510:
            brew_state["pour_count"] += 1
            label = "Bloom" if brew_state["pour_count"] == 1 else f"Pour {brew_state['pour_count']}"
            elapsed = int(time.time() - brew_state["start_time"])
            if on_event:
                _fire_event(on_event, {
                    "event": "pour_start", "pour": brew_state["pour_count"],
                    "label": label, "elapsed_s": elapsed,
                })
        elif cmd == 40515 and len(raw) >= 14:
            w = round(struct.unpack_from('<f', raw, 10)[0], 1)
            if on_event:
                _fire_event(on_event, {
                    "event": "pour_volume", "pour": brew_state["pour_count"], "weight_g": w,
                })
        elif cmd == 40516:
            if on_event:
                _fire_event(on_event, {"event": "pour_transition", "pour": brew_state["pour_count"]})
        elif cmd == 40523 and len(raw) >= 14:
            ml = round(struct.unpack_from('<f', raw, 10)[0], 1)
            if on_event:
                _fire_event(on_event, {"event": "water_volume", "water_ml": ml})
        elif cmd in (40517, 40522, 8203, 8204):
            if on_event:
                _fire_event(on_event, {
                    "event": "error", "code": cmd,
                    "message": CMD_NAMES.get(cmd, f"Unknown error {cmd}"),
                })
        elif cmd in (40511, 40512, 40513):
            elapsed = int(time.time() - brew_state["start_time"])
            if on_event:
                _fire_event(on_event, {
                    "event": "complete", "elapsed_s": elapsed,
                    "final_weight_g": brew_state["last_weight"],
                    "total_water": total_water,
                })
            brew_done.set()

    await client.start_notify(NOTIFY_UUID, _on_notify)
    try:
        await asyncio.wait_for(brew_done.wait(), timeout=600)
    finally:
        await client.stop_notify(NOTIFY_UUID)

    return {
        "elapsed_s": int(time.time() - brew_state["start_time"]),
        "final_weight_g": brew_state["last_weight"],
        "pours_completed": brew_state["pour_count"],
        "total_water": total_water,
    }


def _fire_event(on_event, event_dict):
    """Fire an on_event callback from a sync BLE notification context."""
    try:
        loop = asyncio.get_event_loop()
        if asyncio.iscoroutinefunction(on_event):
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(on_event(event_dict)))
        else:
            loop.call_soon_threadsafe(on_event, event_dict)
    except RuntimeError:
        pass


async def run_command(client, packet: bytes):
    """Send a single command over an existing BLE connection (handshake + command)."""
    await client.write_gatt_char(WRITE_UUID, HANDSHAKE, response=False)
    await asyncio.sleep(1.0)
    await client.write_gatt_char(WRITE_UUID, packet, response=False)
    await asyncio.sleep(0.5)


async def run_grind(client, seconds: float = 4.0, grind_size: int = 63, speed: int = 100):
    """Run the grinder over an existing BLE connection."""
    enter_pkt = build_packet_type1(8006, [grind_size, speed])
    start_pkt = build_packet_type1(3500, [1000, grind_size, speed])
    stop_pkt  = build_packet_type1(3505)

    await client.write_gatt_char(WRITE_UUID, HANDSHAKE, response=False)
    await asyncio.sleep(0.5)
    await client.write_gatt_char(WRITE_UUID, enter_pkt, response=False)
    await asyncio.sleep(0.5)
    await client.write_gatt_char(WRITE_UUID, start_pkt, response=False)
    await asyncio.sleep(seconds)
    await client.write_gatt_char(WRITE_UUID, stop_pkt, response=False)
    await asyncio.sleep(1.0)


@asynccontextmanager
async def xbloom_session(on_notify=None, **kwargs):
    """Scan + connect + auto-disconnect context manager.

    Wraps scan_and_connect(). Yields (client, machine_info). Stops notifications
    and disconnects on exit, even on exception.

        async with xbloom_session(on_notify=handler) as (client, info):
            await client.write_gatt_char(WRITE_UUID, pkt, response=False)
    """
    client, info = await scan_and_connect(on_notify=on_notify, **kwargs)
    try:
        yield client, info
    finally:
        try:
            await client.stop_notify(NOTIFY_UUID)
        except Exception:
            pass
        await client.disconnect()
