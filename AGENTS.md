# Agent Protocol: Marstek (Home Assistant Custom Integration)

This repository contains a Home Assistant custom integration for **Marstek energy storage devices** (Venus A/D/E 3.0, etc.) using the **local “Open API” over UDP**.

## What this integration is (and is not)

- **Domain**: `marstek`
- **Scope**: Marstek devices that support **OPEN API** on the local network.
- **Transport**: UDP JSON-RPC-like messages (default port **30000**) as described in `docs/marstek_device_openapi.MD`.
- **Pattern**: `local_polling` using a single `DataUpdateCoordinator` per device + a background `Scanner` to detect IP changes.
- **Quality Scale**: Bronze (tracked in `quality_scale.yaml`)

Important compatibility notes:
- The integration is currently **not compatible with Venus E2.0** devices (see `README.md`).

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
- Default UDP port is 30000; the Open API spec recommends using a high port range.
- LAN discovery uses UDP broadcast + `Marstek.GetDevice` (see `docs/marstek_device_openapi.MD`).
- Shared UDP client is stored in `hass.data[DOMAIN][DATA_UDP_CLIENT]` and reused across entries.

## Code map (where to implement changes)

| Concern | File | Notes |
|---|---|---|
| Setup / teardown | `__init__.py` | Creates shared UDP client + coordinator; starts `MarstekScanner`; forwards platforms; uses `entry.async_on_unload()` |
| Config flow | `config_flow.py` | Broadcast discovery UI, DHCP updates, reauth, reconfigure, options flow with sections |
| Polling + error handling | `coordinator.py` | Single source of truth; tiered polling (fast/medium/slow); returns previous data on connectivity issues |
| IP change detection | `scanner.py` | Periodic broadcast discovery (60s); triggers discovery flow to update config entries |
| Sensors | `sensor.py` | EntityDescription pattern; coordinator-backed; stable unique IDs; `suggested_display_precision` |
| Binary sensors | `binary_sensor.py` | EntityDescription pattern; CT connection status |
| Select entities | `select.py` | Operating mode selection (Auto/AI/Manual/Passive) |
| Services | `services.py` | Idempotent registration; passive mode, manual schedules, data sync |
| Device actions | `device_action.py` | Automation actions using `ES.SetMode` with retries + verification; pauses polling |
| Device info helper | `device_info.py` | Shared `build_device_info()` + identifier utilities |
| Diagnostics | `diagnostics.py` | Config entry diagnostics with redaction |
| Mode configuration | `mode_config.py` | Mode parameter building helpers |
| Text/translations | `strings.json`, `translations/en.json` | Keep in sync; use translation keys in entities |
| Icons | `icons.json` | Icon translations per entity |
| Local API reference | `docs/marstek_device_openapi.MD` | UDP protocol + method list |
| UDP client library | `pymarstek/` | `MarstekUDPClient`, command builder, data parser, validators |
| Request validation | `pymarstek/validators.py` | Validates methods, params, power/time ranges before transmission |

## Platforms & Entities

| Platform | Entities |
|----------|---------|
| `sensor` | Battery SoC, power, status, temperature; device mode; PV power/voltage/current (4ch, Venus A/D); grid power (3-phase); WiFi diagnostics |
| `binary_sensor` | CT connection status |
| `select` | Operating mode (Auto/AI/Manual/Passive) |

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
| Power values | Prevents obviously invalid commands | `MAX_POWER_VALUE = 5000` |
| Time format | HH:MM pattern enforced | |
| Time range | End must be after start for enabled schedules | |
| Week bitmask | Valid range 0-127 | `MAX_WEEK_SET = 127` |
| Passive duration | Maximum 24 hours | `MAX_PASSIVE_DURATION = 86400` |
| Schedule slots | 0-9 | `MAX_TIME_SLOTS = 10` |
| Mode configs | Required fields checked per mode (manual_cfg, passive_cfg) | |

### Where validation happens

1. **`command_builder.build_command()`** – validates before building JSON
2. **`MarstekUDPClient.send_request()`** – validates before UDP transmission
3. **`MarstekUDPClient.send_broadcast_request()`** – same protection for broadcasts

Invalid requests raise `ValidationError` with a clear message indicating the field.

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

# 2. Type checking (strict mode enabled)
python3 -m mypy --strict custom_components/marstek/

# 3. Run all tests
pytest tests/ -q
```

**Do not consider a change complete until all three commands pass.** If any fails:
1. Fix the lint errors, type errors, or test failures
2. Re-run verification
3. Repeat until all pass

This ensures:
- **Code quality**: Ruff catches unused imports, style issues, and common bugs
- **Type safety**: The codebase uses `--strict` mypy; all functions need proper annotations
- **No regressions**: Tests must pass to confirm existing functionality isn't broken
- **CI alignment**: These are the same checks that run in GitHub Actions

## Testing and QA expectations

- Aim for Bronze+ quality scale: 100% config_flow coverage, connection tested in setup, unload/reload covered.
- Test structure: `tests/` mirrors platforms (`test_config_flow.py`, `test_init.py`, `test_sensor.py`, etc.) with shared fixtures in `tests/conftest.py`.
- Use `pytest-homeassistant-custom-component` with pinned versions in `requirements_test.txt`; mock UDP I/O—no live devices.
- Cover failures: cannot_connect, invalid_auth/invalid_discovery_info, already_configured, coordinator timeouts, action retries.
- Mark coordinator failures with `UpdateFailed` to surface entity unavailability.
- CI: run hassfest + lint (ruff) + **mypy --strict** + pytest (coverage threshold) on latest supported Python versions.
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
