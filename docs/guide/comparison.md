---
title: Compared with other integrations
aside: false
pageClass: mk-compare
description: An honest comparison of Home Assistant integrations for Marstek batteries, covering local UDP, Modbus, MQTT, cloud, and Bluetooth, and which one fits your setup.
---

# Compared with other integrations

There are several ways to get a Marstek battery into Home Assistant. They differ mostly in **how** they talk to the battery, and that decides which batteries they support and what extra hardware you need. This page is written by the maintainers of one of them, so check the links and judge for yourself.

> [!NOTE] Last reviewed 2026-10-28
> Release and activity facts come from each project's own GitHub page on that date. Projects change quickly. Every change to this page is in its [Git history](https://github.com/taurgis/has-marstek-local-api/commits/main/docs/guide/comparison.md). Corrections are welcome as a [pull request](https://github.com/taurgis/has-marstek-local-api/edit/main/docs/guide/comparison.md).

## First, pick a connection type

| Connection | How it works | Extra hardware | Good for |
|---|---|---|---|
| **Local Open API (UDP)** | Home Assistant asks the battery over Wi-Fi or Ethernet, using the interface you enable in the Marstek app. | None | Venus A, C, D, E 3.0, E mini. No wiring. |
| **Modbus TCP** | Home Assistant reads the battery's control registers. | An RS485-to-Ethernet adapter on most models. Some newer models have it over Ethernet. | Venus E 2.0, and anyone who wants a wired, register-level connection. |
| **MQTT** | The battery's cloud connection is redirected to a local MQTT broker. | An MQTT broker and a bridge add-on | B2500 and Jupiter, which have no Open API. |
| **Cloud** | Home Assistant logs in to Marstek's servers. | None | Read-only monitoring when nothing local works. Stops when the cloud is down. |
| **Bluetooth** | Home Assistant reads the battery over Bluetooth Low Energy. | A Bluetooth adapter or proxy in range | Read-only monitoring. |

## Open API (UDP) integrations

All three use the same local interface as this one, so they share its limits: polling, fragile older firmware, and no Venus E 2.0.

