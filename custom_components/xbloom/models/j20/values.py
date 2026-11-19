"""Exact values and encodings for the Original (J20) cloud protocol.

Everything here is read from app 2.2.5's decompiled code, not guessed:

* Manual pour (``BrewerJ20Activity.startWater``) —
  ``"FFFF1005" + volumeHex4 + temperatureHex2 + "1E" + patternCode``,
  with volume in **millilitres** (seekbar 30–500, default 225), temperature
  in **°C** (seekbar 85–96; the endpoints send the fixed RT/BP constants
  85/95), and pattern ``center=00`` / ``circular=01`` / ``spiral=02``.
* Grind size — ``CoffeeConstantUtil.getGrindSizeJ20``: **1–30, default 15**.
* ``work_mode`` / ``warning`` code tables — ``AppExtendsKt``'s J20 predicates
  (``isJ20Grinding`` …) and ``AppWsManager``'s warning switch; strings from
  the app's resources.
"""
from __future__ import annotations

WORK_MODE_IDLE = "idle"
WORK_MODE_AWAKE = "awake"
WORK_MODE_READY = "ready"
WORK_MODE_GRINDING = "grinding"
WORK_MODE_BREWING = "brewing"
WORK_MODE_COMPLETE = "complete"
WORK_MODE_SLEEPING = "sleeping"

# AppExtendsKt.isJ20Grinding. The thing shadow has no grinding flag of its
# own (``grinding_start`` only arrives over the app's WebSocket).
GRINDING_WORK_MODES = frozenset({4, 7, 11, 13})

WORK_MODE_STATES: dict[int, str] = {
    0: WORK_MODE_IDLE,
    1: WORK_MODE_AWAKE,
    4: WORK_MODE_GRINDING,
    5: WORK_MODE_READY,
    6: WORK_MODE_BREWING,
    7: WORK_MODE_GRINDING,
    8: WORK_MODE_BREWING,
    9: WORK_MODE_SLEEPING,
    10: WORK_MODE_COMPLETE,
    11: WORK_MODE_GRINDING,
    12: WORK_MODE_COMPLETE,
    13: WORK_MODE_GRINDING,
    14: WORK_MODE_BREWING,
    15: WORK_MODE_BREWING,
}

WARNING_NONE = "none"
WARNING_GRIND_SETTING = "grind_setting"
WARNING_WATER_LOW = "water_low"
WARNING_DOCK = "dock"
WARNING_NO_BEANS = "no_beans"
WARNING_WATER_SHORTAGE = "water_shortage"
WARNING_VOLTAGE = "voltage"
WARNING_UNKNOWN = "unknown"

# Codes AppWsManager's switch deliberately ignores; they read as no warning.
IGNORED_WARNINGS = frozenset({166, 183, 185})

# AppWsManager's switch over state.reported.warning. Any other non-zero code
# is reported as "unknown" (and still counts as a problem).
WARNING_STATES: dict[int, str] = {
    162: WARNING_GRIND_SETTING,
    169: WARNING_WATER_LOW,
    171: WARNING_DOCK,
    176: WARNING_GRIND_SETTING,
    182: WARNING_NO_BEANS,
    190: WARNING_WATER_SHORTAGE,
    191: WARNING_VOLTAGE,
    192: WARNING_VOLTAGE,
    193: WARNING_VOLTAGE,
}

GRIND_SIZE_MIN = 1
GRIND_SIZE_MAX = 30
GRIND_SIZE_DEFAULT = 15

VOLUME_MIN = 30
VOLUME_MAX = 500
VOLUME_DEFAULT = 225

TEMPERATURE_MIN = 85
TEMPERATURE_MAX = 96
TEMPERATURE_DEFAULT = 85
# Fixed constants used at the seekbar's ends (TemperatureConstant.J20).
TEMPERATURE_RT = 85
TEMPERATURE_BP = 95

PATTERN_CENTER = "center"
PATTERN_CIRCULAR = "circular"
PATTERN_SPIRAL = "spiral"
PATTERN_CODES: dict[str, str] = {
    PATTERN_CENTER: "00",
    PATTERN_CIRCULAR: "01",
    PATTERN_SPIRAL: "02",
}
PATTERN_DEFAULT = PATTERN_CENTER

# AppWsManager / BrewerJ20Activity's reading of state.reported.disp_reserve.
DISP_RESERVE_MEANINGS: dict[int, str] = {
    0: "pour_start",
    1: "pouring",
    2: "pour_finish",
    3: "recipe_changed",
    4: "easy_mode_start",
    6: "idle",
}


def work_mode_state(value: int | None) -> str | None:
    """The state for a ``work_mode`` code; None (HA's Unknown) if unmapped."""
    if value is None:
        return None
    return WORK_MODE_STATES.get(value)


def is_grinding(work_mode: int | None) -> bool | None:
    if work_mode is None:
        return None
    return work_mode in GRINDING_WORK_MODES


def warning_state(value: int | None) -> str:
    if value is None or value == 0 or value in IGNORED_WARNINGS:
        return WARNING_NONE
    return WARNING_STATES.get(value, WARNING_UNKNOWN)


def warning_active(value: int | None) -> bool:
    """True when the warning is one the app would surface to the user."""
    return warning_state(value) != WARNING_NONE


def temperature_code(temperature_c: float) -> int:
    """The integer the app puts in the pour curve's two temperature hex digits.

    The seekbar's ends are *not* the values sent: at or below 85 °C the app
    sends the fixed RT constant (85) and at or above 96 °C the fixed BP
    constant (95), regardless of the exact endpoint reading.
    """
    if temperature_c <= TEMPERATURE_MIN:
        return TEMPERATURE_RT
    if temperature_c >= TEMPERATURE_MAX:
        return TEMPERATURE_BP
    return int(temperature_c)


def build_bruw_curve(
    volume_ml: int,
    temperature_c: float,
    pattern: str = PATTERN_DEFAULT,
) -> str:
    """Build the manual-pour ``bruw_curve`` string.

    Example: 225 ml at 93 °C with the center pattern →
    ``FFFF100500E15D1E00``.
    """
    volume = max(VOLUME_MIN, min(VOLUME_MAX, int(volume_ml)))
    temp = temperature_code(float(temperature_c))
    pattern_code = PATTERN_CODES.get(pattern, PATTERN_CODES[PATTERN_DEFAULT])
    return f"FFFF1005{volume:04X}{temp:02X}1E{pattern_code}"
