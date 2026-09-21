# Mock Marstek Device

A mock Marstek device for testing the Home Assistant integration without a real device. Includes realistic battery simulation with dynamic SOC changes, power fluctuations, and mode transitions.

`--device` and `--ver` select the same **firmware profile** the integration uses. The mock **encodes** physical watts and watt-hours onto the wire the way that firmware would. The integration **decodes** them back to W and Wh. Legacy profiles also reject SYS and UPS with JSON-RPC `-32601 Method not found`.

## Package Structure

```
mock_device/
├── __init__.py           # Package exports
├── __main__.py           # CLI entry point
├── const.py              # Constants and defaults
├── device.py             # UDP server (MockMarstekDevice)
├── handlers.py           # API method handlers
├── utils.py              # Utility functions
├── mock_marstek.py       # Backwards compatibility shim
└── simulators/
    ├── __init__.py
    ├── battery.py        # BatterySimulator
    ├── household.py      # HouseholdSimulator
    └── wifi.py           # WiFiSimulator
```

## Features

- **Firmware profiles**: `--device` + `--ver` choose legacy vs Rev 3.1 encodings and which writes are accepted
- **Per-family hardware**: `--device` also picks the pack size and the rated AC power, so a Venus A does not report a Venus E's 5.12 kWh
- **Closed-loop Auto mode**: regulates the simulated P1/CT reading toward zero through a lagged measurement, a deadband and a ramp-limited inverter
- **Simulated home**: smooth two-peak residential load curve (~10 kWh/day) plus a rooftop PV array following real solar geometry
- **Battery physics**: round-trip conversion losses, SOC tapering, standby draw and a thermal model the BMS gates charging on
- **Mode Support**: Auto, AI, Manual, Passive; UPS only when the profile reports `supports_ups`
- **SYS writes**: Capable firmware accepts `DOD.SET`, `Ble.Adv`, and `Led.Ctrl` with `set_result: true`; legacy firmware returns `-32601 Method not found`
- **Passive Mode Timer**: Automatic expiration after configured duration
- **Manual Schedules**: Slots 0–9, or 0–5 for Venus E mini
- **Household Simulation**: Realistic time-of-day consumption patterns
- **Status Display**: Periodic console output showing current battery state

## Firmware-profile CLI

Physical values inside the simulator are always SI (W, Wh). Before a UDP response is sent, the mock divides by the profile scale so the wire looks like real firmware:

| Generation | Typical `--ver` | Wire encoding | Accepted extras |
|------------|-----------------|---------------|-----------------|
| Legacy (148 or older) | `145` (default) or `148` | Solar energy as Wh; Venus A/D channel-1 PV power as deciwatts; no EM 0.1 Wh totals | SYS and UPS return **Method not found** |
| Venus A 149 | `149` | Solar energy as 0.01 kWh (`Wh / 10` on the wire); channel-1 PV still deciwatts | SYS and UPS return **Method not found** |
| Rev 3.1 | `150` or newer | Solar energy as 0.01 kWh; Venus A/D channel-1 PV still deciwatts (150.9); EM energies as 0.1 Wh | SYS + UPS accepted on families that support them |

