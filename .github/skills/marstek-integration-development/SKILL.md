---
name: marstek-integration-development
description: Repo-specific guidance for maintaining the Marstek integration (UDP Open API, polling, discovery/scanner) in a Home Assistant-friendly way
---

# Marstek Integration Development

This skill focuses on changes that must preserve the intent of this repository: a robust **local UDP Open API** integration for Marstek energy storage devices, while remaining safe and compatible with Home Assistant.

## When to Use

- You want to tweak polling behavior, discovery, or performance
- You’re modifying unique IDs / device registry behavior
- You’re changing scanner/config flow IP-update behavior
- You need to align changes with Home Assistant integration quality practices

## Project Intent

- The integration is **local** (no cloud dependency) and uses Marstek’s **OPEN API over UDP**.
- Discovery is **broadcast-based**; IP changes are handled automatically.
- The domain is `marstek`.

## Polling Interval Policy

Polling is **tiered** to reduce device load (configurable via options flow):

| Tier | Default | Commands |
|------|---------|----------|
| Fast | 30s | `ES.GetMode`, `ES.GetStatus`, `EM.GetStatus` (real-time power) |
| Medium | 60s | `PV.GetStatus` (solar data, Venus A/D only) |
| Slow | 300s | `Wifi.GetStatus`, `Bat.GetStatus` (diagnostics) |

The IP-change scanner runs separately (`scanner.py`, 60s interval).

Avoid shortening intervals without validating device/network stability. The device can be sensitive to request bursts.

## Config / Options / Reauth

- Keep setup, options, and reauth flows in `config_flow.py`; prefer selectors over raw text where it improves UX.
- Abort duplicates: ensure only one entry per device (BLE-MAC/host) and update entries when IP changes via scanner signals.
- Options updates should register `async_on_unload(entry.add_update_listener(...))` to reload on change.
- Reauth flows should request only the credential that changed and update the existing entry.

## Device Identification & Stability

- Unique IDs and device identifiers should stay stable across IP changes.
- Prefer **BLE-MAC** (then WiFi MAC) as the device identifier; IP addresses can change.
- Keep identifiers stable; changing them causes device/entity duplication for users.

## Adding New User-Facing Text

- Add text via `translations/en.json` and keep keys stable.
- Keep `custom_components/marstek/strings.json` and `custom_components/marstek/translations/en.json` in sync.

## Local Dev / Smoke Testing

- Use the VS Code task “Start Home Assistant” to run a dev instance.
- Watch logs for the `marstek` logger.
- Run pytest with `pytest-homeassistant-custom-component`; keep manifests hassfest/HACS-clean and requirements pinned.
## Verification After Changes (MANDATORY)

**After every code modification**, run both checks:

```bash
# 1. Type checking (strict mode)
python3 -m mypy --strict custom_components/marstek/

# 2. All tests
pytest tests/ -q
```

**Both must pass before considering any change complete.** The repository enforces strict typing with `mypy --strict`:
- All functions need return type annotations
- All parameters need type annotations  
- Generic types need explicit parameters (`dict[str, Any]`, `Future[None]`)
- Use `cast()` for type narrowing with mocked objects
## Common “gotchas”

- Sending extra UDP requests per update loop can overload the device.
- Doing discovery inside setup or coordinator updates can race with the scanner and cause flapping.
- Returning unstable unique IDs (e.g., IP-based) causes duplication after DHCP changes.
