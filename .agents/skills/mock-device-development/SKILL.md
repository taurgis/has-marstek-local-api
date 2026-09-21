---
name: mock-device-development
description: Guide for developing and maintaining mock Marstek battery devices for local testing and devcontainer environments, including the closed-loop Auto-mode regulation, simulated home, and battery physics that keep emitted values realistic
---

# Mock Marstek Device Development

This skill covers creating, configuring, and maintaining mock Marstek devices for testing the Home Assistant integration without physical hardware.

## Overview

Mock devices simulate real Marstek batteries (Venus A/C/D/E 3.0) using UDP (default port 30000, user-configurable). They implement the same Open API protocol as real devices, enabling full integration testing.

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
    ├── solar.py          # SolarSimulator + PVChannelSimulator (sun curve)
    └── wifi.py           # WiFiSimulator (RSSI variations)
```

## Key Files

| File | Purpose |
|------|---------|
| `simulators/battery.py` | Core simulation: regulation loop, SOC, power, temperature |
| `simulators/household.py` | Household consumption patterns for Auto mode |
| `simulators/solar.py` | Rooftop array and device MPPT channels |
| `device.py` | UDP server, request handling |
| `handlers.py` | API method responses (DRY: one handler per method) |
| `const.py` | All constants in one place |
| `.devcontainer/docker-compose.yml` | Multi-device orchestration |

## Multi-Battery Setup

The devcontainer supports multiple mock devices:

```yaml
# .devcontainer/docker-compose.yml
mock-marstek:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.20", "--ver", "145"]

mock-marstek-2:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.25", "--ver", "150", "--ble-mac", "02deadbeef02"]

mock-marstek-3:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.22", "--port", "30001", "--device", "VenusA", "--ver", "148", "--ble-mac", "02deadbeef03"]

mock-marstek-5:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.24", "--port", "30003", "--device", "VenusA", "--ver", "149", "--ble-mac", "02deadbeef05"]

mock-marstek-6:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.26", "--device", "VenusC", "--ver", "153", "--ble-mac", "02deadbeef06"]

mock-marstek-7:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.27", "--port", "30004", "--device", "VenusA", "--ver", "150", "--ble-mac", "02deadbeef07"]

mock-marstek-8:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.28", "--device", "Venus E mini", "--ver", "145", "--ble-mac", "02deadbeef08"]

mock-marstek-9:
  command: ["python", "-m", "mock_device", "--ip", "172.28.0.29", "--device", "VenusE", "--ver", "153", "--ble-mac", "02deadbeef09"]

Archived Control extras occupy `172.28.0.30`–`172.28.0.46` (see `tools/mock_device/README.md` and `tools/firmware/catalog.json`).
```

Venus A firmware **148** (solar Wh, channel-1 deciwatts; same encodings as 1.0.0) and **149** (solar 0.01 kWh, channel-1 still deciwatts) must both be present so both energy encodings can be tested. Do not collapse them onto a single Venus A 150 mock. Keep a separate Venus A **150** mock for SYS/UPS plus PV1 deciwatts (firmware **150.9**, issue #57). PV1 stays deciwatts through 150.9; that is separate from the #35 solar-energy scale. Venus E mini is not Venus E: it needs its own mock for SYS-without-150 and slots 0–5.

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
- Owns `HouseholdSimulator`, `SolarSimulator`, `PVChannelSimulator` and `WiFiSimulator`
- Runs a background thread updating state every second
- Resolves capacity and rated power from `--device` via `const.device_spec()`
- Manages the regulation loop, SOC, temperature and CT state

Each update runs the same ordered pipeline; keep new work inside it rather
than bolting state onto `get_state()`:

```
_refresh_inputs -> _advance_power -> _settle_flows
  -> _update_soc -> _update_energy_stats -> _update_temperature
