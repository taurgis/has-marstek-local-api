# Options

Open **Settings → Devices & services → Marstek → (device) → Configure**.

## Polling

The integration uses tiered polling to reduce device load:

- **Fast** (default 30s, range 10–300s): mode/status/meter (real-time power)
- **Medium** (default 60s, range 30s–24h): PV status (Venus A/D)
- **Slow** (default 300s, range 60s–24h): WiFi + battery diagnostics

<img src="screenshots/device-settings-polling.png" alt="Polling settings" width="560" />

## Network & requests

Options also expose request timing knobs to avoid UDP bursts:

- Parallel API requests (advanced)
- Request delay (range 1–30s)
- Request timeout (range 5–60s)
- Failures before unavailable (range 1–10)

### Parallel API requests (advanced)

When enabled, the integration sends enabled status requests in parallel with no
inter-request delay.

- Improves update speed on some stable networks.
- Can increase API timeouts/failures, especially on Wi-Fi.
- Wired LAN is recommended if this mode is enabled. Firmware 150 fixed Local
  API **Ethernet** sends; Wi-Fi still goes through the FC41D module and can
  time out more often after idle (limited local testing). Ethernet replies
  typically arrive well under 500 ms, so the silent-wait copy is not sent.
- Ignored on Control firmware the integration treats as reset-prone
  (generation below 150, including Open API `ver` 1476 / app 147.6). Venus E
  3.0 **150** is the vendor Local API Ethernet fix. Reset-prone devices also
  serialize unicast requests and skip `Bat.GetStatus`.

The **Request delay** field remains configurable, but it is ignored while
parallel API requests is enabled.

The failures threshold controls when entities are marked unavailable. Until the
threshold is reached, the integration keeps the last known values to avoid
flapping on an unstable device API.

<img src="screenshots/device-settings-network.png" alt="Network settings" width="560" />

## Power / behavior

These settings affect automations and command validation:

- Action charge power (W, range -5000–0, default -1300): default power for the **Charge** device action
- Action discharge power (W, range 0–5000, default 800): default power for the **Discharge** device action
- Socket limit: toggles an internal power-limit model used to validate requested power (applies to services and actions)

Socket limit defaults to **on** for Venus C, Venus D, Venus E and Venus E mini, and **off** for Venus A. While it is on, validated discharge power is capped at **800 W**. With it off, the cap is the per-family maximum:

| Family | Maximum absolute power |
|---|---|
| Venus A | 1500 W |
| Venus D | 2200 W |
| Venus C, Venus E, Venus E mini | 2500 W |
| Unknown family | 5000 W |

Charging is always validated against the same per-family maximum (as a negative value); the socket limit only caps discharge.

<img src="screenshots/device-settings-power.png" alt="Power settings" width="560" />

## Reconfigure

If you need to reconfigure the entry (e.g., after changes), use the device’s configure/reconfigure flow.

<img src="screenshots/device-reconfigure.png" alt="Reconfigure flow" width="560" />
