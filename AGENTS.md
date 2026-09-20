# Agent Protocol: Marstek (Home Assistant Custom Integration)

This repository contains a Home Assistant custom integration for **Marstek energy storage devices** (Venus A/D/E 3.0, etc.) using the **local “Open API” over UDP**.

## What this integration is (and is not)

- **Domain**: `marstek`
- **Scope**: Marstek devices that support **OPEN API** on the local network.
- **Transport**: UDP JSON-RPC-like messages (default port **30000**) as described in `docs/marstek_device_openapi.MD`.
- **Pattern**: `local_polling` using a single `DataUpdateCoordinator` per device + a background `Scanner` to detect IP changes.
- Quality Scale: Silver (aiming for Gold with >95% coverage; tracked in `quality_scale.yaml`)

Important compatibility notes:
- The integration is currently **not compatible with Venus E2.0** devices (see `README.md`).

## Research before implementation

For any question or request, always run the `runSubagent` Official Docs Researcher (as defined in `.github/agents/official-docs-researcher.agent.md`) to do online research before starting the implementation. Summarize the findings using official sources only, include a direct link for every key claim, and keep those links or citations in the task notes. If the vendor, version, or goal is ambiguous, ask clarifying questions before proceeding. 

Note: When asked about Marstek information, that exists in our local API documentation in `docs/marstek_device_openapi.MD` as there are no online resources for that.

Note: If you have already done research via the Official Docs Researcher, you do not have to run it again.
Note: For release management you do not have to do Online Resarch, the "Release Management" skill is enough

## Architectural constraints you must respect

### 1) Keep polling centralized
- Do **not** add per-entity polling or extra network calls from entities.
- All entities must read from `MarstekDataUpdateCoordinator.data`.
- Avoid concurrent requests; Marstek devices can be sensitive to request bursts.
- Coordinator uses **tiered polling** (fast/medium/slow intervals) to reduce device load.

If you add/modify device control:
- Pause coordinator polling while sending control commands (see `custom_components/marstek/device_action.py`) to avoid concurrent UDP traffic.

### 2) Async-only I/O
- Never use blocking I/O.
- All network operations must be awaited and run in the event loop.

### 3) Home Assistant integration contract
- Config/UI-first: no new YAML setup; keep config/reauth/options in `config_flow.py` with selectors where it improves UX.
- Unique IDs must stay stable (BLE-MAC-based) to prevent duplicate entities on IP changes.
- Add user-facing text via `strings.json` → mirrored to `translations/en.json`; prefer descriptive error keys (e.g., `cannot_connect`).
- Entity classes must set `_attr_has_entity_name = True` and expose `device_info` for proper device grouping.
- Use `EntityDescription` dataclasses for declarative sensor/binary_sensor definitions.
- Loading must be async and non-blocking; any sync library work belongs in executor jobs.
- Use `entry.async_on_unload()` for cleanup callbacks.
- Services must be registered idempotently (check `hass.services.has_service()` first).

### 4) Discovery and IP changes are handled by the scanner
- Setup/connection uses the configured IP; it does **not** perform discovery during setup.
- `MarstekScanner` runs periodic broadcast discovery and triggers an integration discovery flow to update the config entry when IP changes.
- Don’t add “fallback discovery” inside coordinator updates; it creates race conditions and extra traffic.

### 5) OPEN API semantics (UDP)
- Devices must have OPEN API enabled in the Marstek app.
- Default UDP port is 30000; the Open API spec recommends using a high port range. The listen port is **user-configurable** per device.
- LAN discovery uses UDP broadcast + `Marstek.GetDevice` (see `docs/marstek_device_openapi.MD`).
- UDP clients are stored in `hass.data[DOMAIN][DATA_UDP_CLIENTS]` **keyed by bind port** and reused across entries that share that Open API port. Mixed custom ports each get their own socket.

## Code map (where to implement changes)

