# Marstek (Local Open API) – Home Assistant

This custom integration lets Home Assistant monitor and control **Marstek energy storage devices** over the **local network** using the Marstek **Open API via UDP** (no cloud dependency).

## Highlights

- Local UDP polling (single coordinator per device)
- Automatic discovery + IP change handling
- Stable entity IDs (survive IP changes)
- Control: operating mode, passive mode, manual schedules

## Compatibility

- Requires **Home Assistant Core 2025.10+**
- Device must support **Open API** and have it **enabled** in the Marstek app
- Default UDP port: **30000** (must be reachable on your LAN)

> Warning: **Venus E2.0 is not compatible** with this integration.

## Pages

- [Installation](installation.md)
- [Configuration](configuration.md)
- [Options](options.md)
- [Entities](entities.md)
- [Services & automations](services.md)
- [Repairs](repairs.md)
- [Troubleshooting](troubleshooting.md)
- [Development](development.md)

## Screenshots

Screenshots referenced in these docs live in `docs/screenshots/`.
