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