| Concern | File | Notes |
|---|---|---|
| Setup / teardown | `__init__.py` | Creates per-port UDP clients + coordinator; starts `MarstekScanner`; forwards platforms; uses `entry.async_on_unload()` |
| UDP client pool | `helpers/udp_clients.py` | One `MarstekUDPClient` per Open API bind port; loopback uses ephemeral |
| Config flow | `config_flow.py` | Broadcast discovery UI, DHCP updates, reauth, reconfigure |
| Options flow | `options_flow.py` | Polling, network and power option sections |
| Polling + error handling | `coordinator.py` | Single source of truth; tiered polling (fast/medium/slow); returns previous data on connectivity issues |
| IP change detection | `scanner.py` | Periodic broadcast discovery (60s); triggers discovery flow to update config entries |
| Firmware profile | `firmware_profile.py` | Family + `ver` → capabilities and wire-to-SI scales |
| Sensors | `sensor.py` | EntityDescription pattern; coordinator-backed; stable unique IDs; `suggested_display_precision` |
| Binary sensors | `binary_sensor.py` | EntityDescription pattern; CT connection status |
| Number | `number.py` | Firmware-gated SYS DOD; RestoreNumber; writes pause polling |
| Entity bases | `entity.py` | `MarstekEntity` (identity/device wiring for every platform) and `MarstekSysEntity` (optimistic number/switch SYS entities) |
| SYS writes | `helpers/sys_write.py` | Builds the write, requires `set_result`, maps errors to translations |
| Switch | `switch.py` | Firmware-gated SYS BLE/LED; RestoreEntity; writes pause polling |
| Select entities | `select.py` | Operating mode selection (Auto/AI/Manual/Passive; UPS when the profile allows it) |
| Services | `services.py` | Idempotent registration; passive mode, manual schedules, data sync |
| Device actions | `device_action.py` | Automation actions using `ES.SetMode` with retries + verification; pauses polling |
| Device info helper | `device_info.py` | Shared `build_device_info()` + identifier utilities |
| Polling pause | `helpers/polling.py` | `polling_paused()` context manager wrapped around every write |
| Write retries | `helpers/command_retry.py`, `helpers/service_retry.py` | One retry loop; the service layer adds the pause and the error mapping |
| Diagnostics | `diagnostics.py` | Config entry diagnostics with redaction |
| Mode configuration | `mode_config.py` | Mode parameter building helpers |
| Text/translations | `strings.json`, `translations/en.json` | Keep in sync; use translation keys in entities |
| Icons | `icons.json` | Icon translations per entity |
| Local API reference | `docs/marstek_device_openapi.MD` | UDP protocol + method list |
| UDP client library | `pymarstek/` | `MarstekUDPClient`, command builder, data parser, validators. Each unique Open API port binds its own socket (devices reply there, not to an ephemeral source port). |
| Request validation | `pymarstek/validators.py` | Validates methods, params, power/time ranges before transmission |
| Poll composition | `pymarstek/device_status.py` | One table of the reads a poll performs, shared by the serial and parallel paths |
| Reply routing | `pymarstek/response_router.py` | Pending waiters and the short-lived cache, keyed `(source_ip, id)` with a bare-id fallback |
| Pacing | `pymarstek/throttle.py` | Per-IP minimum interval, per-device I/O locks, stale-IP pruning |
| Poll gating | `pymarstek/poll_gate.py` | Reference-counted pause/resume plus poll-cycle leases |
| Firmware marks | `pymarstek/openapi_marks.py` | Per-IP reset-prone and retransmit-safe flags learned at runtime |
| Command stats | `pymarstek/command_stats.py` | Per-method, per-IP outcome counters behind the diagnostic sensors |
| Device identity | `pymarstek/device_info.py` | `Marstek.GetDevice` parsing and MAC extraction |
| Energy guards | `pymarstek/energy_guard.py` | Rejects implausible lifetime-energy jumps from firmware glitches |

## Platforms & Entities

| Platform | Entities |
|----------|---------|
| `sensor` | Battery SoC, power, status; device mode; PV (Venus A/D); on-grid/EM power; EM lifetime energy when reported; WiFi diagnostics; battery details (disabled by default) |
| `binary_sensor` | CT connection status |
| `select` | Operating mode (Auto/AI/Manual/Passive; UPS when the firmware profile allows it) |
| `number` | Depth of discharge (firmware-gated SYS write; restored optimistic state) |
| `switch` | Bluetooth advertising and panel LED (firmware-gated SYS writes; restored optimistic state) |

