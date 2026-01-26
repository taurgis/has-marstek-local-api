---
name: mock-device-development
description: Guide for developing and maintaining mock Marstek battery devices for local testing and devcontainer environments
---

# Mock Marstek Device Development

This skill covers creating, configuring, and maintaining mock Marstek devices for testing the Home Assistant integration without physical hardware.

## Overview

Mock devices simulate real Marstek batteries (Venus A/D/E 3.0) using UDP on port 30000. They implement the same Open API protocol as real devices, enabling full integration testing.

## Package Structure

```
tools/mock_device/
├── __init__.py           # Package exports
├── __main__.py           # CLI entry point (python -m mock_device)
├── const.py              # Constants (modes, SOC limits, defaults)
├── device.py             # MockMarstekDevice UDP server
├── handlers.py           # API method response handlers
├── utils.py              # Utility functions (get_local_ip)
├── mock_marstek.py       # Backwards compatibility shim
├── Dockerfile            # Container image
└── simulators/
    ├── __init__.py       # Simulator exports
    ├── battery.py        # BatterySimulator (main simulation logic)
    ├── household.py      # HouseholdSimulator (power consumption)
    └── wifi.py           # WiFiSimulator (RSSI variations)
```

## Key Files

| File | Purpose |
|------|---------|
| `simulators/battery.py` | Core simulation: SOC, power, modes, temperature |
| `simulators/household.py` | Household consumption patterns for Auto mode |
| `device.py` | UDP server, request handling |
| `handlers.py` | API method responses (DRY: one handler per method) |
| `const.py` | All constants in one place |
| `.devcontainer/docker-compose.yml` | Multi-device orchestration |

## Multi-Battery Setup

The devcontainer supports multiple mock devices:

```yaml
# .devcontainer/docker-compose.yml
mock-marstek:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.20"]

mock-marstek-2:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.21", "--ble-mac", "009b08a5bb40", "--soc", "75"]

mock-marstek-3:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.22", "--ble-mac", "009b08a5cc41", "--soc", "30"]
```

### Adding a New Mock Device

1. Add service to `docker-compose.yml`:
   - Unique container name
   - Unique IP in `172.28.0.0/16` subnet
   - Unique BLE MAC (used as device unique_id)

2. Required CLI flags:
   - `--ip` - Must match container's `ipv4_address`
   - `--ble-mac` - Unique 12 hex chars (no colons)

## Architecture

### BatterySimulator

Central simulation coordinator in `simulators/battery.py`:
- Owns `HouseholdSimulator` and `WiFiSimulator` instances
- Runs background thread updating state every second
- Calculates power based on mode and consumption
- Manages SOC changes, temperature, CT state

### HouseholdSimulator

Generates realistic consumption in `simulators/household.py`:
- Time-of-day patterns (morning/evening peaks)
- Random appliance events (cooking, washing, etc.)
- Micro-fluctuations for realism

### Handlers (DRY Pattern)

Each API method has a dedicated handler in `handlers.py`:
```python
def handle_es_get_status(request_id, src, state):
    return {"id": request_id, "src": src, "result": {...}}
```

This keeps response logic separate from device/networking code.

## CLI Reference

```bash
python -m mock_device [OPTIONS]

--port PORT        UDP port (default: 30000)
--ip IP            Reported IP (must match container IP)
--device TYPE      Device type (default: "VenusE 3.0")
--ble-mac MAC      BLE MAC, 12 hex chars
--wifi-mac MAC     WiFi MAC, 12 hex chars
--soc PERCENT      Initial SOC 0-100 (default: 50)
--no-simulate      Disable dynamic simulation
```

## Adding New API Methods

1. Add handler function in `handlers.py`:
```python
def handle_new_method(request_id: int, src: str, state: dict) -> dict:
    return {"id": request_id, "src": src, "result": {...}}
```

2. Register in `device.py` `_build_response()`:
```python
elif method == "NewMethod.GetData":
    return handle_new_method(request_id, src, state)
```

3. If new state needed, add to `BatterySimulator.get_state()`.

## Adding New Simulation Features

1. For new simulators, create `simulators/new_sim.py`
2. Add to `simulators/__init__.py` exports
3. Instantiate in `BatterySimulator.__init__`
4. Include values in `get_state()` return dict

## Testing

```bash
# Run directly
cd tools && python -m mock_device --soc 30

# In devcontainer
docker logs marstek-mock-device -f

# Query device
python3 tools/query_device.py 172.28.0.20
```

## Constants Reference

Key constants in `const.py`:

| Constant | Value | Purpose |
|----------|-------|---------|
| `SOC_MIN_DISCHARGE` | 5 | Stop discharging below this |
| `SOC_RESERVE` | 10 | Auto mode reserve |
| `SOC_TAPER_DISCHARGE` | 10 | Start tapering discharge |
| `SOC_TAPER_CHARGE` | 90 | Start tapering charge |
| `BATTERY_CAPACITY_WH` | 5120 | Default capacity |

## When to Modify

- Adding new sensor entities → add to `get_state()` + handler
- Testing multi-battery aggregation → add devices to docker-compose
- Validating mode control → modify `set_mode()` in battery.py
- Reproducing specific states → use CLI flags or modify defaults