Issue [#57](https://github.com/taurgis/has-marstek-local-api/issues/57) is **PV1 power** 10× too high (firmware **148.3** and **150.9** after integration 1.1.0). Keep channel-1 ÷10. Issue [#35](https://github.com/taurgis/has-marstek-local-api/issues/35) is **total solar energy** 10× too low on firmware **149** (`total_pv_energy` as 0.01 kWh). Do not collapse those onto one scale or one Venus A 150 mock.

Venus E mini is a distinct family (`--device "Venus E mini"`). It must not be configured as Venus E if you need the SYS-without-150 and slots 0–5 behavior.

## Firmware UDP quirks

The mock reproduces Control firmware behavior found in VNSE3-0 / HMG-50
binaries (see [tools/firmware/ANALYSIS.md](../firmware/ANALYSIS.md)):

- JSON-RPC `id` is stored as uint16 (`65536` replies as `0`)
- Invalid JSON replies with parse error `id=0`, code `-32700`
- A 0-byte UDP datagram freezes later Open API replies
- Reset-prone firmware duplicates each UDP reply (WiFi + Ethernet send
  anomaly). VNSE3-0 **150+** and HMG-50 **156** send a single reply
- HMG-50 Control **153** includes `bat_power` in `ES.GetStatus`; **155/156** omit it
- HMG-50 accepts `Wifi.SetConfig`; Venus E/A/D return Method not found
- `Set.Ver` / `Reset.Factory` succeed on firmware whose recv list includes them
  (1487 and 149+). They stay unimplemented in Home Assistant

## Usage

### As a Module (Recommended)

```bash
cd /workspaces/ha_marstek/tools
python -m mock_device [OPTIONS]
```

### Standalone (Backwards Compatible)

```bash
cd /workspaces/ha_marstek
python3 tools/mock_device/mock_marstek.py [OPTIONS]
```

### Options

- `--port PORT` - UDP port (default: 30000)
- `--ip IP` - Override reported IP address
- `--device TYPE` - Device type (default: `"VenusE 3.0"`)
- `--ver INTEGER` - Non-negative firmware version returned by discovery (default: 145)
- `--ble-mac MAC` - BLE MAC address (unique per device)
- `--wifi-mac MAC` - WiFi MAC address
- `--soc PERCENT` - Initial battery SOC percentage (default: 50)
- `--pv-channels` - Optional `power:voltage:current` list (up to 4 channels). Values are the channel's **peak (STC) physical watts**; the mock scales them by the solar curve and encodes channel 1 according to the profile
- `--house-pv-wp WATTS` - Rooftop array peak power for the simulated dwelling (default: 3500, or 0 when `--pv-channels` is given). `0` disables rooftop solar
- `--phases {1,3}` - Phases the home is supplied on. The Venus is single-phase either way; on `3` it only offsets phase A, so `EM.GetStatus` shows one phase exporting while the others import (default: 1)
- `--no-simulate` - Disable dynamic simulation (static values only)
- `--quiet` - Do not log a line per handled request (dropped datagrams are still summarised)
- `--status-interval SECONDS` - Seconds between status lines (default: 30; `0` disables them)

### Examples

```bash
# Rev 3.1 Venus E (GetMode CT zeros, omitted bat_power, SYS/UPS)
python -m mock_device --device "VenusE 3.0" --ver 150 --soc 52

# Legacy Venus E (default ver 145): no SYS/UPS
python -m mock_device --soc 30

# Venus A firmware 148 or older: solar energy in Wh, channel-1 deciwatts, no SYS/UPS
python -m mock_device --device VenusA --ver 148

# Venus A firmware 149: scaled solar energy, still deciwatt PV, no SYS/UPS
python -m mock_device --device VenusA --ver 149

# Venus C firmware 153: HMG-50 reporting VenusC; no SYS/UPS; no EM server
python -m mock_device --device VenusC --ver 153

# Venus A firmware 150 / 150.9: scaled solar, deciwatt PV1, SYS + UPS
python -m mock_device --device VenusA --ver 150 --soc 75

# Venus E mini: SYS without the 150 gate, no UPS, slots 0-5
python -m mock_device --device "Venus E mini" --ver 145

# Static mode (no simulation)
python -m mock_device --no-simulate
```

### With Docker Compose (devcontainer)

The devcontainer runs **these twenty-six** mock devices. `.20`–`.29` are the
issue-log / custom-port set. `.30`–`.46` are the remaining archived Control
images from `tools/firmware/catalog.json`.

| Service | IP | Port | Model | `ver` | Profile | PV encoding | Expected capabilities |
|---------|-----|------|-------|-------|---------|-------------|------------------------|
| mock-marstek | 172.28.0.20 | 30000 | VenusE 3.0 | 145 | Legacy | n/a (no PV) | No SYS, no UPS; solar/grid Wh. Stands in for Venus E **144/147/148** issue logs. |
| mock-marstek-2 | 172.28.0.25 | 30000 | VenusE 3.0 | 150 | Rev 3.1 | n/a (no PV) | SYS + UPS + EM energy; GetMode CT keys are zeros (LAN capture) |
| mock-marstek-3 | 172.28.0.22 | 30001 | VenusA | 148 | 148 or older | Channel 1 **deciwatt**, others watts; solar Wh | PV yes; no SYS, no UPS. Stands in for Venus A **147** (#11) and **148.3** ([#57](https://github.com/taurgis/has-marstek-local-api/issues/57)); GetMode CT keys are zeros |
| mock-marstek-4 | 172.28.0.23 | 30002 | VenusD | 145 | Legacy | Channel 1 **deciwatt**, others watts; solar Wh | PV yes; no SYS, no UPS |
| mock-marstek-5 | 172.28.0.24 | 30003 | VenusA | 149 | Venus A 149 | Channel 1 **deciwatt**, others watts; solar 0.01 kWh | PV yes; no SYS, no UPS ([#35](https://github.com/taurgis/has-marstek-local-api/issues/35)) |
| mock-marstek-6 | 172.28.0.26 | 30000 | VenusC | 153 | HMG-50 | n/a (no PV) | No SYS/UPS; no EM.GetStatus server; GetDevice omits result MACs ([#60](https://github.com/taurgis/has-marstek-local-api/issues/60)); `bat_power` present; `Wifi.SetConfig` accepted; reset-prone |
| mock-marstek-7 | 172.28.0.27 | 30004 | VenusA | 150 | Rev 3.1 | Channel 1 **deciwatt**, others watts; solar 0.01 kWh | PV yes; SYS + UPS ([#57](https://github.com/taurgis/has-marstek-local-api/issues/57) firmware **150.9**) |
| mock-marstek-8 | 172.28.0.28 | 30000 | Venus E mini | 145 | E mini | n/a (no PV) | SYS without the 150 gate; no UPS; slots 0–5 |
| mock-marstek-9 | 172.28.0.29 | 30000 | VenusE (HMG-50 / E2.0) | 153 | Unsupported E2 | n/a (no PV) | GetDevice `device=VenusE`, `src VenusE-%s`, result MACs present; `bat_power` in ES.GetStatus; no `EM.GetStatus` until Control 155; integration must reject, not add as Venus E 3.x |

Archived Control extras (default UDP 30000):

| Service | IP | Model | `ver` | What it proves |
|---------|-----|-------|-------|----------------|
| mock-marstek-10–14 | 172.28.0.30–.34 | VenusE 3.0 | 144, 147, 1476, 148, 149 | Distinct VNSE3-0 Control images (1476 is app 147.6) |
| mock-marstek-15 | 172.28.0.35 | VenusA | 1487 | Dotted 148.7; no SYS |
| mock-marstek-16 | 172.28.0.36 | VenusE Pro | 1508 | VEPRO-0 banners; unknown family, not Venus A |
| mock-marstek-17 | 172.28.0.37 | VenusA | 1509 | App 150.9 as Open API `ver` 1509 |
| mock-marstek-18–21 | 172.28.0.38–.41 | VenusD | 147, 149, 1492, 150 | VNSD-0 Control matrix |
| mock-marstek-22–23 | 172.28.0.42–.43 | VenusC | 155, 156 | EM server from 155; no `bat_power`; Open API stable at 156 |
| mock-marstek-24–25 | 172.28.0.44–.45 | VenusE | 155, 156 | Unsupported HMG-50 later Controls |
| mock-marstek-26 | 172.28.0.46 | Venus E mini | 150 | E mini with UPS + ten-slot exception still 0–5 |

Venus A @ 148 vs Venus A @ 149 is the unscaled-Wh versus 0.01 kWh solar-energy pair (#35). Both encode channel-1 PV as deciwatts, and firmware 150 / 150.9 does too (#57). Venus D @ 145 remains the other PV family on legacy encoding. Venus A @ 150 is the SYS/UPS PV device; do not replace the 148/149 pair with it.

> **Note:** MAC addresses use the locally-administered range (`02:xx:xx:xx:xx:xx`) with memorable patterns (`deadbeef`, `cafebabe`) to clearly distinguish mock devices from real hardware.

To add devices in Home Assistant:
1. Go to Settings → Devices & Services
2. Add Integration → Marstek
3. Use manual entry with one of these IP/port pairs:
    - `172.28.0.20:30000`
    - `172.28.0.25:30000`
    - `172.28.0.22:30001`
    - `172.28.0.23:30002`
    - `172.28.0.24:30003`
    - `172.28.0.26:30000`
    - `172.28.0.27:30004`
    - `172.28.0.28:30000`
    - `172.28.0.29:30000` (Venus E 2.0 / HMG-50; expect unsupported, do not add)
    - `172.28.0.30`–`172.28.0.46` archived Control variants (see table above)

## Simulation Behavior

The mock is meant to sit on a Home Assistant dashboard next to a real Venus
without looking out of place, so the numbers it emits obey the same physics
the hardware does.

### The simulated home

Every mock owns a dwelling. Its gross load is a smooth two-peak residential
curve interpolated between hourly set points, plus a fridge duty cycle, plus
discrete cooking and appliance events, plus a small continuous noise term.
A day comes to roughly 10 kWh, which is where ODYSSEE-MURE puts the average
EU household. The curve moves over minutes, not milliseconds: redrawing a
wide random range on every read turns a P1 trace into white noise and buries
every signal worth looking at.

Unless `--house-pv-wp 0` is given, the home also has a rooftop array. Its
output follows solar declination and hour angle for the configured latitude,
scaled by a slowly drifting cloud factor, so it is dark at midnight, higher
at noon than at seven, and higher in June than in December. Without panels
the meter never reads negative, Auto mode can only ever discharge, and the
mock drains to its reserve on day one and stays there.

`--pv-channels` on a Venus A or D drives the device's own MPPT inputs from
the same sky. The configured values are read as the channel's peak figures;
voltage sags mildly with irradiance while current tracks it, so `P = V * I`
holds at every point on the curve.

### Power sign conventions

| Field | Meaning |
|-------|---------|
| `ES.GetStatus.ongrid_power` | The **inverter's own AC port**. Positive when the unit pushes power out. Equals `pv_power + battery_power`. |
| `ES.GetStatus.bat_power` | Positive = charging (Open API convention) |
| `EM.GetStatus.total_power` | The **P1 / CT meter**. Positive when the house imports. |

These are two different measurements and the mock keeps them apart. The
integration derives battery power as `pv_power - ongrid_power`, so reporting
the meter reading in `ongrid_power` makes Home Assistant show a battery
sitting at 0 W while the mock's SOC visibly drains. The Venus A capture in
[#11](https://github.com/taurgis/has-marstek-local-api/issues/11) shows the
two disagreeing on a real device (`ongrid_power: 318` against
`total_power: -16`).

### Auto Mode

Auto is a closed loop on the meter, not a lookup of the house load. Each
cycle the controller reads the CT through a first-order lag, ignores any
error inside a 30 W deadband, and moves its AC setpoint by a fraction of
what is left. The inverter then slews toward that setpoint at a bounded
ramp rate instead of stepping.

The consequences are the ones a real installation shows:

- A kettle switching on is visible on P1 for a second or two before the
  battery covers it.
- A rooftop surplus pushes the loop negative, so the battery charges without
  any special case in the code.
- A surplus or a load larger than the inverter can follow keeps flowing
  through the meter.
- With no CT connected the unit idles rather than guessing at the load.
- A settled unit hovers within a few tens of watts of zero. It does not pin
  the meter to exactly 0 W, and neither does the hardware.

### AI Mode
Like Auto, but tops up from the grid in the cheap overnight window and holds
a reserve back for the evening peak.

### Passive Mode
Uses configured power for set duration, then reverts to Auto. The inverter
still ramps to the commanded value.

### Manual Mode
Follows configured schedule slots with day/time/power settings.

### UPS Mode
Charges toward a full pack and holds it there so the unit can carry an
outage. Accepted only when the firmware profile reports `supports_ups`
(typically `ver >= 150`). Legacy mocks reject the write.

### Battery physics

- **Conversion losses**: 0.93 each way, an ~86% AC round trip. Charging
  therefore costs more watt-hours than discharging returns, and the SOC
  reflects that.
- **SOC limits**: no discharge below 5%, no charge above 100%, tapering
  below 10% and above 90%, and a 10% reserve Auto mode holds back for the
  house.
- **Standby draw**: the unit's own auxiliaries appear on the meter, because
  that is where a real installation sees them.
- **Temperature**: a lumped thermal model heats the pack with its conversion
  losses and cools it toward a diurnal ambient. Charging is refused outside
  the LFP window, which the mock reports through `Bat.GetStatus.charg_flag`.

### Pack size and rated power

`--device` selects both, so each family reports its own hardware:

| Family | Capacity | Rated AC power |
|--------|----------|----------------|
| Venus A | 2080 Wh | 1500 W |
| Venus C | 2560 Wh | 2500 W |
| Venus D | 2560 Wh | 2200 W |
| Venus E 3.0 | 5120 Wh | 2500 W |
| Venus E mini | 2010 Wh | 1500 W |

An unrecognised model falls back to the Venus E shape. The powers mirror the
integration's own per-family ceilings; they are duplicated in
`tools/mock_device/const.py` rather than imported because the mock container
has no Home Assistant, and a test keeps the two copies in agreement.

## Supported API Methods

| Method | Description |
|--------|-------------|
| `Marstek.GetDevice` | Device info (discovery), including explicit `ver` |
| `ES.GetStatus` | Battery status (SOC, power, energy encoded per profile) |
| `ES.GetMode` | Current operating mode (Rev 3.1 also encodes EM energy fallbacks) |
| `ES.SetMode` | Change mode; UPS rejected on legacy |
| `PV.GetStatus` | PV panel readings (Venus A/D); power encoded per profile |
| `Wifi.GetStatus` | WiFi signal and network info |
| `Wifi.SetConfig` | HMG-50 only (`ssid` required); Method not found on VNSE3-0 / VNSA-0 / VNSD-0 |
| `EM.GetStatus` | CT clamp / energy meter (lifetime energy encoded on Rev 3.1). HMG-50 / Venus C 153 replies `-32601`. |
| `Bat.GetStatus` | Battery temperature and flags |
| `DOD.SET` / `Ble.Adv` / `Led.Ctrl` | SYS writes on capable firmware; Method not found on legacy |
| `Set.Ver` / `Reset.Factory` | Recv-list firmware (1487 and 149+); not exposed in Home Assistant |
| Anything else | JSON-RPC `-32601 Method not found` (Control unknown-method path) |

## Testing Discovery

```bash
# Test broadcast discovery
python3 /workspaces/ha_marstek/tools/debug_udp_discovery.py --verbose

# Query specific device
python3 /workspaces/ha_marstek/tools/query_device.py 172.28.0.20
```

## Console Output

Status updates every `--status-interval` seconds (default 30):
```
[STATUS] SOC: 45% | Batt: 523W | 🏠 650W | ⚖️ Balanced | Mode: Auto | 🔋 Discharging
```

One line per handled request:
```
[14:32:05] 172.28.0.10:54321 ES.GetMode id=1 (wire 1) -> replied
```

Datagrams the mock cannot answer are summarised rather than printed
individually, at most one line per `DROP_LOG_INTERVAL` seconds:
```
[14:32:07] Dropped datagram from 172.28.0.10:30000: not a request (no method) (412 since the last summary)
```

A datagram carrying `result` or `error` instead of `method` is a *reply*, and
the mock never answers one. Two sockets sharing a UDP port would otherwise
answer each other's answers, spinning at CPU speed and filling the disk with
log output. Containers additionally cap their logs (see
`.devcontainer/docker-compose.yml`), which Docker leaves unbounded by default.

## Programmatic Usage

```python
from mock_device import MockMarstekDevice, BatterySimulator

# Create device with custom config
device = MockMarstekDevice(
    port=30000,
    device_config={"ble_mac": "custom_mac"},
    initial_soc=75,
)
device.start()

# Or use simulators directly for testing
sim = BatterySimulator(initial_soc=50, device_type="VenusA")
sim.start()
state = sim.get_state()
print(f"SOC: {state['soc']}%")
```

To ask "the P1 meter reads X, what does the battery do?", pin the home and
advance the loop yourself instead of sleeping — regulation, the CT lag and
the inverter ramp all need simulated seconds to converge:

```python
sim = BatterySimulator(initial_soc=60)
sim.set_house_load(1800)  # gross appliance load in watts
sim.set_house_pv(0)  # no rooftop production
sim.settle(60.0)  # advance a minute of simulation

print(sim.actual_power)  # ~1800 W discharge (+ standby)
print(sim.grid_power)  # near zero: the meter the loop is chasing
print(sim.ongrid_power)  # the inverter port the API reports

sim.set_house_load(None)  # hand the house back to the simulator
```