| Feature | This integration | Marstek (vendor) | Marstek Venus E (yuyuki) | Marstek Local API (jaapp) |
|---|---|---|---|---|
| Repository | [taurgis/has-marstek-local-api](https://github.com/taurgis/has-marstek-local-api) | [marstekEnergy/ha_marstek](https://github.com/marstekEnergy/ha_marstek) | [yuyuki/hacs_marstek_venus_e](https://github.com/yuyuki/hacs_marstek_venus_e) | [jaapp/ha-marstek-local-api](https://github.com/jaapp/ha-marstek-local-api) |
| Models | Venus A, C, D, E 3.0, E mini | Venus E 3.0, A, D | Venus E | Venus C, D, E |
| Mode select | Auto, AI, and UPS where the firmware allows it. Manual and Passive are shown, but set through device actions and services | Auto, AI, Manual, UPS | Auto, AI, Manual, Passive | Buttons for Auto, AI, Manual |
| Passive mode | <span class="mk-yes">Yes</span> | <span class="mk-yes">Yes</span> (service) | <span class="mk-yes">Yes</span> | <span class="mk-yes">Yes</span> |
| Manual schedules | <span class="mk-yes">Yes</span> | <span class="mk-partial">Experimental</span> | <span class="mk-yes">Yes</span> | <span class="mk-yes">Yes</span> |
| Depth of discharge, LED, Bluetooth | <span class="mk-yes">Yes</span>, firmware-gated | <span class="mk-yes">Yes</span> | <span class="mk-no">No</span> | <span class="mk-no">No</span> |
| Several batteries as one device | <span class="mk-no">No</span> (comparison dashboard) | <span class="mk-no">No</span> | <span class="mk-no">No</span> | <span class="mk-yes">Yes</span> |
| Battery health estimate | <span class="mk-yes">Yes</span> | <span class="mk-no">No</span>¹ | <span class="mk-no">No</span>¹ | <span class="mk-no">No</span>¹ |
| Install | HACS custom | HACS custom | HACS custom | HACS custom |
| Latest release | [v1.11.0](https://github.com/taurgis/has-marstek-local-api/releases) (2026-10-28) | v0.2.3 (2026-10-27) | None published | 1.2.0.rc7 (2025-11-03) |
| Recent activity | Active (Oct 2026) | Active | Active (Sept 2026) | No commits since Nov 2025. An active fork exists (below) |
| License | MIT | MIT | MIT | None (no license file) |

### What to know about each

**Marstek (vendor).** Published by Marstek's own GitHub organization. Marstek also has a [pull request open to add a Marstek integration to Home Assistant itself](https://github.com/home-assistant/core/pull/179635). That pull request is still a draft and, in its current form, adds sensors only. Its HACS version does more, including a factory-reset button, which this integration leaves out on purpose. If it is merged, Marstek support will come with Home Assistant out of the box.

**yuyuki.** Focused on one model, Venus E, with mode, passive, and schedule control. A good fit if that's all you have and you want a smaller integration.

**jaapp.** Adds several batteries together into a "system" device. The original is still a release candidate and has had no commits since November 2025. [Flodesirat's fork](https://github.com/Flodesirat/ha-marstek-local-api) keeps the system device and is actively maintained (GPL-3.0).

¹ [Marstek Capacity Monitor](https://github.com/satscan1/marstek-capacity-monitor), in the HACS default store, measures capacity next to any of these integrations.

**This integration.** The broadest model support (the only one here that lists Venus E mini), firmware-aware controls, a battery health estimate, and tests that run Marstek's own firmware in an emulator. In exchange, it is one entry per battery with no combined system device. See [Limitations](limitations.md).

> [!WARNING] Only one at a time
> This integration, Marstek's vendor integration, and the proposed built-in one all install under the same name, `marstek`. Home Assistant can load only one. Remove one completely, restart, then install the other. A custom integration always wins over a built-in one with the same name, so if the built-in one ships, remove this one first to try it.

## Modbus integrations

These need a wired connection, usually an RS485-to-Ethernet adapter, but they don't depend on the Open API at all. That makes them the main option for **Venus E 2.0**.

### Omnibattery

- **Repository:** [ffunes/Omnibattery](https://github.com/ffunes/Omnibattery)
- **Models:** Venus A, C (v2/v3), D, E (v2/v3), plus batteries from other brands.
- **What it does:** a full energy manager with zero-export control, time slots, price-based charging, and up to 10 batteries.
- **Install:** HACS custom repository. **License:** GPL-3.0. **Latest release:** 1.5.0 (2026-10-04).

### Marstek Venus Modbus

- **Repository:** [ViperRNMC/marstek_venus_modbus](https://github.com/ViperRNMC/marstek_venus_modbus)
- **Models:** Venus A, D, E v1, v2, and v3.
- **What it does:** force charge and discharge, power and charge limits, and schedules.
- **Install:** HACS custom repository. **License:** MIT. **Latest release:** 2026.10.1 (2026-10-26).

### Marstek Modbus Suite

- **Repository:** [sphings79/marstek-modbus-suite](https://github.com/sphings79/marstek-modbus-suite)
- **Models:** Venus A, D, E v3. Its README marks E v1 and v2 as unverified.
- **What it does:** control plus a dashboard panel.
- **Install:** HACS default store. **License:** MIT. **Latest release:** 3.1.3 (2026-10-28).

Omnibattery replaces the earlier Marstek Venus Energy Manager by the same author.

## MQTT, cloud, and Bluetooth

### hm2mqtt (MQTT)

- **Repository:** [tomquist/hm2mqtt](https://github.com/tomquist/hm2mqtt)
- **Covers:** B2500, Venus A, C, D, E, E mini (beta), Jupiter, and CT meters, with sensors and control.
- **Install:** a Home Assistant add-on or Docker container, not HACS. Venus and Jupiter keep talking to Marstek's cloud. The companion hame-relay mirrors that data locally using your Marstek account, so this route still depends on the cloud. [marsrelay](https://github.com/tomquist/marsrelay) is a newer offline option.

### Marstek Cloud

- **Repository:** [DoctaShizzle/marstek_cloud](https://github.com/DoctaShizzle/marstek_cloud)
- **Covers:** sensors only, read from Marstek's servers.
- **Install:** manual copy. No commits since October 2025.

### Marstek BLE (Bluetooth)

- **Repository:** [Cappa83/marstek-ble-ha](https://github.com/Cappa83/marstek-ble-ha)
- **Covers:** CT002 and Venus E 3.0, read-only.
- **Install:** HACS custom repository. **License:** Apache-2.0.

### AstraMeter

[AstraMeter](https://github.com/tomquist/AstraMeter) (formerly b2500-meter) is not an integration, but it is useful next to one: it pretends to be a Marstek CT meter, so a battery in self-adaptation mode can follow a meter you already have in Home Assistant. This project uses it to test against real firmware.

## Which one should I use?

- **Venus A, D, E 3.0, or E mini, and you want no extra hardware:** an Open API integration. Pick this one for the widest model support, firmware-aware controls, and battery health. Pick the vendor one if you prefer Marstek's own code.
- **Venus E 2.0:** a Modbus integration, or hm2mqtt over MQTT. No Open API integration supports it safely. On firmware 158, one owner got control working from their own script with commands spaced about 30 seconds apart, but not yet reliably unattended ([#121](https://github.com/taurgis/has-marstek-local-api/issues/121)).
- **Venus C:** an Open API integration works, but read the [Venus C caveat](supported-devices.md#venus-c-caveat) first.
- **Venus E mini:** this is the only Open API integration above that lists it. hm2mqtt has beta support over MQTT.
- **B2500 or Jupiter:** hm2mqtt. There is no local Open API for those.
- **You want an automatic energy manager rather than building automations yourself:** Omnibattery.
- **You want several batteries combined into one device:** Flodesirat's maintained fork of jaapp's integration, or Omnibattery over Modbus.

Decided on this one? [Check your battery](supported-devices.md), then follow [Getting started](getting-started.md). It takes about 20 minutes.
