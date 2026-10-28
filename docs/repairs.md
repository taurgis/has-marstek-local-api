# Repairs

Home Assistant lists these under **Settings** > **System** > **Repairs** when the integration detects something you can fix, or a firmware warning you should know about. Screenshots of the connection repair are in the [visual tour](screenshots.md#energy-dashboard-and-repairs).

## Cannot connect

If Home Assistant can’t connect to the device (for example, after a DHCP IP change), the integration can raise a repair flow that lets you:

- Enter an updated **Host** and **Port**
- Validate device identity (the device unique ID must match)
- Apply the fix and automatically reload the config entry

If the unique ID doesn’t match, the repair refuses the update to prevent accidentally pointing the entry at a different device.

## Firmware can reset Open API

On Venus A, D and E 3.0 firmware below **150**, Venus C (HMG-50) below **156**,
and any battery whose firmware number is missing or unknown, polling the Open API can disable it and
wipe settings ([#15](https://github.com/taurgis/has-marstek-local-api/issues/15)).
The warning is created from stored firmware metadata before the first UDP
probe, so it also appears while setup is retrying. See
[Troubleshooting](troubleshooting.md).

## Open API can interrupt the device's own meter

HMG-50 Control (Venus C, and the Venus E 2.0 builds this integration
refuses) shares one Wi-Fi receive channel between the Local API server and the
device's own UDP meter client, so polling can cost it the meter samples
that Self-consumption (Auto) mode regulates on
([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)). The
warning is created from stored firmware metadata for every HMG-50 entry,
including firmware **156**, and is not fixable from Home Assistant. See
[Troubleshooting](troubleshooting.md#venus-c-stops-charging-from-excess-solar-in-auto-mode).

## Other cases

The three repairs above are the ones the integration creates today: the connection repair, the reset-prone firmware warning, and the HMG-50 meter warning.

<img src="screenshots/repair-list.png" alt="Repair list" width="340" />
<img src="screenshots/repair-detail.png" alt="Repair detail" width="340" />
<img src="screenshots/repair-fix.png" alt="Repair fix" width="340" />
