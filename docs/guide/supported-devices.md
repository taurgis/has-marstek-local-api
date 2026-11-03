---
title: Will it work for me?
description: Which Marstek batteries and firmware versions work with the Home Assistant integration, which features each one gets, and which models to avoid.
---

# Will it work for me?

Short answer: it works with **Venus A, Venus C, Venus D, Venus E 3.0, and Venus E mini**. It does **not** work with **Venus E 2.0**. Venus C works, with one serious caveat.

The table uses a few battery terms. Depth of discharge is how low the battery may discharge. UPS mode is a backup-power mode. The CT meter is the clamp-on meter at your fuse box that measures grid power. The [glossary](glossary.md) has the rest.

## Supported batteries

| Battery | Works? | What to know |
|---|---|---|
| Venus E 3.0 | <span class="mk-yes">Yes</span> | The most tested model. Depth of discharge, LED, Bluetooth, and UPS mode from firmware 150. Ten schedule slots. |
| Venus A | <span class="mk-yes">Yes</span> | Adds solar (PV) sensors. The extra controls need firmware 150, as on Venus E 3.0. |
| Venus D | <span class="mk-yes">Yes</span> | Adds up to four solar (PV) inputs. The extra controls need firmware 150. |
| Venus E mini | <span class="mk-partial">Mostly</span> | Six schedule slots. Depth of discharge, LED, and Bluetooth controls. UPS mode on firmware 301. Firmware 301 refuses passive mode. Add it by IP address, and keep it on a network with the usual home router settings. See [Venus E mini notes](#venus-e-mini-notes). |
| Venus C | <span class="mk-partial">With a caveat</span> | Home Assistant's regular requests can stop Auto mode from charging on excess solar. No depth of discharge, LED, Bluetooth, or UPS controls. [Read the caveat](#venus-c-caveat). |
| Venus E 2.0 | <span class="mk-no">No</span> | Home Assistant's regular requests can disconnect it from its CT meter. The integration refuses to set it up. |
| Other Marstek devices with Open API | <span class="mk-partial">Maybe</span> | Treated as an unknown model and given only the safest features. Untested. |

> [!TIP] How do I tell Venus E 2.0 from Venus E 3.0?
> Check the model name in the Marstek app or on the label. Venus E 3.0 reports itself as `VenusE 3.0` or `VNSE3-0`. Venus E 2.0 reports `VenusE`. If you add a Venus E 2.0, the integration stops with an error and never starts asking it for data.

## Firmware matters

Marstek ships features through firmware updates, and older firmware misbehaves more. The integration reads the firmware number from the battery and turns features on or off to match. A control your battery would reject is left off the device page, instead of showing up and failing.

To see your battery's firmware number, open its device page in Home Assistant (**Settings** > **Devices & services** > **Marstek** > your battery). It is listed under **Device info**. Home Assistant shows it the way the battery reports it, so app version 147.6 appears as `1476`.

- **Firmware 150 or newer** on Venus A, D, and E 3.0 unlocks depth of discharge, the panel LED, Bluetooth advertising, and UPS mode.
- **Firmware below 150** on those models is known to switch its Open API off, and sometimes reset its settings, when Home Assistant asks it for data at regular intervals. Venus C below 156 and Venus E mini 145 are treated the same way to be safe. Home Assistant shows a warning in **Settings** > **System** > **Repairs** for these versions. Update the battery in the Marstek app if you can. See [troubleshooting](../troubleshooting.md#local-api-disables-itself-settings-reset-to-defaults).
- After a firmware update, the integration notices the new version on its own within about 10 minutes and adds or removes controls. You don't need to remove and re-add the battery.

### Tested firmware versions

Every row in the [test matrix](../development.md#test-matrix) is covered by the automated tests, which simulate each version. Most versions have also been tested on Marstek's own unmodified firmware, running in an emulator (a program that imitates the battery's hardware on a computer). A few versions have no public firmware download, so they are only simulated.

- **Venus E 3.0:** 144, 145, 147, 147.6, 148, 149, 150, 151
- **Venus A:** 148, 148.7, 149, 149.1, 150, 150.9, 151
- **Venus D:** 145, 147, 149, 149.2, 150, 151
- **Venus C:** 153, 155, 156
- **Venus E mini:** 145, 301

Missing yours? [Open a firmware coverage issue](https://github.com/taurgis/has-marstek-local-api/issues/new?template=firmware_coverage.yml). Owner reports are the best evidence there is.

## Venus C caveat

Venus C uses a different controller board than Venus A, D, and E 3.0. Marstek calls it HMG-50. On that board, the Open API and the battery's own link to its grid meter share one Wi-Fi channel. When Home Assistant asks the battery for data, some meter readings get lost. In **Auto** (self-consumption) mode, enough lost readings make the battery think the meter is gone. It then stops charging from excess solar ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)).

None of the firmware checked so far (153, 155, 156) fixes this. Firmware 158 hasn't been checked yet.

On Venus E 2.0 firmware 158, one owner later got charging, discharging and Auto working from their own script. The script spaced its commands about 30 seconds apart ([#121](https://github.com/taurgis/has-marstek-local-api/issues/121)). That hasn't been tried with this integration, and it doesn't fix the meter problem.

If you own a Venus C:

- Make Home Assistant ask less often: raise the polling intervals under **Configure** > **Polling Intervals**.
- Read grid power from your meter's own integration (HomeWizard P1, Shelly) instead of from the battery.
- If Auto mode keeps stalling, poll less, or stop polling the battery for a while. That is the only reliable fix known today.
- Looking at other integrations? Most of the Modbus ones (they use a wired connection) in the [comparison](comparison.md) don't list Venus C. hm2mqtt does, over MQTT.

Venus E 2.0 uses the same controller board, which is why it is not supported.

## Venus E mini notes

Venus E mini works, but it differs from the other models in a few ways ([#86](https://github.com/taurgis/has-marstek-local-api/issues/86)):

- **Add it by IP address.** It doesn't answer when Home Assistant searches the network, so it won't show up in the list of found batteries. Choose **Enter IP/port manually** when you [add the battery](getting-started.md#step-2-add-your-battery).
- **Keep it on a normal /24 home network.** Firmware 301 sends its replies to an address that only works when the network uses the usual home-router subnet mask, `255.255.255.0` (also written /24).
- **Six schedule slots**, not ten. Manual schedules with a power value work since integration version 1.3.2. If one is refused, turn off any schedule in the Marstek app that overlaps it.
- **No Passive mode on firmware 301.** The firmware doesn't have it, so the integration leaves it out. Auto, AI, Manual, and UPS work.
- **Depth of discharge, the panel LED, and Bluetooth advertising** are available.

[Venus E mini troubleshooting](../troubleshooting.md#venus-e-mini-manual-power-shows-as-or-passive-times-out) has the details.

## What else you need

- **Home Assistant 2025.10 or newer.**
- **The same network.** Home Assistant and the battery must be able to reach each other. Automatic discovery only finds the battery when both are on the same network. If the battery is on a separate network (VLAN), it still works when you type its IP address.
- **Open API switched on** in the Marstek app. [Before you start](before-you-start.md) covers this.

## Next

Go through [Before you start](before-you-start.md), then follow [Getting started](getting-started.md).
