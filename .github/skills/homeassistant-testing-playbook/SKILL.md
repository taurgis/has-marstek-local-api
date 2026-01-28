---
name: homeassistant-testing-playbook
description: Practical test/CI playbook for Home Assistant custom components (config flow, coordinator, entities, diagnostics)
---

# Home Assistant Testing Playbook

Use this skill to design and implement tests/CI for a custom integration targeting Bronze/Silver quality scale.

## When to Use
- Adding or improving tests (`tests/` structure, fixtures, snapshots)
- Ensuring config flow coverage and duplicate/reauth handling
- Verifying coordinator error handling/unavailable states
- Planning CI (hassfest, lint, pytest, coverage)

## Test Layout
- `tests/`
  - `conftest.py`: enable custom integrations; shared fixtures (MockConfigEntry, mock UDP/HTTP, snapshot extension)
  - `test_config_flow.py`: user/discovery/reauth/options flows
  - `test_init.py`: setup/unload/reload lifecycle
  - Platform tests (e.g., `test_sensor.py`) per platform
  - `fixtures/`: sanitized API/device payloads for mocks/snapshots

## Fixtures & Tools
- `pytest-homeassistant-custom-component`: hass fixture, MockConfigEntry, aiohttp client mock (if HTTP), time helpers.
- Snapshot: `syrupy` + `HomeAssistantSnapshotExtension` for diagnostics/device registry/entity attributes; redact secrets before snapshotting.
- Time travel: `freezer` + `async_fire_time_changed` to advance coordinator timers.
- Coverage: `pytest --cov=custom_components.marstek --cov-report=xml --cov-fail-under=95`.
- Examples: see [references/EXAMPLES.md](references/EXAMPLES.md) for skeleton tests (config flow, setup/unload, coordinator failures, diagnostics snapshots).
 - Repo scaffold: see `tests/` and `requirements_test.txt` for pinned deps and starter tests.

## Config Flow Test Matrix
- Happy path: discovery list → select → creates entry; asserts unique_id set.
- Errors: `cannot_connect`, `invalid_auth`, `invalid_discovery_info`, `already_configured` aborts.
- DHCP/integration discovery: updates host when MAC matches; aborts duplicate.
- Options flow: returns form and creates entry; ensure reload listener is in place.
- Reauth (if applicable): asks only for changed credential; updates existing entry.

## Coordinator & Entities
- Mock transport/library to avoid real network.
- Happy path: coordinator populates data; entities render expected state/attributes/units.
- Failure path: transport error → `UpdateFailed` → entities become unavailable; `last_update_success` false.
- Debounce/refresh: multiple `async_request_refresh` calls coalesce to one fetch (spy call count).
- Entity gating: only create entities when data keys exist; ensure unique_id stable (MAC-based) and device_info shared.

## Actions/Commands (if present)
- Verify polling is paused during commands; retries/backoff executed; verification logic validates device state.
- Failure surfaces as error (no silent success); no extra coordinator fetches are triggered concurrently.

## Diagnostics & Snapshots (Gold path)
- Add diagnostics view; snapshot output with syrupy; ensure secrets (tokens, IPs if needed) are redacted.
- Device registry snapshot: manufacturer/model/identifiers/connections are correct and stable.

## CI Pipeline
- hassfest before tests; lint with `ruff` and **`mypy --strict`** (required, not optional).
- Matrix on latest supported Python versions.
- Pin test deps in `requirements_test.txt` to avoid drift.
- Optional: upload coverage (e.g., Codecov) for PR diffs.

## Verification After Changes (MANDATORY)

**After every code modification**, you MUST run verification:

```bash
# 1. Type checking (strict mode enforced)
python3 -m mypy --strict custom_components/<domain>/

# 2. Run all tests
pytest tests/ -q
```

Do not consider work complete until both pass. This catches:
- Type annotation errors (missing return types, wrong argument types)
- Regressions in existing functionality
- Integration issues between components

Fix failures immediately before moving on.

## Quick Checklist
- [ ] Config flow tests cover success + cannot_connect + invalid_auth + invalid_discovery_info + already_configured
- [ ] Options flow tested; reload listener present
- [ ] Coordinator tests cover success + UpdateFailed path → entities unavailable
- [ ] Entities only created for available data keys; unique IDs MAC-based
- [ ] Actions pause polling, retry, and verify outcomes
- [ ] Snapshots for diagnostics/device registry (if diagnostics enabled)
- [ ] CI runs hassfest, lint, pytest with coverage gate
- [ ] **mypy --strict passes (0 errors)**
- [ ] **All tests pass (pytest tests/ -q)**