```

### Power sign conventions (get these wrong and HA reads zero)

| Field | Meaning |
|-------|---------|
| `ongrid_power` | The inverter's own AC port. `pv_power + battery_power`, positive when exporting. |
| `bat_power` (API) | Positive = charging |
| `EM.GetStatus.total_power` | The P1 / CT meter, positive when the house imports |

`ongrid_power` and the meter are different measurements. The integration
derives battery power as `pv_power - ongrid_power`, so putting the meter
reading in `ongrid_power` makes Home Assistant show 0 W while the mock's SOC
drains. The Venus A capture in issue #11 has them disagreeing on real
hardware (`ongrid_power: 318` against `total_power: -16`).

### Auto mode is a closed loop

Auto does not look up the house load. It reads the CT through a first-order
lag, ignores errors inside `AUTO_DEADBAND_W`, moves the AC setpoint by
`AUTO_LOOP_GAIN` of the remainder, and lets the inverter slew there at
`POWER_RAMP_W_PER_SECOND`. A settled unit therefore hovers within a few tens
of watts of zero rather than pinning the meter, a load step is briefly
visible on P1, and a PV surplus turns into charging with no special case.

Anything that changes regulation, ramping or the CT path needs simulated
seconds before it can be observed. Use `sim.settle(seconds)`; do not
`time.sleep()` and do not assert on the value immediately after `set_mode()`.

### HouseholdSimulator

Generates realistic consumption in `simulators/household.py`:
- A smooth two-peak diurnal curve interpolated between hourly set points
- A fridge duty cycle, plus random appliance and cooking events
- Micro-fluctuations for realism

Roughly 10 kWh/day, near the EU average. Event probabilities are **per
30-second check**, so a day holds ~2880 rolls: a chance that looks small
still fires several times. Never resample a wide random range on every call
— that turns the P1 trace into hundreds of watts of white noise.

### SolarSimulator

Drives both the dwelling's rooftop array (`--house-pv-wp`) and a Venus A/D's
own MPPT channels (`--pv-channels`) from one clear-sky curve built from solar
declination and hour angle, scaled by a drifting cloud factor. Without it the
meter never reads negative, Auto can only discharge, and the mock parks at
its reserve forever.

### Handlers (DRY Pattern)

Each API method has a dedicated handler in `handlers.py`:
```python
def handle_es_get_status(request_id, src, state):
    return {"id": request_id, "src": src, "result": {...}}
```

This keeps response logic separate from device/networking code.
Recv-list and field-table quirks from archived Control images live in
`firmware_quirks.py` (HMG-50 `Wifi.SetConfig`, 153 `bat_power`, `Set.Ver`
from 1487/149+). Do not copy those into Home Assistant entities.

## CLI Reference

```bash
python -m mock_device [OPTIONS]

--port PORT        UDP port (default: 30000)
--ip IP            Reported IP (must match container IP)
--device TYPE      Device type (default: "VenusE 3.0")
--ver N            Firmware integer selecting the profile (default: 145)
--ble-mac MAC      BLE MAC, 12 hex chars
--wifi-mac MAC     WiFi MAC, 12 hex chars
--soc PERCENT      Initial SOC 0-100 (default: 50)
--pv-channels SPEC VenusA/D PV channels, peak 'power:voltage:current' (max 4)
--house-pv-wp W    Rooftop array peak watts (default 3500; 0 with --pv-channels)
--phases {1,3}     Phases the home is supplied on (default 1)
--no-simulate      Disable dynamic simulation
--state-dir DIR    Where persisted mock state is stored
--reset-state      Discard this device's persisted state on start
--quiet            Drop the per-request log line (drops still summarised)
--status-interval  Seconds between status lines (default: 30; 0 disables)
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
| `BATTERY_CAPACITY_WH` | 5120 | Fallback capacity for unknown models |
| `CHARGE_EFFICIENCY` / `DISCHARGE_EFFICIENCY` | 0.93 | ~86% AC round trip |
| `POWER_RAMP_W_PER_SECOND` | 2000 | Inverter slew limit |
| `AUTO_DEADBAND_W` | 30 | Errors the loop ignores |
| `AUTO_LOOP_GAIN` | 0.8 | Fraction of the error corrected per cycle |
| `CT_MEASUREMENT_LAG_SECONDS` | 1.5 | First-order lag on the CT reading |
| `DEFAULT_HOUSE_PV_WP` | 3500 | Rooftop array for the simulated home |

`_FAMILY_SPECS` maps each `DeviceFamily` to `(capacity_wh, rated_power_w)`.
It duplicates the integration's `_FAMILY_POWER_LIMITS` because the mock
container has no Home Assistant installed and
`custom_components/marstek/const.py` imports `homeassistant.const`. Keep the
duplicate in step — `tests/test_mock_device/test_device_physics.py` fails if
the mock ever claims more than the integration would command. Anything the
mock imports from `custom_components` must also be COPYed in the Dockerfile.

## When to Modify

- Adding new sensor entities → add to `get_state()` + handler
- Testing multi-battery aggregation → add devices to docker-compose
- Validating mode control → modify `set_mode()` in battery.py
- Reproducing specific states → use CLI flags or modify defaults
- Scripting a "P1 reads X, what does the battery do?" scenario → pin the home
  with `set_house_load()` / `set_house_pv()`, then `settle()`:

```python
sim = BatterySimulator(initial_soc=60, device_type="VenusA")
sim.set_house_load(1800)
sim.set_house_pv(0)
sim.settle(60.0)
assert abs(sim.grid_power) < 120      # the loop converges, not pins
sim.set_house_load(None)              # hand the house back

```

### Realism guardrails

Before changing a simulated value, check it against the hardware:

- Pack size, rated power and efficiency come from `_FAMILY_SPECS` and the
  efficiency constants, never from a literal in a handler.
- A value that cannot change instantly on real hardware must not change
  instantly here: power ramps, temperature has a time constant, the CT lags.
- Prefer deriving a reading from the model over emitting a plausible-looking
  constant. A fixed `--pv-channels` value reporting 320 W at midnight is the
  kind of thing this simulator exists to avoid.
