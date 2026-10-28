---
title: Limitations
description: What the Marstek integration cannot do, why, and what each limitation means for you. Read this before relying on it.
---

# Limitations

Read this page before you rely on the integration for anything that matters, like keeping the house powered or reacting to grid prices. Most limits come from Marstek's Open API itself. A few are choices this integration makes on purpose.

## Limits of the Open API

**Changes show up after a delay.**
The battery never reports a change by itself. Home Assistant asks every 30 seconds by default. A change made in the Marstek app, or by the battery itself, can take up to that long to appear. Don't build automations that need a reaction within seconds.

**Some firmware is fragile.**
Some firmware versions switch the Open API off, or even reset their settings, when they are polled. Venus A, D, and E 3.0 firmware below 150 is the known case. Home Assistant shows a warning for these versions. Update the firmware in the Marstek app if you can. [Details](../troubleshooting.md#local-api-disables-itself-settings-reset-to-defaults)

**Requests sometimes get no answer.**
Over Wi-Fi, even good firmware drops some replies. On a Venus E 3.0 with firmware 150, about one Wi-Fi reply in five goes missing. On firmware known to cope with it, the integration resends read requests. It always keeps the last values for a few failed polls before marking entities unavailable. A wired Ethernet connection is more reliable where the battery has one.

**There is no password.**
Anyone on your network can read and control the battery once Open API is on. The integration can't change that. Keep the battery off guest networks.

**Some settings can't be read back.**
Marstek offers no way to read depth of discharge, Bluetooth advertising, the panel LED, or the stored schedule slots. Home Assistant shows the last value it sent successfully. If you change one of these in the Marstek app, Home Assistant won't know.

**Venus C can stop charging from solar.**
On Venus C, polling competes with the battery's own grid-meter link for one Wi-Fi channel. In Auto mode this can stop charging from excess solar. No firmware fixes it yet. [Details](supported-devices.md#venus-c-caveat)

**Venus E 2.0 is not supported.**
It runs the same controller as Venus C, and polling it can disconnect it from its CT meter.

**Venus E mini firmware 301 is limited.**
Passive mode is refused by the firmware. Replies only arrive on a normal /24 home network. Manual schedules are accepted, but whether the battery stores the power value is not confirmed yet. [Details](../troubleshooting.md#venus-e-mini-manual-power-shows-as-or-passive-times-out)

## Choices this integration makes

**One request at a time.**
Each battery gets one request at a time, with a pause between them. That's slower, but it is what the fragile firmware needs. Writing many schedule slots takes one round trip per slot.

**Risky requests are off by default.**
Battery temperature and the other battery-detail sensors use a request that has been linked to resets on some firmware ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)). They start disabled. Firmware updates and factory reset are not offered at all.

**Controls depend on firmware.**
Depth of discharge, the panel LED, Bluetooth advertising, and UPS mode only appear on firmware that accepts them (150 or newer on Venus A, D, and E 3.0). If the integration can't recognize the firmware, it uses the safest feature set.

**Manual and Passive are not on the mode dropdown.**
Both need extra settings: a power, a duration, or time slots. Set them with the services or device actions instead.

**One battery per entry.**
Each battery is added separately. There is no combined "system" device that adds batteries together. The Battery health dashboard does compare batteries side by side.

**Energy totals never go down.**
If the firmware briefly reports a lower or impossible lifetime total, the integration keeps the last plausible value so your Energy dashboard doesn't break. A real reset of the counter on the battery needs a statistics correction in Home Assistant.

## Project limits

**It is a community project.**
It is not made or supported by Marstek. Marstek can change the Open API in a firmware update, and a fix here can take a while. [Report issues](https://github.com/taurgis/has-marstek-local-api/issues) on GitHub, not to Marstek support.

**It is a custom HACS repository.**
It is not in the HACS default store or built into Home Assistant, so you add it as a custom repository. HACS still notifies you of updates.

**Only one `marstek` integration at a time.**
This integration, Marstek's own HACS integration, and a proposed built-in Home Assistant integration all use the same `marstek` name. Home Assistant can load only one of them. See the [comparison](comparison.md).

## What it does well despite all this

These limits are why the integration is careful, and they are what most of its code is for: tracking firmware versions, retrying lost replies, following IP changes, and telling a battery restart apart from a network hiccup. Each supported firmware version runs in automated tests, most of them against Marstek's own firmware in an emulator.
