# Mock Marstek Device

A mock Marstek device for testing the Home Assistant integration without a real device. Includes realistic battery simulation with dynamic SOC changes, power fluctuations, and mode transitions.

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

- **Dynamic Battery Simulation**: SOC increases/decreases based on power flow
- **Power Fluctuations**: Realistic ±5% variations in power readings
- **Mode Support**: Auto, AI, Manual, and Passive modes with proper behavior
- **Passive Mode Timer**: Automatic expiration after configured duration
- **Manual Schedules**: Supports schedule slots with day/time configuration
- **Household Simulation**: Realistic time-of-day consumption patterns
- **Status Display**: Periodic console output showing current battery state

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
- `--device TYPE` - Device type (default: "VenusE 3.0")
- `--ble-mac MAC` - BLE MAC address (unique per device)
- `--wifi-mac MAC` - WiFi MAC address
- `--soc PERCENT` - Initial battery SOC percentage (default: 50)
- `--no-simulate` - Disable dynamic simulation (static values only)

### Examples

```bash
# Start with 30% battery
python -m mock_device --soc 30

# Start with custom MAC (for multi-device testing)
python -m mock_device --ble-mac 009b08a5bb40 --soc 75

# Static mode (no simulation)
python -m mock_device --no-simulate
```

### With Docker Compose (devcontainer)

The devcontainer runs 3 mock devices with unique, clearly-fake MAC addresses:

| Device | IP | BLE MAC | WiFi MAC | Initial SOC |
|--------|-----|---------|----------|-------------|
| mock-marstek | 172.28.0.20 | 02deadbeef01 | 02cafebabe01 | 50% |
| mock-marstek-2 | 172.28.0.21 | 02deadbeef02 | 02cafebabe02 | 75% |
| mock-marstek-3 | 172.28.0.22 | 02deadbeef03 | 02cafebabe03 | 30% |

> **Note:** MAC addresses use the locally-administered range (`02:xx:xx:xx:xx:xx`) with memorable patterns (`deadbeef`, `cafebabe`) to clearly distinguish mock devices from real hardware.

To add devices in Home Assistant:
1. Go to Settings → Devices & Services
2. Add Integration → Marstek
3. Use manual entry with IP: `172.28.0.20`, `172.28.0.21`, or `172.28.0.22`

## Simulation Behavior

### Auto Mode
Discharges to offset simulated household consumption, keeping grid power near 0.

### AI Mode
Like Auto, but saves energy during low-usage periods for evening peaks.

### Passive Mode
Uses configured power for set duration, then reverts to Auto.

### Manual Mode
Follows configured schedule slots with day/time/power settings.

### SOC Limits
- Cannot discharge below 5% SOC
- Cannot charge above 100% SOC
- Power tapers near limits (below 10%, above 90%)

## Supported API Methods

| Method | Description |
|--------|-------------|
| `Marstek.GetDevice` | Device info (discovery) |
| `ES.GetStatus` | Battery status (SOC, power, energy) |
| `ES.GetMode` | Current operating mode |
| `ES.SetMode` | Change mode with config |
| `PV.GetStatus` | PV panel readings |
| `Wifi.GetStatus` | WiFi signal and network info |
| `EM.GetStatus` | CT clamp / energy meter |
| `Bat.GetStatus` | Battery temperature and flags |

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
