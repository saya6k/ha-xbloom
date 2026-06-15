# xBloom J15 BLE Protocol

Reverse-engineered reference for the xBloom J15 BLE protocol. Independent
documentation based on HCI snoop captures (Android developer options →
Bluetooth HCI snoop log, pulled via `adb bugreport`) and APK decompilation of
the official app with [jadx](https://github.com/skylot/jadx). Not affiliated
with, endorsed by, or sponsored by xBloom.

Tested firmware: **V12.0D.500** (J15). Other firmware versions may differ —
PRs with capture data from other units welcome.

Canonical repo: [`github.com/brAzzi64/xbloom-ble`](https://github.com/brAzzi64/xbloom-ble).
Python reference implementation lives in [`python/`](./python/).

---

## Device Info
- **Device Name**: `XBLOOM <suffix>` (BLE advertiser name; suffix is the unit's serial-number tail, e.g. `XBLOOM ABC123`)
- **Peripheral UUID** (CoreBluetooth / macOS): `32328477-12DE-4CFC-0262-62CA388C4047`
- **BLE MAC suffix**: `xx:xx:xx:xx:37:28` (as seen in Android logs)
- **Phone**: OPPO/OnePlus ColorOS device (CPH2791), MediaTek chipset
- **App**: `com.xbloom.tbdx` (APK pulled via adb, decompiled with jadx)

---

## GATT Structure

### Custom Service (main comms)
**UUID**: `0000E0FF-3C17-D293-8E48-14FE2E4DA212`

| Characteristic | Handle  | Properties                              | Role                               |
|----------------|---------|-----------------------------------------|------------------------------------|
| FFE1           | 0x0010  | Write w/o Response, Write               | **Command channel (app → device)** |
| FFE2           | ?       | Notify                                  | Notifications (device → app)       |
| FFE3           | ?       | Read, Write w/o Response, Write, Notify | Multi-purpose / status             |

### Standard GATT Service
- Manufacturer Name, Model Number, Serial Number
- Hardware/Firmware/Software Revision, System ID, PnP ID

---

## Fully Decoded Command Protocol

All writes go to **FFE1 (handle 0x0010)** via ATT Write Request.
All multi-byte integers are **little-endian**.

### Packet Format

#### Type 1 — `buildCommandString` (function code `01`)
Used for simple commands and int-array data commands.
```
58 01 01  [cmd_lo cmd_hi]  [len_b0 len_b1 len_b2 len_b3]  01  [N×4-byte LE ints]  [crc_lo crc_hi]
```

#### Type 2 — `buildCommandString2` (function code `02`)
Used for commands with raw hex payloads (mode switch, recipe data, etc.)
```
58 01 02  [cmd_lo cmd_hi]  [len_b0 len_b1 len_b2 len_b3]  01  [hex_data bytes]  [crc_lo crc_hi]
```

**Fields:**
| Field      | Size    | Value                                          |
|------------|---------|------------------------------------------------|
| Header     | 1 byte  | `0x58` (ASCII 'X' for xBloom)                 |
| Device ID  | 1 byte  | `0x01`                                         |
| Func code  | 1 byte  | `0x01` (type1) or `0x02` (type2/CodeModule2)  |
| Command    | 2 bytes | command code, little-endian                   |
| Length     | 4 bytes | `(data_bytes) + 12`, little-endian             |
| Sub-type   | 1 byte  | `0x01` (constant)                              |
| Data       | N bytes | command-specific payload                       |
| CRC16      | 2 bytes | CRC of all preceding bytes, little-endian     |

**CRC16:** Polynomial `0x8408` (reversed CCITT 0x1021), initial value `0`.
Implemented in `com.leonxtp.library.CRC16.calcCRC()`.

**Packet sizes:**
- 0 data bytes → 12 bytes total → ATT payload 12, L2CAP 15
- 4 data bytes → 16 bytes total → ATT payload 16, L2CAP 19
- N data bytes → (12 + N) bytes total

---

## Key Command Codes (from `CommandParams.java`)

```
APP_BREWER_START          =  4506   0x119A   Start brew
APP_BREWER_STOP           =  4507   0x119B   Stop brew
APP_BREWER_PAUSE          =  8019   0x1F53   Pause brew
APP_BREWER_QUIT           =  8013   0x1F4D   Quit brewer
APP_BREWER_RESTART        =  8021   0x1F55
APP_GRINDER_START         =  3500   0x0DAC   Start grinder
APP_GRINDER_STOP          =  3505   0x0DB1   Stop grinder
APP_GRINDER_PAUSE         =  8018   0x1F52   Pause grinder
APP_SET_CUP               =  8104   0x1FA8   Set cup weight range (theMax, theMin as floats)
APP_BYPASS                =  8102   0x1FA6   Bypass water + dose info
APP_EXIT_SCALE            =  8014   0x1F4E   Exit scale mode
APP_SCALE_TARE            =  8500   0x2134   Scale tare / zero
APP_WEIGHT_UNIT           =  8005   0x1F45   Switch weight unit (0=g, 1=oz, 2=ml)
APP_TEMP_UNIT             =  8010   0x1F4A   Switch temp display (0=°C, 1=°F) "设备显示温度"
APP_RECIPE_START_QUIT     =  8017   0x1F51
APP_TEA_RECIP_MAKE        =  4512   0x11A0   Execute tea recipe
APP_TEA_RECIP_CODE        =  4513   0x11A1   Send tea recipe code

RD_BackToHome             =  8022   0x1F56   Back to home screen
RD_EASYMODE_BEGIN         =  8111   0x1FAF   Auto mode begin
RD_EASYMODE_TYPE          = 11511   0x2CF7   ★ MODE SWITCH (auto ↔ pro)
RD_EASYMODE_RECIPE_NUM    = 40525   0x9E4D   Send recipe count
RD_EASYMODE_RECIPE_SEND   = 11510   0x2CF6   Send recipe hex
RD_EASYMODE_RECIPE_ORDER  = 11512   0x2CF8   Send recipe order
RD_WaterSource            =  4508   0x119C   Switch water source
RD_LetType                =  8103   0x1FA7   LED brightness
RD_CurrentGrinder         = 40526   0x9E4E   Back to normal state
```

---

## ★ Mode Switch — Fully Decoded

**Source**: `BleCodeFactory.easyModeSwitch()` → `buildCommandString2(RD_EASYMODE_TYPE, mode.getCode())`

| Mode      | Mode code (hex data) | Full packet (16 bytes)                            |
|-----------|----------------------|---------------------------------------------------|
| Pro Mode  | `00000000`           | `580102F72C1000000001000000002A90`                 |
| Auto/Easy | `91327856`           | `580102F72C100000000191327856FF58`                 |

**Breakdown (Pro Mode example):**
```
58         header
01         device ID
02         function code (CodeModule2 → type2)
F7 2C      command 11511 LE (0x2CF7 → F7, 2C)
10 00 00 00  length = 16 LE
01         sub-type
00 00 00 00  PRO mode code
2A 90      CRC16
```

**How to use:** Close phone app → run `send_command.py easy` or `send_command.py pro`.

---

## Computed Packet Reference

```
easy  (Auto/Easy Mode):  580102F72C100000000191327856FF58
pro   (Pro Mode):        580102F72C1000000001000000002A90
stop  (Stop brewer):     5801019B110C000000013643
quit  (Quit brewer):     5801014D1F0C000000018BD6
home  (Back to home):    580101561F0C00000001C015
```

---

## ★ Full Brew Sequence — HCI Snoop Log (2026-03-28)

Captured via Android `adb bugreport` with HCI snoop logging enabled on a Pixel 3.
Recipe: 1:16 ratio, 16g dose, 256ml total, 6 pours, no grinder, spiral pattern.

### APP→DEV Commands (in order)

| Δt (s) | Cmd   | Name               | Packet (hex)                                                       | Notes |
|---------|-------|--------------------|--------------------------------------------------------------------|-------|
| 0.0     | 8100  | MTU Handshake      | `580101A41F1400000001B900000001000000BDD1`                         | Same as ours |
| 3.1     | 8022  | Back to Home       | `580101561F0C00000001C015`                                         | App sends this on initial connect |
| (reconnect ~6 min later; user navigates to recipe, presses Start) |
| 0.0     | 8100  | MTU Handshake      | `580101A41F1400000001B900000001000000BDD1`                         | |
| 5.9     | 8100  | MTU Handshake      | (same — sent twice)                                                 | |
| 13.8    | 8102  | Bypass + Dose      | `580101A61F1800000001000000000000000010000000088C`                  | **NEW** — [0.0f, 0.0f, 16] = bypass OFF, dose=16g |
| 14.3    | 8104  | Set Cup            | `580101A81F1400000001000048430000A0422A0F`                         | **NEW** — [200.0f, 80.0f] = cup weight range |
| 14.7    | 8004  | Recipe (no grind)  | `580101441F3F000000013032...46A01635` (63 bytes)                   | Recipe blob |
| 16.3    | 8002  | Execute Recipe     | `580101421F0C000000017FCF`                                         | Start brew |

### DEV→APP Notifications (brew events only)

| Δt (s) | Cmd   | Name             | Payload decode |
|---------|-------|------------------|----------------|
| 16.7    | 8002  | Execute ACK      | |
| 16.7    | 40502 | Coffee Starting  | |
| 17.7    | 40510 | Pour Start       | pour_index=0 (bloom) |
| 60.9    | 40510 | Pour Start       | pour_index=1 |
| 83.9    | 40510 | Pour Start       | pour_index=2 |
| 106.9   | 40510 | Pour Start       | pour_index=3 |
| 129.9   | 40510 | Pour Start       | pour_index=4 |
| 155.0   | 40510 | Pour Start       | pour_index=5 |
| 178.0   | 40511 | Brewer Stop      | |
| 180.1   | 40512 | Enjoy!           | |
| 186.4   | 40513 | Enjoy! (2)       | |

**Total brew time: ~161 seconds.** No Pour Volume ACK (40515) or Pour Transition
(40516) observed in this session; those may be firmware-version-dependent.

**Post-brew:** No cleanup commands from app. Machine Activity returns to `0x01`.
The app just receives Enjoy! and eventually disconnects.

### Comparison: App vs our brew.py

| Step | App sends | Our script sends | Difference |
|------|-----------|-----------------|------------|
| 1 | Handshake (8100) | Handshake (8100) | Same |
| 2 | Back to Home (8022) | — | We skip |
| 3 | Bypass+Dose (8102) | — | **We skip — sends dose to machine** |
| 4 | Set Cup (8104) | — | **We skip — sets cup weight range** |
| 5 | Recipe (8004) | Recipe (8004) | Blob differs (see below) |
| 6 | Execute (8002) | Execute (8002) | Same |

### Recipe Blob Differences

| Field | App sends | Our script sends |
|-------|-----------|-----------------|
| Pattern | 2 (spiral) | 0 (center) — user preference, not a bug |
| Bloom vibration | 2 (after) | 0 (none) — user preference |
| **RPM byte (timing byte 2)** | **90 (0x5A)** | **rpm param** — grinder RPM, stored in first pour's timing block |
| **Grinder tail byte** | **70** (machine's stored size) | **0** (when --no-grind) |
| **Ratio tail byte** | **160 (0xA0) = ratio×10** | **ratio×10** — fixed (was grand_water/10) |
| Last pour post_wait | 10s | 0s (we set 0; doesn't matter, machine ignores) |

---

## Source Files (APK Decompilation)

Located at `~/Development/xbloom-ble-capture/xbloom_decompiled/sources/`

| File | Role |
|------|------|
| `xbloom/others/VerifyCodeUtils.java` | **Packet builder** — `buildCommandString` / `buildCommandString2` |
| `xbloom/others/CommandParams.java` | **All command codes** + UUID constants |
| `xbloom/others/CodeModule.java` | Command container (int data) |
| `xbloom/others/CodeModule2.java` | Command container (hex string data) — triggers type2 builder |
| `xbloom/others/TypeConversion.java` | Byte encoding utilities (little-endian) |
| `com/leonxtp/library/CRC16.java` | CRC16 (poly 0x8408, init 0) |
| `com/xbloom/util/BleCodeFactory.java` | **High-level command factory** |
| `com/xbloom/model/DeviceMode.java` | Mode enum: PRO→`"00000000"`, EASY→`"91327856"` |
| `com/chisalsoft/andite/manager/AppBleManager.java` | BLE send/receive manager |
| `com/chisalsoft/andite/uicontroller/activity/recipe/RecipeDetailActivity.java` | **Brew flow** — sendBypassJ15, sendCupJ15, sendCodeJ15, startJ15 |
| `com/chisalsoft/andite/uicontroller/activity/ScaleActivity.java` | Scale UI — tare (8500), exit scale (8014), weight unit (8005) |
| `com/chisalsoft/andite/http/response/GetRecipeCodeResponse.java` | API response: theCode, theMax, theMin |
| `com/chisalsoft/andite/uicontroller/fragment/MachineJ15Fragment.java` | UI — mode toggle (`setDeviceMode()`) |

---

## Replay Script

**`~/Development/xbloom-ble-capture/send_command.py`** — fully implemented

```
python3 send_command.py easy   # Switch to Auto/Easy Mode
python3 send_command.py pro    # Switch to Pro Mode
python3 send_command.py stop   # Stop brewing
python3 send_command.py quit   # Quit brewer
python3 send_command.py home   # Back to home
```

### Recipe files

Recipes can be saved to JSON and reused across brew and slot commands:

```bash
# Save a recipe while brewing:
python3 brew.py --save ./my-recipe.json --dose 17 --no-grind --temp 91 --pattern spiral \
    --pour 50 20 --pour 50 10 --pour 50 10 \
    --pour 50 10 --pour 50 10 --pour 50 0

# Save only (no brew):
python3 brew.py --save ./my-recipe.json --dry-run --dose 17 --no-grind --temp 91 \
    --pour 50 20 --pour 50 0

# Brew from saved recipe (two examples ship in `python/recipe-examples/`):
python3 brew.py --load ./my-recipe.json
python3 brew.py --load recipe-examples/simple.json

# Write to Easy Mode slot from saved recipe:
python3 send_command.py slot A --load recipe-examples/example2.json --scale on
```

Scan → connect → write to FFE1 → listen on FFE2 for response.

---

## Capture Infrastructure

### Tools
- `adb` — connected to an Android device (Pixel 3 used for this capture)
- `bleak` (Python) — installed, used in send_command.py / brew.py / monitor.py
- `parse_hci.py` — custom btsnoop_hci.log parser for xBloom BLE traffic
- Android bugreport — working method for full HCI snoop log extraction

### Capture Method (updated 2026-03-28)
1. Enable **Bluetooth HCI snoop log** in Android Developer Options
2. Perform actions in xBloom app
3. `adb bugreport bugreport.zip`
4. Extract: `unzip -o bugreport.zip "FS/data/misc/bluetooth/logs/btsnoop_hci.log*" -d .`
5. Parse: `python3 parse_hci.py --no-weight FS/data/misc/bluetooth/logs/btsnoop_hci.log`

**Pixel 3** (crosshatch, Android 9) produces standard `btsnoop_hci.log` with full payloads.
The `--no-weight` flag suppresses the ~100ms weight/water notifications for cleaner output.
Previous OPPO/MediaTek phone only gave text logs without payloads.

---

## Connection Sequence (Confirmed Working)

The machine **requires a handshake** after connecting before it will respond to commands.
Without it, writes succeed at the BLE level but the machine ignores them entirely (no display
wake, no BLE indicator, no responses).

### Step-by-step
1. Connect to device (`32328477-12DE-4CFC-0262-62CA388C4047`)
2. Subscribe to FFE2 notifications
3. Send **handshake** (within ~200ms of connect):
   ```
   580101A41F1400000001B900000001000000BDD1
   ```
   = `buildCommandString(8100, [185, 1])` — "MTU negotiation" signal
   Source: `AppBleManager.mtuSuccess()` → `CodeModule(8100, "MTU协商", Opcodes.INVOKEINTERFACE=185, 1)`
4. Machine display wakes up, BLE dot appears, status notifications begin flooding in
5. Send your actual command

**Use `response=False`** (Write Without Response / ATT opcode 0x52); Write With Response is rejected with `CBATTErrorDomain Code=14`.

---

## Notification Format (Device → App, FFE2)

Responses use the **same packet structure** as commands, with function code `0x07`:
```
58 02 07  [cmd_lo cmd_hi]  [len_b0..b3]  [status]  [data bytes]  [crc_lo crc_hi]
```
The byte at offset 9 (where commands have `01`) appears to be a **status byte**:
- `C1` = periodic status update
- `C2` = ACK for a command we sent

### Confirmed Notification Codes (from live session)

| Code  | Constant            | Hex    | Observed data          | Meaning                      |
|-------|---------------------|--------|------------------------|------------------------------|
| 8100  | (handshake)         | 0x1FA4 | —                      | Handshake ACK                |
| 40521 | RD_MachineInfo      | 0x9E49 | 61-byte blob           | Serial number, firmware ver  |
| 8011  | RD_MachineNotSleeping | 0x1F4B | —                    | Machine is awake             |
| 8023  | RD_MachineActivity  | 0x1F57 | `01000000` or `1D000000` | Activity state             |
| 11511 | RD_EASYMODE_TYPE    | 0x2CF7 | mode code              | Mode switch ACK              |
| 20501 | RD_CURRENT_WEIGHT2  | 0x5015 | 4-byte float?          | Scale weight (~100ms period) |
| 40523 | RD_WATER_VOLUME     | 0x9E4B | 4-byte value           | Water level (~100ms period)  |

### MachineInfo blob (cmdCode=40521, 61 bytes)
Raw: `580207499E4B000000C14A313541303146354157303136FFFFFFFFFFFF5631322E30442E353030003FC547010001005D0F6E01014158000000808BC642`

Decoded (ASCII visible in the middle):
- `4A313541303146354157303136` = `J15A01F5AW016` — serial / model string
- `FFFFFFFFFFFF` — padding
- `5631322E30442E353030` = `V12.0D.500` — **firmware version**

---

## ★ Proof of Concept — CONFIRMED WORKING (2026-03-01)

Both mode switches tested and confirmed on physical hardware:

```
python3 send_command.py easy   → switched to Auto/Easy Mode ✓
python3 send_command.py pro    → switched to Pro Mode ✓
```

Machine responded with mode-switch ACK (`cmdCode=11511, status=C2`) in both cases.

---

## TODO / Next Steps

- [x] Identify BLE service/characteristic UUIDs
- [x] Decode full packet format from APK source
- [x] Find mode switch command codes
- [x] Build proof-of-concept replay script
- [x] **Run proof of concept** — mode switch confirmed working on physical device
- [x] Discover post-connect handshake requirement
- [x] Decode periodic status notification codes
- [x] Decode scale weight — `RD_CURRENT_WEIGHT2` (20501): LE float at bytes[10:14] — confirmed live
- [x] Decode water volume — `RD_WATER_VOLUME` (40523): LE float at bytes[10:14]
- [x] Decode full MachineInfo blob (40521) — all fields mapped
- [x] Scale press confirmed: `In Scale` (9002) + `Scale Out` (9008) events fired correctly
- [x] Timed grind confirmed — `grind 4` ran 4s and received `Grinder Stop` ACK ✓
- [x] Map the 63-byte recipe config blob — fully decoded via HCI snoop log (2026-03-28)
- [x] Discover Bypass+Dose (8102) and Set Cup (8104) commands from HCI capture
- [x] Discover Scale commands: Exit Scale (8014), Tare (8500), Weight Unit (8005)
- [x] Correct recipe tail byte: ratio×10, not grand_water/10 (see "Tail bytes corrected")
- [x] Identify bloom timing byte 2 = rpm value from server (120 in HCI log)
- [x] 8102 + 8104 added to brew flow — scale no longer drifts after single brews
- [x] Scale tare (8500) confirmed working — zeroes scale instantly
- [x] Large pour volumes (300ml) confirmed — 127ml chunking works correctly (2026-03-29)
- [x] Scale accuracy tested — ~17g fixed thermal offset on hot water, not proportional drift
- [ ] Determine what bloom timing byte 2 (rpm=120) actually controls
- [ ] Build an interactive REPL / persistent connection mode

---

## Scale / Weight

Both `RD_CURRENT_WEIGHT2` (20501) and `RD_WATER_VOLUME` (40523) use identical encoding:
- Data is a **little-endian IEEE 754 float** at bytes[10:14] of the notification
- `struct.unpack('<f', data[10:14])[0]`
- Streams continuously at ~100ms intervals while connected
- Machine also fires state events: `In Scale` (9002) when object placed, `Scale Out` (9008) when removed

Confirmed live on hardware: weight readings from 0 → ~972g while pressing scale.

Scale tare (command 8500) confirmed working: zeroes the reading instantly via
`python3 send_command.py tare`.

**Known issue**: After multiple consecutive brews, the scale can drift significantly
(observed -366g after 3 back-to-back brews). Tare command fixes it. Root cause TBD.

### Scale accuracy tests (2026-03-30)

Hot water tests at 93°C with no coffee grounds, comparing xBloom scale vs
external calibrated scale (both agreed on empty jar weight).

| Programmed | External scale | xBloom scale | Gap | xBloom/External |
|------------|---------------|-------------|-----|-----------------|
| 100ml | 99.1g | 85.1g | 14.0g | 85.9% |
| 200ml | 188.8g | 170.9g | 17.9g | 90.5% |
| 400ml (1 pour) | 374.7g | 356.4g | 18.3g | 95.1% |
| 400ml (8×50ml) | 379.2g | 359.8g | 19.4g | 94.9% |
| ~800ml (ran dry) | 746.9g | 729.8g | 17.1g | 97.7% |

**Findings:**

1. **xBloom scale has a ~15-19g fixed thermal offset** when reading hot water.
   The gap does not scale proportionally with volume — it stays in the 14-19g
   range from 100ml to 800ml. This is consistent with heat from the hot water
   affecting the load cell.

2. **Machine water delivery is reasonably accurate.** External scale shows
   the machine delivers within ~2-3% of the density-adjusted expectation
   (93°C water weighs 0.9634 g/ml).

3. **The ~7% shortfall in coffee brews** was the fixed ~17g thermal offset
   plus ~3.7% density effect (ml vs g at 93°C) adding up to look like a
   consistent percentage at typical brew volumes (250-540ml).

4. **Splitting pours doesn't affect accuracy** — 1×400ml and 8×50ml
   delivered similar total water and showed similar scale gaps.

5. **Tank capacity limits large brews.** 800ml ran dry mid-pour with
   "Error: No Water" (40522). The 540ml brew also hit this. Refill
   before batches >500ml.

---

## Machine Info — Full Field Map (RD_MachineInfo = 40521)

Data = bytes[10:-2] of notification. All offsets are byte offsets into payload.

| Byte offset | Chars | Field         | Decode                                    | Our machine       |
|-------------|-------|---------------|-------------------------------------------|-------------------|
| 0–12        | 0–25  | serialNumber  | ASCII bytes                               | `J15A01F5AW016`   |
| 13–18       | 26–37 | theModel      | ASCII bytes (0xFF = blank)                | (blank)           |
| 19–28       | 38–57 | theVersion    | ASCII bytes                               | `V12.0D.500`      |
| 29–32       | 58–65 | areaAp        | LE float                                  | 100990.0          |
| 33          | 66–67 | waterEnough   | uint8 (0=low, 1=ok)                       | 1 (ok)            |
| 34          | 68–69 | systemStatus  | uint8                                     | 0                 |
| 35          | 70–71 | userCount     | uint8                                     | 1                 |
| 36          | 72–73 | waterFeed     | uint8 (0=tank, 1=tap)                     | 0 (tank)          |
| 37          | 74–75 | grinder (raw) | uint8 − 30, min 1                         | 93−30 = **63**    |
| 38          | 76–77 | ledType       | uint8                                     | 15                |
| 39          | 78–79 | voltage       | uint8                                     | 110               |
| 40          | 80–81 | tempUnit      | uint8 (0=°C, 1=°F)                        | 1 (°F)            |
| 41          | 82–83 | weightUnit    | uint8 (0=g, 1=oz)                         | 1 (oz)            |
| 51–54       | 102–109 | modeType    | hex match vs `91327856` → EASY else PRO  | PRO               |
| 55–58       | 110–117 | pouringRadius | LE uint32                               | 0                 |
| 59–62       | 118–125 | vibrationInit | LE uint32                               | 0                 |

---

## ★ New Commands Discovered (2026-03-28 HCI capture)

### Command 8102 — Bypass Water + Dose

**Source**: `RecipeDetailActivity.sendBypassJ15()`

Sends bypass pour info and the coffee dose. Type 1 packet with 3 int args.

```
CodeModule(8102, "Bypass", [
    Float.floatToIntBits(bypassVolume),    // 0 if bypass disabled
    Float.floatToIntBits(bypassTemp * 10), // 0 if bypass disabled
    (int) recipe.dose                      // dose in grams (plain int, NOT float-encoded)
])
```

**Example from HCI log** (bypass OFF, 16g dose):
```
580101 A61F 18000000 01 00000000 00000000 10000000 088C
                        ^^^^^^^^ ^^^^^^^^ ^^^^^^^^
                        bypass=0 temp=0   dose=16
```

The app sends this even when bypass is disabled; it still communicates the **dose** to the
machine. Our script doesn't send this, meaning the machine doesn't know the dose weight.

### Command 8104 — Set Cup (weight range)

**Source**: `BleCodeFactory.setCup(float max, float min)`, `RecipeDetailActivity.sendCupJ15()`

Tells the machine the expected cup weight range. Type 1 packet with 2 int args
(float bits packed into ints via `Float.floatToIntBits()`).

```
CodeModule(8104, "设置胶囊杯类型", Float.floatToIntBits(theMax), Float.floatToIntBits(theMin))
```

**`theMax` and `theMin` come from the xBloom cloud API** (`GetRecipeCodeResponse`),
not computed locally. The server returns them alongside the recipe hex blob.

**Example from HCI log** (theMax=200.0, theMin=80.0):
```
580101 A81F 14000000 01 00004843 0000A042 2A0F
                        ^^^^^^^^ ^^^^^^^^
                        200.0f   80.0f
```

**Hypothesis**: This configures the scale's expected weight range for the brew. Skipping
it may leave the scale in an unconfigured state.

**Open question**: The meaning of theMax=200.0 and theMin=80.0 is unclear. These values
come from the xBloom cloud API based on `cupType` (xPod=1, xDripper=2, Other=3, Tea=4).
They may represent cup volume capacity limits or scale display range, but the exact
semantics are unknown. The machine brews correctly regardless of these values.

### Command 8014 — Exit Scale Mode

**Source**: `ScaleActivity.onBackPressed()`

Sent when the app leaves the scale screen. No data args.

```
CodeModule(8014, "退出称重页面")
```

The machine sends `RD_IN_SCALE` (9002) when something is placed on the scale, which triggers
the app to open ScaleActivity. When the user navigates away, the app sends 8014 to tell the
machine to exit scale mode, and the machine responds with `RD_OUT_SCALE` (9008).

### Command 8500 — Scale Tare / Zero

**Source**: `ScaleActivity`, tare button handler

Zeroes the scale reading. No data args.

```
CodeModule(8500, "称重清零")
```

### Command 8005 — Weight Unit Switch

**Source**: `ScaleActivity.updateWeightUnit()`

Switches the scale display unit. Type 1 packet with 1 int arg.

```
CodeModule(8005, "重量单位切换", unitType)
```

Where `unitType` = `WeightUnitType.G`, `WeightUnitType.Ml`, or `WeightUnitType.Oz`.

---

## Recipe Blob — Updated Understanding (2026-03-28)

### Key correction: recipe blob is server-encoded

Even for user-created local recipes, the app sends the pour data as JSON to xBloom's
cloud API (`getRecipeCodeJ15()` → `GetRecipeCodeTransfer` HTTP call), which returns:
- `theCode` — the encoded recipe hex string
- `theMax` — cup max weight (float)
- `theMin` — cup min weight (float)

The form sent to the server includes: `pourDataJSONStr` (pour list as JSON), `grinderSize`,
`grandWater`, `rpm` (likely the bloom timing byte 2), `cupType`, and `tableId` (if any).

The app then sends these to the machine via commands 8104 + 8004.
`GetRecipeCodeManager.sendData2Hex()` is the local recipe encoder used for both
Easy Mode slot programming (11510) and PRO mode on-the-fly brews (8004/8001).
The cloud API (`getRecipeCodeJ15`) may also encode recipes but returns the same format.

### Timing byte 2 (grinder RPM)

In `GetRecipeCodeManager.sendData2Hex(JSONArray, byte[] bArr, Long l)`:
- The `Long l` parameter is `recipe.getRpm()` — the grinder RPM speed setting
- Stored in byte **2** of the first pour's timing block only; subsequent pours get 0
- Timing block byte order: `[post_wait_neg, 0x00, rpm, flow_rate]`
- HCI captures: value = 90 across all three test brews (matching RPM=90 setting)
- Previously misidentified as "bloom hold time"; confirmed as RPM via decompiled code
  and three-way HCI comparison (see `debugging/three-way-comparison.txt`)

### Per-pour parameter ranges (from app UI)

| Parameter | Range | Default | Encoding |
|-----------|-------|---------|----------|
| Volume | 0–240ml (app limit; 300ml+ tested OK) | — | raw byte, >127 splits into chunks |
| Temperature | RT, 40–95°C, BP | 91°C | raw byte (Celsius) |
| Flow rate | 3.0–3.5 | 3.0 | `int(rate × 10)` in timing byte 3 |
| Pausing (post_wait) | 0–59s | 0 | `(-seconds) & 0xFF` in timing byte 0 |
| Pour type | center(0), circular(1), spiral(2) | center | substep byte 2 |
| Agitation before | on/off | off | substep byte 3, bit pattern |
| Agitation after | on/off | off | substep byte 3, bit pattern |

**Dripper type** (xPod, xDripper, Omni, Other) is app-UI only; it constrains
the dose slider range but is not sent in the recipe blob. The machine doesn't
know which dripper is attached.

**Per-pour vs global**: The app allows setting pattern, vibration, flow rate, and
temperature independently per pour. Our CLI applies them all globally (`--temp`,
`--pattern`, `--vibration`, `--flow-rate`). Per-pour temperature overrides are
available via JSON recipes (`--load`); `encode_recipe()` reads per-pour values
from each dict.

### Tail bytes corrected

```
[grinder_byte] [ratio_byte]
```

| Byte | Field | Encoding | HCI example |
|------|-------|----------|-------------|
| 0 | grinder_size | Raw value (0 = off) | 70 (0x46) — machine's stored grinder size, sent even with no-grind |
| 1 | ratio × 10 | `int(ratio * 10) & 0xFF` | 160 (0xA0) = 1:16 ratio → 16 × 10 |

**Previously we thought byte 1 was `grand_water / 10`. This is wrong.**
The decompiled `GetRecipeCodeService.executeClient()` passes
`[grinderSize, grandWater × mulNumber]` where `mulNumber` defaults to 10.
The field `grandWater` in the Recipe model is the brew ratio, NOT total water —
confirmed by `RecipeDetailActivity` line 670 which validates
`dose × grandWater == totalPourVolume` (i.e. 16 × 16 = 256ml).
HCI capture: 0xA0 (160) for a 16g dose / 1:16 / 256ml recipe → ratio 16 × 10 = 160.
The machine appears to ignore this byte (metadata only).

---

## Settings Commands — HCI Confirmed (2026-03-29)

All settings commands are Type 1 packets with a single int arg.

### Command 8010 — Temperature Unit
**Source**: `MachineJ15Fragment`, "设备显示温度" (device display temperature)
```
CodeModule(8010, "设备显示温度", value)   // 0=°C, 1=°F
```

### Command 8005 — Weight Unit
**Source**: `ScaleActivity.updateWeightUnit()`
```
CodeModule(8005, "重量单位切换", value)   // 0=g, 1=oz, 2=ml
```

### Command 4508 — Water Source
**Source**: `MachineJ15Fragment`
```
CodeModule(4508, value)                   // 0=tank, 1=tap
```

### Command 11511 — Mode Switch
Type 2 packet (hex payload). See "Mode Switch" section above.
```
buildCommandString2(11511, modeCode)      // "00000000"=PRO, "91327856"=EASY
```

### All settings via send_command.py
```
python3 send_command.py temp-c       # °C
python3 send_command.py temp-f       # °F
python3 send_command.py unit-g       # grams
python3 send_command.py unit-ml      # milliliters
python3 send_command.py unit-oz      # ounces
python3 send_command.py water-tank   # tank water
python3 send_command.py water-tap    # tap water
python3 send_command.py easy         # Auto/Easy Mode
python3 send_command.py pro          # Pro Mode
python3 send_command.py tare         # Zero scale
```

---

## Easy Mode Slots — HCI Confirmed (2026-03-29)

### Command 11510 — Easy Recipe Send

Type 2 packet. Sends a recipe to an Easy Mode slot (A/B/C). The app sends all 3
slots when syncing, each as a separate 11510 command.

**Payload format**: `[slot_index] [flags] [recipe_blob]`

| Byte | Field | Values |
|------|-------|--------|
| 0 | slot_index | 0=A, 1=B, 2=C |
| 1 | flags | bit field (see below) |
| 2+ | recipe_blob | same format as `encode_recipe()` output |

**Flags byte**:
| Bit | Mask | Meaning |
|-----|------|---------|
| 4 | 0x10 | Scale: 1=ON, 0=OFF |
| 1 | 0x02 | Grinder ON |
| 2 | 0x04 | Grinder OFF |

| Flags | Scale | Grinder |
|-------|-------|---------|
| 0x04 | OFF | OFF |
| 0x14 | ON | OFF |
| 0x02 | OFF | ON |
| 0x12 | ON | ON |

**Sync flow**: App sends 11510 × 3 (one per slot), machine ACKs each, then
sends 11512 (Recipe Order) at the end.

Empty/unchanged slots may be sent with a short 3-byte payload instead of a
full recipe blob.

---

## Confirmed Brew Tests (2026-03-29)

All brews use the updated flow: handshake → 8102 → 8104 → recipe → execute.

| Dose | Ratio | Water | Pours | Pattern | Result |
|------|-------|-------|-------|---------|--------|
| 16g  | 1:16  | 256ml | 6 × ~43ml | center | ✓ Completed, scale OK after |
| 17g  | 1:18  | 300ml | 6 × 50ml | spiral | ✓ Completed |
| 15g  | 1:18  | 270ml | 6 × 45ml | spiral | ✓ Completed (×2 back-to-back) |
| —    | —     | 600ml | 2 × 300ml | spiral | ✓ Completed — 127ml chunking works, 543g in cup |

**Volume chunking**: Pours > 127ml are automatically split into 127ml substeps
(same temp/pattern/vibration). 300ml → 127 + 127 + 46. Machine handles this correctly.

---

## Grinder Commands (Pro Mode)

Source: `GrinderActivity.java`, `CoffeeConstantUtil.java`

### Parameters
| Parameter    | Default | Range   | Step | Our machine |
|--------------|---------|---------|------|-------------|
| grind_size   | 70      | 1–80    | 1    | 63          |
| speed        | 100     | 60–120  | 10   | —           |

### Command sequence
```
1. APP_GRINDER_IN   (8006, [grind_size, speed])  — "entering grinder screen"
2. APP_GRINDER_START (3500, [1000, grind_size, speed])  — start grinding
   (1000 = constant first arg the app always sends)
3. APP_GRINDER_STOP (3505, [])  — stop grinding
   APP_GRINDER_PAUSE (8018, [])  — pause
   APP_GRINDER_RESTART (8020, [])  — resume after pause
```

### Expected response codes during grind
- `RD_IN_GRINDER` (9000) — grinder module engaged
- `RD_GRINDER_BEGIN` (9003) — grinding started
- `RD_Grinder_Stop` (40507) — grinder stopped
- `RD_OUT_GRINDER` (9004) — grinder module disengaged
- `RD_GearReport` (40505) — gear position report
- `RD_CurrentGrinder` (40526) — current grinder state
