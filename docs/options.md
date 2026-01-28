# Options

Open **Settings → Devices & services → Marstek → (device) → Configure**.

## Polling

The integration uses tiered polling to reduce device load:

- **Fast** (default 30s): mode/status/meter (real-time power)
- **Medium** (default 60s): PV status (Venus A/D)
- **Slow** (default 300s): WiFi + battery diagnostics

![Polling settings](screenshots/device-settings-polling.png)

## Network & requests

Options also expose request timing knobs to avoid UDP bursts:

- Request delay
- Request timeout
- Failures before unavailable

![Network settings](screenshots/device-settings-network.png)

## Power / behavior

Depending on device/firmware, additional power-related settings may be available.

![Power settings](screenshots/device-settings-power.png)

## Reconfigure

If you need to reconfigure the entry (e.g., after changes), use the device’s configure/reconfigure flow.

![Reconfigure flow](screenshots/device-reconfigure.png)