## Adding or changing sensors

Use the **EntityDescription pattern**:

```python
@dataclass(kw_only=True)
class MarstekSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[MarstekDataUpdateCoordinator, dict, ConfigEntry | None], StateType]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda data: True

SENSORS: tuple[MarstekSensorEntityDescription, ...] = (
    MarstekSensorEntityDescription(
        key="battery_soc",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda coord, _info, _entry: coord.data.get("battery_soc"),
    ),
)
```

Steps:
1. Ensure the value is present in `MarstekDataUpdateCoordinator.data`.
2. Add a `MarstekSensorEntityDescription` to the `SENSORS` tuple in `sensor.py`.
3. Use `exists_fn` to conditionally create entities (avoids permanent unavailable state).
4. Keep unique IDs stable (BLE-MAC + key).
5. Add translation keys in `translations/en.json` and keep `strings.json` in sync.
6. Use `suggested_display_precision` for numeric sensors.

## Polling intervals

Polling is **tiered** to reduce device load:

| Tier | Default | Data |
|------|---------|------|
| Fast | 30s | `ES.GetMode`, `ES.GetStatus`, `EM.GetStatus` (real-time power) |
| Medium | 60s | `PV.GetStatus` (solar data, Venus A/D only) |
| Slow | 300s | `Wifi.GetStatus`, `Bat.GetStatus` (diagnostics) |

