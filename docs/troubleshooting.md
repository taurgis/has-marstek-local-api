# Troubleshooting

## Device not discovered

- Confirm **Open API is enabled** in the Marstek app.
- Ensure HA and the device are on the **same LAN segment**.
- Confirm UDP **port 30000** is allowed (router/AP isolation can break discovery).

## Entities unavailable

- Check WiFi quality and network stability.
- Try `marstek.request_data_sync`.
- Increase request timeout/failures threshold in [Options](options.md).

## Connection instability

- Reduce request rate (increase fast/medium intervals).
- Ensure only one controller is talking to the device.

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
