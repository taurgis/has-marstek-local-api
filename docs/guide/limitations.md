---
title: Limitations
description: What the Marstek integration cannot do, why, and what each limitation means for you. Read this before relying on it.
---

# Limitations

Read this page before you rely on the integration for anything that matters, like keeping the house powered or reacting to grid prices. Most limits come from Marstek's [Open API](glossary.md#open-api-local-api) itself, the local network interface the integration uses to talk to the battery. A few are choices this integration makes on purpose.

## Limits of the Open API

**Changes show up after a delay.**
The battery never reports a change by itself. Home Assistant asks every 30 seconds by default. A change made in the Marstek app, or by the battery itself, can take up to that long to appear. Don't build automations that need a reaction within seconds.

**Some firmware is fragile.**
Some [firmware](glossary.md#firmware) versions switch the Open API off, or even reset their settings, when Home Assistant [polls](glossary.md#polling) them for data. Owners have seen this on Venus A, D, and E 3.0 below firmware 150. To be safe, Venus C below 156 and Venus E mini 145 are treated the same way. Home Assistant shows a warning for these versions. Update the firmware in the Marstek app if you can. See [why the Open API switches itself off](../troubleshooting.md#local-api-disables-itself-settings-reset-to-defaults).

**Requests sometimes get no answer.**
Over Wi-Fi, even good firmware drops some replies. On a Venus E 3.0 with firmware 150, about one Wi-Fi request in four gets no answer the first time. On firmware known to cope with it, the integration resends read requests. It always keeps the last values for a few failed polls before marking the battery's [entities](glossary.md#entity) unavailable. A wired Ethernet connection is more reliable where the battery has one.

**There is no password.**
Anyone on your network can read and control the battery once Open API is on. The integration can't change that. Keep the battery off guest networks.

**Some settings can't be read back.**
Marstek offers no way to read depth of discharge, Bluetooth advertising, the panel LED, or the stored schedule slots. Home Assistant shows the last value it sent successfully. If you change one of these in the Marstek app, Home Assistant won't know.

**Venus C can stop charging from solar.**
On Venus C, polling competes with the battery's own grid-meter link for one Wi-Fi channel. In Auto mode this can stop charging from excess solar. No firmware fixes it yet. See the [Venus C caveat](supported-devices.md#venus-c-caveat).

**Venus E 2.0 is not supported.**
It runs the same controller as Venus C, and polling it can disconnect it from its CT meter.

**Venus E mini firmware 301 is limited.**
The firmware refuses [Passive mode](glossary.md#passive-mode). Discovery can't find it, so add it by IP address. Its replies only reach Home Assistant on a normal home network with mask `255.255.255.0` (a [/24](glossary.md#subnet-24), the usual router default). Manual schedules with a power value work since integration 1.3.2. See [Venus E mini troubleshooting](../troubleshooting.md#venus-e-mini-manual-power-shows-as-or-passive-times-out).

## Choices this integration makes

**One request at a time.**
By default each battery gets one request at a time, with a pause between them. Pausing between requests makes updates slower, but it is what the fragile firmware needs. On firmware known to cope, you can allow parallel requests under [Options](../options.md). Writing many schedule slots takes one round trip per slot.

**Risky requests are off by default.**
Battery temperature and the other battery-detail sensors use a request that has been linked to resets on some firmware ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)). They start disabled, and on firmware known to be fragile they are not offered at all. Firmware updates and factory reset are not offered at all.

**If resets keep happening, a wired connection may suit you better.**
Some owners report that Marstek support advises against the Open API, and some moved to a [Modbus](glossary.md#modbus) integration after resets. If your battery keeps resetting on firmware 150 or newer, [open an issue](https://github.com/taurgis/has-marstek-local-api/issues) with diagnostics, and consider a [Modbus integration](comparison.md#modbus-integrations) in the meantime.

**Controls depend on firmware.**
[Depth of discharge](glossary.md#depth-of-discharge-dod), the panel LED, Bluetooth advertising, and [UPS mode](glossary.md#ups-mode) only appear on firmware that accepts them: 150 or newer on Venus A, D, and E 3.0. Venus E mini has depth of discharge, the panel LED, and Bluetooth advertising on any firmware, and UPS mode on firmware 301. Venus C has none of them. If the integration can't recognize the firmware, it uses the safest feature set.

**Manual and Passive can't be chosen from the mode dropdown.**
They appear there so you can see the current mode, but both need extra settings: a power, a duration, or time slots. Choosing them in the dropdown shows an error. Set them with the [services](glossary.md#service-action) or [device actions](glossary.md#device-action) instead.

**One battery per entry.**
Each battery is added separately. There is no combined "system" device that adds batteries together. The Battery health dashboard does compare batteries side by side.

**Glitched energy totals are filtered.**
The two "Total grid" counters are held so they never drop. On every lifetime total, readings of zero, impossible values, and huge jumps are ignored, so your Energy dashboard doesn't break. A real reset of the counter on the battery needs a statistics correction in Home Assistant.

## Project limits

**It is a community project.**
It is not made or supported by Marstek. Marstek can change the Open API in a firmware update, and a fix here can take a while. [Report issues](https://github.com/taurgis/has-marstek-local-api/issues) on GitHub, not to Marstek support.

**It is a custom HACS repository.**
It is not in the [HACS](glossary.md#hacs) default store or built into Home Assistant, so you add it to HACS by hand as a custom repository. [Installation](../installation.md) shows how. HACS still notifies you of updates.

**Only one `marstek` integration at a time.**
This integration, Marstek's own HACS integration, and a proposed built-in Home Assistant integration all use the same internal name, `marstek`. Home Assistant can load only one of them at a time. See the [comparison](comparison.md).

## Why it's built this way

These limits are why the integration is careful, and they are what most of its code is for: tracking firmware versions, retrying lost replies, following IP changes, and telling a battery restart apart from a network hiccup. Every supported firmware version is covered by the automated tests, and most have also been run against Marstek's own firmware in an emulator, a program that runs the battery's real software on a computer.

Still a fit? Follow [Getting started](getting-started.md).
