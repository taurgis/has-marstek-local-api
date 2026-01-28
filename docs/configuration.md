# Configuration

## Add the integration

1. Go to **Settings → Devices & services**.
2. Click **Add integration**.
3. Search for **Marstek**.
4. Select your device from the discovered list and complete setup.

If discovery doesn’t find your device (or all discovered devices are already configured), Home Assistant will guide you to **manual entry** where you can enter the device **IP address** (and optionally a **port**, default `30000`).

### Discovery screen examples

<img src="screenshots/device-discovery.png" alt="Device discovery" width="340" />
<img src="screenshots/device-list.png" alt="Device list" width="340" />

## Device page

After setup you’ll see a device page with sensors/entities grouped under the device.

<img src="screenshots/device-details.png" alt="Device details" width="560" />

## IP changes

If your device IP changes (DHCP), the integration’s background scanner will detect it and update the config entry.

Unique IDs are based on the device’s **BLE MAC** (falling back to other MACs when needed) so entities remain stable across IP changes.

## Unsupported devices

Venus **E2.0** is not supported.
