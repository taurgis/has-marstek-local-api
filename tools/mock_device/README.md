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
- **Dynamic Battery Simulation**: SOC increases/decreases based on power flow
- **Power Fluctuations**: Realistic ±5% variations in power readings
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
| Rev 3.1 | `150` or newer | Solar energy as 0.01 kWh; PV channel power as watts; EM energies as 0.1 Wh | SYS + UPS accepted on families that support them |

Issue [#57](https://github.com/taurgis/has-marstek-local-api/issues/57) is Venus A firmware **148.3** after integration **1.1.0**: PV1 showed 10× too high. Firmware **148 or older** must keep the 1.0.0 encodings (solar Wh and PV1 ÷10). Firmware **149** is the solar-unit case from issue [#35](https://github.com/taurgis/has-marstek-local-api/issues/35): PV energy uses 0.01 kWh even though PV1 stays deciwatts and SYS/UPS stay off. Do not collapse 148 and 149 onto a single Venus A 150 mock.

Venus E mini is a distinct family (`--device "Venus E mini"`). It must not be configured as Venus E if you need the SYS-without-150 and slots 0–5 behavior.

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
- `--pv-channels` - Optional `power:voltage:current` list (up to 4 channels). Values are **physical watts**; the mock encodes channel 1 according to the profile
- `--no-simulate` - Disable dynamic simulation (static values only)

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

# Venus C firmware 153: SYS/UPS, no PV (issue #60 wire shape)
python -m mock_device --device VenusC --ver 153

# Rev 3.1 Venus A: watt PV, SYS, UPS, EM energy
python -m mock_device --device VenusA --ver 150 --soc 75

# Static mode (no simulation)
python -m mock_device --no-simulate
```

### With Docker Compose (devcontainer)

The devcontainer runs **these six** mock devices.

| Service | IP | Port | Model | `ver` | Profile | PV encoding | Expected capabilities |
|---------|-----|------|-------|-------|---------|-------------|------------------------|
| mock-marstek | 172.28.0.20 | 30000 | VenusE 3.0 | 145 | Legacy | n/a (no PV) | No SYS, no UPS; solar/grid Wh |
| mock-marstek-2 | 172.28.0.25 | 30000 | VenusE 3.0 | 150 | Rev 3.1 | n/a (no PV) | SYS + UPS + EM energy; GetMode CT keys are zeros (LAN capture) |
| mock-marstek-3 | 172.28.0.22 | 30001 | VenusA | 148 | 148 or older | Channel 1 **deciwatt**, others watts; solar Wh | PV yes; no SYS, no UPS ([#57](https://github.com/taurgis/has-marstek-local-api/issues/57)) |
| mock-marstek-4 | 172.28.0.23 | 30002 | VenusD | 145 | Legacy | Channel 1 **deciwatt**, others watts; solar Wh | PV yes; no SYS, no UPS |
| mock-marstek-5 | 172.28.0.24 | 30003 | VenusA | 149 | Venus A 149 | Channel 1 **deciwatt**, others watts; solar 0.01 kWh | PV yes; no SYS, no UPS ([#35](https://github.com/taurgis/has-marstek-local-api/issues/35)) |
| mock-marstek-6 | 172.28.0.26 | 30000 | VenusC | 153 | Rev 3.1 | n/a (no PV) | SYS + UPS; no PV ([#60](https://github.com/taurgis/has-marstek-local-api/issues/60)) |

Venus A @ 148 vs Venus A @ 149 is the unscaled-Wh versus 0.01 kWh solar-energy pair. Both still encode channel-1 PV as deciwatts (the 1.1.0 watt-PV change is **150+** only). Venus D @ 145 remains the other PV family on legacy encoding. Rev 3.1 watt-PV (`ver >= 150`) is covered by unit tests and `python -m mock_device --device VenusA --ver 150`.

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

## Simulation Behavior

### Auto Mode
Discharges to offset simulated household consumption, keeping grid power near 0.

### AI Mode
Like Auto, but saves energy during low-usage periods for evening peaks.

### Passive Mode
Uses configured power for set duration, then reverts to Auto.

### Manual Mode
Follows configured schedule slots with day/time/power settings.

### UPS Mode
Accepted only when the firmware profile reports `supports_ups` (typically `ver >= 150`). Legacy mocks reject the write.

### SOC Limits
- Cannot discharge below 5% SOC
- Cannot charge above 100% SOC
- Power tapers near limits (below 10%, above 90%)

## Supported API Methods

| Method | Description |
|--------|-------------|
| `Marstek.GetDevice` | Device info (discovery), including explicit `ver` |
| `ES.GetStatus` | Battery status (SOC, power, energy encoded per profile) |
| `ES.GetMode` | Current operating mode (Rev 3.1 also encodes EM energy fallbacks) |
| `ES.SetMode` | Change mode; UPS rejected on legacy |
| `PV.GetStatus` | PV panel readings (Venus A/D); power encoded per profile |
| `Wifi.GetStatus` | WiFi signal and network info |
| `EM.GetStatus` | CT clamp / energy meter (lifetime energy encoded on Rev 3.1) |
| `Bat.GetStatus` | Battery temperature and flags |
| `DOD.SET` / `Ble.Adv` / `Led.Ctrl` | SYS writes on capable firmware; Method not found on legacy |

## Testing Discovery

```bash
# Test broadcast discovery
python3 /workspaces/ha_marstek/tools/debug_udp_discovery.py --verbose

# Query specific device
python3 /workspaces/ha_marstek/tools/query_device.py 172.28.0.20
```

## Console Output

Status updates every 5 seconds:
```
[STATUS] SOC: 45% | Batt: 523W | 🏠 650W | ⚖️ Balanced | Mode: Auto | 🔋 Discharging
```

Request logging:
```
[14:32:05] Request from 172.28.0.10:54321
   Method: ES.GetMode
   ID: 1
   -> Sent response: ES.GetMode
```

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
sim = BatterySimulator(initial_soc=50)
sim.start()
state = sim.get_state()
print(f"SOC: {state['soc']}%")
```
