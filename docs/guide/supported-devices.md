---
title: Will it work for me?
description: Which Marstek batteries and firmware versions work with the Home Assistant integration, which features each one gets, and which models to avoid.
---

# Will it work for me?

Short answer: it works with **Venus A, Venus C, Venus D, Venus E 3.0, and Venus E mini**. It does **not** work with **Venus E 2.0**. Venus C works, with one serious caveat.

## Supported batteries

| Battery | Works? | What to know |
|---|---|---|
| Venus E 3.0 | <span class="mk-yes">Yes</span> | The most tested model. Depth of discharge, LED, Bluetooth, and UPS mode from firmware 150. Ten schedule slots. |
| Venus A | <span class="mk-yes">Yes</span> | Adds solar (PV) sensors. Same firmware 150 gate for the extra controls. |
| Venus D | <span class="mk-yes">Yes</span> | Adds up to four solar (PV) channels. Same firmware 150 gate. |
| Venus E mini | <span class="mk-partial">Mostly</span> | Six schedule slots. Depth of discharge, LED, and Bluetooth controls. UPS mode on firmware 301. Firmware 301 refuses passive mode. Add it by IP address, and keep it on a normal /24 home network. |
| Venus C | <span class="mk-partial">With a caveat</span> | Polling can stop Auto mode from charging on excess solar. No depth of discharge, LED, Bluetooth, or UPS controls. [Read the caveat](#venus-c-caveat). |
| Venus E 2.0 | <span class="mk-no">No</span> | Polling can disconnect it from its CT meter. The integration refuses to set it up. |
| Other Open API devices | <span class="mk-partial">Maybe</span> | Treated as an unknown model with the safest feature set. Untested. |

> [!TIP] How do I tell Venus E 2.0 from Venus E 3.0?
> Check the model name in the Marstek app or on the label. Venus E 3.0 reports itself as `VenusE 3.0` or `VNSE3-0`. Venus E 2.0 reports `VenusE`. If you add a Venus E 2.0, the integration stops with an error instead of polling it.

## Firmware matters

Marstek ships features through firmware updates, and older firmware misbehaves more. The integration reads the firmware number from the battery and turns features on or off to match. A control your battery would reject is left off the device page, instead of showing up and failing.

To see your battery's firmware number, open its device page in Home Assistant (**Settings** > **Devices & services** > **Marstek** > your battery). It is listed under **Device info**. Home Assistant shows it the way the battery reports it, so app version 147.6 appears as `1476`.

- **Firmware 150 or newer** on Venus A, D, and E 3.0 unlocks depth of discharge, the panel LED, Bluetooth advertising, and UPS mode.
- **Firmware below 150** on those models is known to switch its Open API off, and sometimes reset its settings, under polling. Home Assistant shows a warning in **Settings** > **System** > **Repairs** for these versions. Update the battery in the Marstek app if you can. See [troubleshooting](../troubleshooting.md#local-api-disables-itself-settings-reset-to-defaults).
- After a firmware update, the integration notices the new version on its own within about 10 minutes and adds or removes controls. You don't need to remove and re-add the battery.

### Tested firmware versions

Every row in the [test matrix](../development.md#test-matrix) is covered by the automated tests, which simulate each version. Most versions have also been run on Marstek's own unmodified firmware in an emulator. A few have no public firmware image, so they are only simulated.

- **Venus E 3.0:** 144, 145, 147, 147.6, 148, 149, 150, 151
- **Venus A:** 148, 148.7, 149, 149.1, 150, 150.9, 151
- **Venus D:** 145, 147, 149, 149.2, 150, 151
- **Venus C:** 153, 155, 156
- **Venus E mini:** 145, 301

Missing yours? [Open a firmware coverage issue](https://github.com/taurgis/has-marstek-local-api/issues/new?template=firmware_coverage.yml). Owner reports are the best evidence there is.

## Venus C caveat

Venus C uses a different controller (Marstek calls it HMG-50) than Venus A, D, and E 3.0. On that controller, the Open API and the battery's own link to its grid meter share one Wi-Fi channel. When Home Assistant polls, some meter readings get lost. In **Auto** (self-consumption) mode, enough lost readings make the battery think the meter is gone, and it stops charging from excess solar ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)).

No firmware fixes this so far, 156 included. If you own a Venus C:

- Raise the polling intervals under **Configure** > **Polling Intervals**.
- Read grid power from your meter's own integration (HomeWizard P1, Shelly) instead of from the battery.
- If Auto mode keeps stalling, poll less, or stop polling the battery for a while. That is the only reliable fix known today. The Modbus integrations in the [comparison](comparison.md) don't list Venus C.

Venus E 2.0 runs the same controller, which is why it is not supported.

## What else you need

- **Home Assistant 2025.10 or newer.**
- **The same network.** Home Assistant and the battery must reach each other over UDP. Discovery needs them on the same network segment. A battery on another VLAN works if you type its IP address.
- **Open API switched on** in the Marstek app. [Before you start](before-you-start.md) covers this.

## Next

Go through [Before you start](before-you-start.md), then follow [Getting started](getting-started.md).
