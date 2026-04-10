# Troubleshooting

## Device not discovered

- Confirm **Open API is enabled** in the Marstek app.
- Ensure HA and the device are on the **same LAN segment**.
- Confirm UDP **port 30000** is allowed (router/AP isolation can break discovery).

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
