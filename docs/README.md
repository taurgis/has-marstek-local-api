# Marstek (Local Open API) – Home Assistant

This custom integration lets Home Assistant monitor and control **Marstek energy storage devices** over the **local network** using the Marstek **Open API Rev 3.1 via UDP** (no cloud dependency).

Device family plus discovery firmware `ver` select a **firmware profile**. That profile decides energy/power decoding, which setup-time entities exist (PV, UPS, SYS), and how many manual schedule slots the device accepts. EM lifetime energy sensors appear when the meter payload reports those fields (scaled on Rev 3.1 firmware). Older firmware keeps the legacy entity set. Capable firmware adds UPS, SYS settings, and correctly scaled solar/meter totals.

## Highlights

- Local UDP polling (single coordinator per device)
- Automatic discovery + IP change handling, including reload when firmware capabilities change
- Stable entity IDs (survive IP changes)
- Control: operating mode (Auto/AI, plus UPS when allowed), passive mode and manual schedules via services, firmware-gated SYS settings (depth of discharge, Bluetooth advertising, panel LED)

## Compatibility

- Requires **Home Assistant Core 2025.10+**
- Device must support **Open API** and have it **enabled** in the Marstek app
- Default UDP port: **30000** (must be reachable on your LAN)
- Not every model exposes the same modes, schedule count, or energy encoding — see [Entities](entities.md) and the [Open API reference](marstek_device_openapi.MD)

> Warning: **Venus E2.0 is not compatible** with this integration.

## Pages

- [Installation](installation.md)
- [Configuration](configuration.md)
- [Options](options.md)
- [Entities](entities.md)
- [Energy Dashboard](energy_dashboard.md)
- [Services & automations](services.md)
- [Repairs](repairs.md)
- [Troubleshooting](troubleshooting.md)
- [Development](development.md)
- [Open API Rev 3.1 reference](marstek_device_openapi.MD)
- [Control firmware research (issue #15)](../tools/firmware/README.md)

## Screenshots

Screenshots referenced in these docs live in `docs/screenshots/`.
