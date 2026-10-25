# Mock energy plug (dev only)

A tiny Home Assistant custom integration that stands in for a third-party
smart plug, so the Marstek **Energy meter (optional)** setting can be tested
with something other than HomeWizard. It is not shipped with the Marstek
integration and is not meant for real devices.

It reads a Shelly-style RPC endpoint that the mock battery serves when it runs
with `--plug-http-port` (see [the mock README](../mock_device/README.md)):

| Request | Used for |
|---|---|
| `GET /rpc/Shelly.GetDeviceInfo` | Config flow check and unique ID |
| `GET /rpc/Switch.GetStatus?id=0` | Polled every 10 seconds |

It creates three sensors on one device:

| Sensor | Unit | State class |
|---|---|---|
| Power | W | measurement |
| Energy (into the battery) | Wh | total_increasing |
| Returned energy (out of the battery) | Wh | total_increasing |

The units differ from HomeWizard's kWh on purpose, so the Marstek side has to
convert them.

## Use it

The devcontainer's `docker-compose.yml` mounts `mock_energy_plug/` into Home
Assistant's `custom_components`. Restart Home Assistant, then add
**Mock energy plug (dev only)** with host `172.28.0.20`.

Outside the devcontainer, copy `mock_energy_plug/` into your test
configuration's `custom_components/` folder.