Note: `Wifi.GetStatus` and `Bat.GetStatus` are only sent while at least one entity that depends on them is enabled in the entity registry (see `WIFI_STATUS_KEYS`/`BAT_STATUS_KEYS` in `const.py` and `MarstekDataUpdateCoordinator._select_polling_tiers`). The battery detail entities are disabled by default because `Bat.GetStatus` is suspected to trigger device resets on some firmwares (issue #14). When adding an entity backed by one of these calls, add its key to the matching key set in `const.py`.

**Request delay**: 5 seconds between API calls during a polling cycle.

Configurable via options flow. The IP-change scanner runs periodically (10 min backup interval), but uses **event-driven triggers** for fast detection: when the coordinator hits its failure threshold, it triggers an immediate scan (debounced to 30s minimum).

## Services

| Service | Description |
|---------|-------------|
| `marstek.set_passive_mode` | Set passive mode with power and duration |
| `marstek.set_manual_schedule` | Configure single schedule slot |
| `marstek.set_manual_schedules` | Configure multiple schedules via YAML |
| `marstek.clear_manual_schedules` | Clear all schedule slots |
| `marstek.request_data_sync` | Trigger immediate coordinator refresh |

Services are registered **once globally** (idempotent).

## Validation & Security

The `pymarstek/validators.py` module provides a **validation layer** that protects devices from invalid requests:

### What is validated

| Validation | Scope | Limit |
|------------|-------|-------|
| Method names | Only known API methods (`ES.GetStatus`, `ES.SetMode`, etc.) are allowed | |
| Device ID | Must be 0-255 | `MAX_DEVICE_ID = 255` |
| JSON-RPC id | Wire ids stay in the uint16 range firmware echoes back | `MAX_JSON_RPC_ID = 65535` |
| Power values | Prevents obviously invalid commands | `MAX_POWER_VALUE = 5000` |
| Time format | HH:MM pattern enforced | |
| Time range | End must be after start for enabled schedules | |
| Week bitmask | Valid range 0-127 | `MAX_WEEK_SET = 127` |
| Passive duration | Maximum 24 hours | `MAX_PASSIVE_DURATION = 86400` |
| Schedule slots | Venus A/C/D/E: 0-9; Venus E mini: 0-5 | Profile `max_manual_schedule_slot`; validator still lists `MAX_TIME_SLOTS = 10` as the schema ceiling |
| Mode configs | Required fields checked per mode (manual_cfg, passive_cfg) | |
| Wire numbers | Every number in a decoded payload must be a finite JSON number | `json_loads_strict()` |

### Where validation happens

1. **`command_builder.build_command()`** – validates before building JSON
2. **`MarstekUDPClient.send_request()`** – validates before UDP transmission
3. **`MarstekUDPClient.send_broadcast_request()`** – same protection for broadcasts
4. **`json_loads_strict()`** – decodes every inbound datagram (`udp.py`, `discovery.py`)

Invalid requests raise `ValidationError` with a clear message indicating the field.

Inbound payloads are decoded with `json_loads_strict()` instead of `json.loads()`.
Python's decoder accepts the non-standard `NaN`/`Infinity`/`-Infinity` literals and
overflows `1e400` to `inf`, so one glitched datagram could otherwise write a
non-finite value into coordinator state that no later poll overwrites. A datagram
carrying such a number raises `json.JSONDecodeError` and is ignored, exactly like a
syntactically broken one, and the device is retried. `merge_device_status()` drops
non-finite values a second time, for values arithmetic inside the parsers produces.

### Rate limiting

The UDP client enforces a **minimum interval between requests** to the same device IP (`MIN_REQUEST_INTERVAL = 0.3s`) to prevent overwhelming devices.

### Strict validation mode

For development/testing, enable stricter validation that logs warnings for edge cases:

```python
from custom_components.marstek.pymarstek import enable_strict_mode

enable_strict_mode(True)  # Warns on power >90% of max, very short schedules, etc.
```

### Unified constants

Validation limits are defined once in `pymarstek/validators.py` and exported via `pymarstek/__init__.py`. Services and other modules import these constants to avoid duplication:

```python
from .pymarstek import MAX_POWER_VALUE, MAX_PASSIVE_DURATION, MAX_TIME_SLOTS
```

## Quality and style expectations

- Follow Home Assistant coordinator patterns.
- Use EntityDescription dataclasses for declarative entity definitions.
- Keep changes minimal and consistent with existing style.
- Prefer clear user-facing error messages in config flows via `strings.json` / translations.
- Use `suggested_display_precision` for sensor formatting.
- Integration-grade hygiene: avoid per-entity I/O, keep one coordinator per device, debounce refreshes, and ensure options changes trigger reloads.
- Testing/QA: prefer pytest + `pytest-homeassistant-custom-component`; keep manifest versions pinned and metadata valid for HACS/hassfest.

## Verification after changes (MANDATORY)

**After every code modification**, you MUST run linting, type checking, and tests:

```bash
# 1. Linting (code style and common errors)
python3 -m ruff check custom_components/marstek/

# 1b. Formatting (ruff is pinned, so this matches CI exactly)
python3 -m ruff format --check custom_components tests tools scripts

# 2. Type checking (strict mode enabled)
python3 -m mypy --strict custom_components/marstek/

# 3. Run all tests with coverage check (>95% required)
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95

# 3b. The config flow is held to 100% (bronze config-flow-test-coverage).
# Reads the .coverage file step 3 just wrote.
python3 -m coverage report --include="custom_components/marstek/config_flow.py" --fail-under=100

# 4. Static quality gates (blocking in the Code Quality workflow)
python3 -m ruff check custom_components tests tools scripts
python3 -m ruff format --check custom_components tests tools scripts
python3 scripts/check_code_limits.py
python3 -m vulture
jscpd
```

**Do not consider a change complete until every command passes.** If any fails:
1. Fix the lint errors, type errors, test failures, or quality violations
2. Re-run verification
3. Repeat until all pass

This ensures:
- **Code quality**: Ruff catches unused imports, style issues, and common bugs
- **Consistent formatting**: `ruff format` owns layout, so review comments are
  about behavior rather than line breaks. Write the change however you like and
  run `python3 -m ruff format custom_components tests tools scripts` before
  committing; the pinned ruff means your result and CI's agree
- **Type safety**: The codebase uses `--strict` mypy; all functions need proper annotations
- **No regressions**: Tests must pass to confirm existing functionality isn't broken
- **Bounded complexity**: Complexity, dead code, duplication, and the 1000-line
  file ceiling are enforced, not advisory — see
  [Code quality gates](docs/development.md#code-quality-gates)
- **CI alignment**: These are the same checks that run in GitHub Actions

The step 4 tools install from `requirements_quality.txt`, which is deliberately
separate from `requirements_test.txt`: they need no Home Assistant test
harness, so they run on any Python version.

## Testing and QA expectations

- **Quality Scale**: The manifest claims **Platinum**, tracked rule by rule in
  `custom_components/marstek/quality_scale.yaml`. Keep that file honest: a rule
  is `done` only when the thing it asks for exists, otherwise `exempt` with the
  reason. Coverage floors are >95% overall and **100% for `config_flow.py`**.
- Test structure: `tests/` mirrors platforms (`test_config_flow/`, `test_init/`, `test_sensor/`, etc.) with shared fixtures in `tests/conftest.py`. A platform's tests are a package once they outgrow the 1000-line ceiling: themed `test_*.py` modules, module-level helpers in `_helpers.py`, and package-scoped fixtures in that package's `conftest.py`.
- Use `pytest-homeassistant-custom-component` with pinned versions in `requirements_test.txt`; mock UDP I/O—no live devices.
- Cover failures: cannot_connect, invalid_auth/invalid_discovery_info, already_configured, coordinator timeouts, action retries.
- Mark coordinator failures with `UpdateFailed` to surface entity unavailability.
- CI: run hassfest + lint (ruff) + **mypy --strict** + pytest (**coverage >95%**, **100% on `config_flow.py`**) on latest supported Python versions, plus the blocking `Code Quality` workflow (complexity, size limits, dead code, duplication).
- Mock device available in `tools/mock_device/` for local testing.

### Type checking requirements

This repository enforces **strict typing** via `mypy --strict`. When adding or modifying code:

- All functions must have return type annotations
- All function parameters must have type annotations
- Use `from __future__ import annotations` at the top of each file
- Generic types need explicit parameters: `dict[str, Any]`, `list[int]`, `Future[None]`
- Use `cast()` when type narrowing is needed for mocked objects in tests
- EntityDescription dataclasses that inherit from frozen HA classes need `# type: ignore[misc]`

## Local development

- Run Home Assistant dev instance and watch logs for the `marstek` logger.
- Use `tools/mock_device/` to simulate devices without hardware.
- Use `tools/query_device.py` to query real devices for debugging.
- When troubleshooting discovery: ensure devices and HA are on the same LAN segment and UDP port is reachable.
- Devcontainer supports multiple mock devices (see `.devcontainer/docker-compose.yml`).

## Cursor Cloud specific instructions

Cloud Agents must use **Python 3.14.2+**. Home Assistant Core 2026.9 and `pytest-homeassistant-custom-component==0.13.365` declare `requires-python = ">=3.14.2"` ([HA 2026.9.2 pyproject](https://github.com/home-assistant/core/blob/2026.9.2/pyproject.toml)). The default Ubuntu 24.04 `python3` is 3.12 and cannot install the test harness.

`.cursor/install.sh` provisions Python 3.14, a venv at `~/.venvs/ha-marstek` (symlinked onto `PATH` via `/usr/local/bin`), and Docker Engine. `.cursor/start.sh` starts `dockerd` (this VM has no systemd; PID 1 is `tini`) and brings up `.devcontainer/docker-compose.yml`.

After start:

- Home Assistant: `http://127.0.0.1:8123` (onboarding, then username `admin` / password `marstek-dev`)
- Mock devices: `172.28.0.20`–`172.28.0.46` as documented in the Chrome UI testing skill
- Nested Docker uses `fuse-overlayfs` and `iptables-legacy`. If HA cannot ping a mock, `start.sh` already sets `FORWARD ACCEPT`.

Use `python3 -m ruff`, `python3 -m mypy --strict`, and `pytest` from that venv (same commands as in Verification after changes). Drive the HA UI with `.agents/skills/homeassistant-chrome-ui-testing` (`ha_cdp.py`), not screenshot clicks.

Official setup notes: [Cursor Cloud Agent environment](https://cursor.com/docs/cloud-agent/setup), [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [HA Container install](https://www.home-assistant.io/installation/linux#install-home-assistant-container).

## Development Tools

The `tools/` directory contains utilities for testing, debugging, and development. **Use these tools proactively** when working on device communication, debugging issues, or improving mock data.

### `tools/query_device.py` — Query a device directly

Quick diagnostic tool to verify a device is responding on the network.

```bash
python3 tools/query_device.py <IP_ADDRESS> [--port 30000] [--timeout 5.0]
```

**Use when:**
- Verifying a device is reachable before debugging integration issues
- Checking if OPEN API is enabled on a device
- Quick connectivity test without starting Home Assistant

**Example:**
```bash
python3 tools/query_device.py 192.168.0.152
# Output: Device info including version, MAC addresses, etc.
```

### `tools/debug_udp_discovery.py` — Debug broadcast discovery

Comprehensive UDP discovery debug tool that tests broadcast discovery and diagnoses issues like echoed requests, missing responses, and network configuration problems.

```bash
python3 tools/debug_udp_discovery.py [--timeout 10] [--verbose]
```

**Use when:**
- Discovery in config flow isn't finding devices
- Investigating echo/duplicate response issues
- Diagnosing network/VLAN configuration problems
- Verifying broadcast addresses are correct

**Features:**
- Lists all broadcast addresses being used
- Filters echo responses from real device responses
- Provides detailed troubleshooting suggestions
- Shows raw responses in verbose mode

**Example:**
```bash
python3 tools/debug_udp_discovery.py --verbose --timeout 15
```

### `tools/capture_device.py` — Capture real device responses

Captures responses from all API methods on a real device and saves them to JSON files. Optionally updates the mock device with realistic data.

```bash
python3 tools/capture_device.py <IP_ADDRESS> [--port 30000] [--output FILE] [--update-mock]
```

**Use when:**
- Adding support for a new device model (Venus A, Venus D, etc.)
- Updating mock device with realistic response data
- Documenting real device behavior for test fixtures
- Debugging discrepancies between real and mock responses

**Features:**
- Queries all supported API methods with proper delays (10s between requests)
- Saves raw JSON responses for reference
- Generates Python config for mock device
- Can auto-update `mock_device/` with `--update-mock`

**Example:**
```bash
# Capture data and save to file
python3 tools/capture_device.py 192.168.0.152 -o captured_venus_e.json

# Capture and update mock device
python3 tools/capture_device.py 192.168.0.152 --update-mock
```

**Output files:**
- `captured_device_<ip>.json` — Raw API responses
- `captured_device_<ip>.py` — Python config for mock device

### `tools/verify_battery_logic.py` — Verify battery power calculations

Queries `ES.GetStatus` from a real device and verifies the battery power calculation logic matches expected behavior.

```bash
python3 tools/verify_battery_logic.py [IP_ADDRESS]
```

**Use when:**
- Debugging battery charging/discharging status issues
- Verifying power sign conventions (positive = charging/discharging)
- Validating `bat_power` fallback calculation (`pv_power - ongrid_power`)
- Understanding how different modes affect power flow

**Features:**
- Shows raw API response
- Applies integration's power calculation logic
- Performs sanity checks (grid import/export vs. battery status)
- Reports ✅/❌ for logic correctness

**Example:**
```bash
python3 tools/verify_battery_logic.py 192.168.0.152
# Shows: pv_power, ongrid_power, bat_power, calculated battery_status
```

### `tools/mock_device/` — Mock Marstek device for testing

A full mock Marstek device that simulates realistic battery behavior without hardware. Essential for development and testing.

```bash
# Run as module (recommended)
cd tools && python -m mock_device [OPTIONS]

# Or standalone
python3 tools/mock_device/mock_marstek.py [OPTIONS]
```

**Options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--port` | 30000 | UDP port |
| `--ip` | auto | Override reported IP address |
| `--device` | "VenusE 3.0" | Device type string (family matching; Venus E mini is not Venus E) |
| `--ver` | 145 | Firmware integer returned by discovery; selects the shared firmware profile |
| `--ble-mac` | random | BLE MAC address (unique ID) |
| `--wifi-mac` | random | WiFi MAC address |
| `--soc` | 50 | Initial battery SOC percentage |
| `--pv-channels` | none | VenusD PV channels as `power:voltage:current, ...` (up to 4) |
| `--no-simulate` | false | Disable dynamic simulation |
| `--state-dir` | see `utils.py` | Directory holding persisted mock state |
| `--reset-state` | false | Discard this device's persisted state on start |
| `--quiet` | false | Drop the per-request log line (drop summaries still print) |
| `--status-interval` | 30 | Seconds between simulator status lines; `0` disables them |

**Use when:**
- Running tests (pytest uses mock device fixtures)
- Developing without hardware access
- Testing mode transitions and schedule behavior
- Simulating edge cases (low SOC, full battery, etc.)

**Features:**
- **Dynamic battery simulation**: SOC changes based on power flow
- **Power fluctuations**: Realistic ±5% variations
- **All modes**: Auto, AI, Manual, Passive, and firmware-gated UPS
- **Firmware profiles**: `--device` + `--ver` encode wattage/energy and accept or reject SYS/UPS (`Method not found` on legacy)
- **Passive timer**: Auto-expiration after configured duration
- **Manual schedules**: Day/time/power slot configuration
- **Household simulation**: Time-of-day consumption patterns

**Example:**
```bash
# Start with 30% battery for low-SOC testing
python -m mock_device --soc 30

# Start Venus A 148 (solar Wh, PV1 deciwatts) or 149 (solar 0.01 kWh, PV1 still deciwatts)
python -m mock_device --device VenusA --ver 148
python -m mock_device --device VenusA --ver 149
python -m mock_device --device VenusA --ver 150
```

**In devcontainer:** Twenty-six mock devices run automatically. `172.28.0.20`–`.29` are the issue-log / custom-port set: Venus E 145 and 150, Venus A 148/149/150, Venus D 145, Venus C 153 (HMG-50 reporting VenusC: no SYS/UPS, no EM server, omitted GetDevice MACs), Venus E mini 145, and unsupported VenusE 153. `172.28.0.30`–`.46` add the remaining archived Control images (VNSE3-0 144/147/1476/148/149, VNSA-0 1487/1508/1509, VNSD-0 147/149/1492/150, Venus C 155/156, HMG-50 155/156, Venus E mini 150). `VenusE Pro` 1508 is an unknown family. Custom ports 30001/30002/30003/30004 exercise the per-port UDP pool. See `tools/mock_device/README.md`.

### Tool selection guide

| Scenario | Tool to use |
|----------|-------------|
| "Device not found in config flow" | `debug_udp_discovery.py --verbose` |
| "Quick check if device responds" | `query_device.py <IP>` |
| "Battery status shows wrong state" | `verify_battery_logic.py <IP>` |
| "Adding support for new device model" | `capture_device.py <IP> --update-mock` |
| "Running tests without hardware" | `mock_device/` (automatic in pytest) |
| "Need realistic mock data" | `capture_device.py <IP> -o data.json` |
| "Testing low battery behavior" | `mock_device/ --soc 5` |
| "Debugging mode transitions" | `mock_device/` + watch console output |

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for `taurgis/has-marstek-local-api`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: `CONTEXT.md` at the repo root and ADRs under `docs/adr/`. See `docs/agents/domain.md`.

### Install targets

All project skills live in `.agents/skills/` (the Cursor / Forward Nexus project path). Claude Code uses symlinks under `.claude/skills/`. Matt Pocock engineering skills (`/to-spec`, `/wayfinder`, and related) are tracked in `skills-lock.json` and installed with `npx forward-nexus`. Default targets are `cursor` and `claude-code` (see `.forward-nexus.json`).

After cloning: `npx forward-nexus restore --yes`. To attach another agent later: `npx forward-nexus agents add cursor,claude-code`.
