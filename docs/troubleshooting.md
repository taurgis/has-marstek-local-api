# Troubleshooting

## Missing UPS, Panel LED, Bluetooth advertising, or depth of discharge

Those controls are firmware-profile gated. They are **omitted** when the device cannot accept the matching Open API write — they are not left permanently unavailable.

| Control | When it appears |
|---|---|
| Depth of discharge, Bluetooth advertising, Panel LED | Venus A/C/D/E at firmware **`ver >= 150`**; Venus E mini when discovery `ver` is a **known integer** (no 150 minimum) |
| UPS on the operating-mode select | ES-capable family (including Venus E mini) with **`ver >= 150`** |

If they are missing:

1. Check the device model on the device page (Venus E mini is not Venus E).
2. Check discovery firmware `ver` (`Device version` diagnostic, or **Download diagnostics** → `firmware_profile`).
3. Unknown or unparseable `ver` stays legacy-safe: no SYS and no UPS.
4. After a firmware update that crosses the gate, the scanner reloads the config entry; you do not need to delete and re-add the device.

The Open API documents **no GET methods** for DOD, Bluetooth advertising, or LED. Home Assistant restores the last value it successfully wrote. Changes made in the Marstek app, after a device reboot, or by another controller are not detected.

Older notes that “LED is not in the API” applied to legacy Open API firmware. Capable firmware has a **Panel LED** switch (`Led.Ctrl`).

`Set.Ver` and factory reset are intentionally not exposed in the default UI.

## Device not discovered

- Confirm **Open API is enabled** in the Marstek app.
- Ensure HA and the device are on the **same LAN segment**.
- Confirm UDP **port 30000** is allowed (router/AP isolation can break discovery).
- This integration **sends from each device’s configured Open API port** (default 30000, but the port is user-configurable in the Marstek app). Some firmware replies only to that listen port and ignores ephemeral source ports. Devices on different ports each get their own local socket. Devices that share a port share that socket: adding a second same-port device reuses it for `Marstek.GetDevice` instead of binding again. If another integration already bound a device’s port without `SO_REUSEPORT`, setup can fail with `No valid response from device`; stop the other client or change the Marstek Open API port in the app.

## Entities unavailable

- Check WiFi quality and network stability.
- Try `marstek.request_data_sync`.
- Increase request timeout/failures threshold in [Options](options.md).

Note: Because the device Open API can be unstable, the integration keeps the
last known values during transient failures. Entities are marked unavailable
only after the configured "Failures before unavailable" threshold is reached.
This is expected behavior.

## Connection instability

- Reduce request rate (increase fast/medium intervals).
- Ensure only one controller is talking to the device.
- If **Parallel API requests** is enabled, try disabling it first (especially on Wi-Fi).
- In diagnostics, check `polling_config.request_strategy` and
  `polling_config.request_delay_effective` to confirm actual request behavior.

## Grid energy totals look frozen

On some Venus firmware builds, the device keeps returning fixed
`total_grid_input_energy` or `total_grid_output_energy` values even while
`on_grid_power` continues to show import/export activity.

The integration now keeps those totals moving by using the reported grid power
between updates and restores the corrected total after Home Assistant restarts.
If the totals still look wrong, compare the grid energy sensors with
`sensor.<device>_on_grid_power` and include debug logs in your report.

## Venus A solar totals look 10× too low, then jump

Venus A firmware 149 reports `total_pv_energy` as 0.01 kWh (for example raw
`25742` meaning `257.42 kWh`). Older integration versions stored that raw value
as Wh, so Energy Dashboard solar production was about 10× too low.

The integration now converts that field to Wh. Existing recorder history is
**not** rewritten. The first corrected state is a large upward delta, which
Home Assistant accumulates; it is **not** a meter reset. A reset is a
decrease in a `total_increasing` sensor.

If the Energy Dashboard shows a one-time production spike at the upgrade, use
**Settings → Tools → Statistics** to locate and correct the transition:
https://www.home-assistant.io/docs/tools/dev-tools/#statistics-tab

Firmware **148** (including app labels such as `148.3`) keeps solar energy in
Wh. Integration **1.1.0** introduced firmware-gated **solar energy** scaling:
only Venus A **149** (and known families at **150+**) starts the 0.01 kWh
encoding ([#35](https://github.com/taurgis/has-marstek-local-api/issues/35)).
That energy fix is separate from PV1 power.

## PV1 power looks 10× too high

PV1 (`PV.GetStatus` channel 1) is a different field from total solar energy.
Issue [#57](https://github.com/taurgis/has-marstek-local-api/issues/57) is PV1
about 10× too high after integration **1.1.0**, first on firmware **148.3** and
also on **150.9**. Channel 1 is still deciwatts on those builds; the
integration divides it by 10, matching 1.0.0. The Rev 3.1 PDF labels PV as
watts, but skipping ÷10 at `ver >= 150` is what made PV1 jump.

If PV1 is still about 10× higher than the Marstek app, enable debug logging and
capture `Send:` / `Recv:` for `PV.GetStatus` (`pv1_power`) plus
`Marstek.GetDevice` (`ver`). App labels such as `148.3` and `150.9` map to
Open API integers `148` and `150`. Diagnostics should show
`pv_channel_1_power_scale` of `0.1`.

## Venus A solar or load energy stays at 0 Wh

Some Venus A firmware versions report `total_pv_energy` as `0` even while PV
channel power is active.

The integration treats those contradicted zero solar totals as invalid instead
of exposing a misleading lifetime counter. That means `total_pv_energy` may
stay unavailable until the device reports a real non-zero total.

If you need solar energy in the meantime, compare the sensor with
`sensor.<device>_pv_power` and consider Home Assistant's Integration helper to
derive energy from power.

`total_load_energy` remains device-reported because Marstek documents it as
load or off-grid energy and its exact semantics vary by firmware.

## Venus E2.0

Venus **E2.0 is not supported**.

## Debug logging

When reporting an issue, enabling debug logging helps us diagnose UDP communication and coordinator behavior.

### Preferred: enable debug logging from the UI (no restart)

1. Go to **Settings → Devices & Services**.
2. Open the **Marstek** integration.
3. Open the **⋮** menu → **Enable debug logging**.
4. Reproduce the problem.
5. Go back to the Marstek integration menu → **Disable debug logging** and download the log file.

Official Home Assistant docs: https://www.home-assistant.io/docs/configuration/troubleshooting/#enabling-debug-logging

### Advanced: configuration.yaml (logger integration)

Add to `configuration.yaml`:

```yaml
logger:
	default: warning
	logs:
		custom_components.marstek: debug
		custom_components.marstek.pymarstek: debug
```

Then reload/restart as appropriate.

Official Home Assistant docs: https://www.home-assistant.io/integrations/logger/

### What to include in a bug report

- Home Assistant version + Marstek integration version
- Steps to reproduce
- Downloaded debug log (or the relevant excerpt around the error/traceback)
- Downloaded diagnostics (Marstek integration menu → **⋮ → Download diagnostics**)

Note: debug logs may include sensitive details; please only include what’s relevant.
