---
title: Features
description: Everything the Marstek integration for Home Assistant can do, grouped by task, with what each feature needs.
---

# Features

Grouped by what you are trying to do. Each item links to the page with the details. Unfamiliar terms are explained in the [glossary](glossary.md).

## See what the battery is doing

- **State of charge, battery power, and status** (charging, discharging, idle). [Entities](../entities.md)
- **Grid power** at the meter, in total and per phase, when a CT meter (the clamp-on meter at your fuse box) is connected. [Entities](../entities.md)
- **Solar (PV) power, voltage, and current** per input, on Venus A and Venus D. Venus D reports up to four inputs. [Entities](../entities.md#pv-channel-sensors-pv1-to-pv4)
- **Lifetime energy totals** for battery charge and discharge, solar, and, when the CT meter reports them, grid import and export. [Energy dashboard](../energy_dashboard.md)
- **Diagnostics**: a **Restarts** counter that tells real restarts from Wi-Fi dropouts. You can also turn on sensors for Wi-Fi signal, CT meter connection, and firmware version. [Why a battery goes offline](../firmware-resets.md)
- **Battery details** such as temperature and remaining capacity. Off by default: reading them means asking the battery for extra data, and that request has been linked to resets on some firmware. [Entities](../entities.md#diagnostic-sensors-disabled-by-default)

## Control the battery

Names that start with `marstek.` are actions you can call from an automation or script. Home Assistant used to call these *services*. The other items appear on the battery's device page or in the automation editor.

| You want to… | Use | Notes |
|---|---|---|
| Let the battery run self-consumption | **Operating mode** select: **Auto** | The battery follows its grid meter. |
| Use Marstek's AI mode | **Operating mode** select: **AI** | Marstek's own optimization. |
| Charge or discharge at a set power, now | **Charge battery** / **Discharge battery** device actions | Runs until **Stop**. |
| Charge or discharge for a fixed time | `marstek.set_passive_mode` | Negative power charges. Ends on its own. Not on Venus E mini 301. |
| Charge or discharge on a weekly timetable | `marstek.set_manual_schedule(s)` | Ten slots (six on Venus E mini). Stored on the battery. |
| Switch to UPS (backup) mode | **Operating mode** select: **UPS** | Venus A, D, and E 3.0 on firmware 150 or newer. Venus E mini on firmware 301. |
| Limit how deep the battery discharges | **Depth of discharge** number | Venus A, D, and E 3.0 on firmware 150 or newer. Venus E mini. |
| Turn off the panel LED or Bluetooth | **Panel LED** / **Bluetooth advertising** switches | Venus A, D, and E 3.0 on firmware 150 or newer. Venus E mini. |
| Refresh readings right now | `marstek.request_data_sync` | Handy in an automation after a change. |

Copy-paste examples are in [Automations and services](../services.md).

## Fit into Home Assistant

- **Energy dashboard** sources for the battery and solar, and for the grid when the CT meter reports totals. [Energy dashboard](../energy_dashboard.md)
- **Device actions** in the visual automation editor, no YAML needed.
- **Repairs**: Home Assistant tells you when the battery is unreachable for a while, or when its firmware is known to be fragile. [Repairs](../repairs.md)
- **Diagnostics download** for bug reports. It hides the battery's MAC addresses and its configured IP address, so you can share it. [Troubleshooting](../troubleshooting.md#what-to-include-in-a-bug-report)
- **Options** for how often Home Assistant asks for data, how long it waits for an answer, and the default charge and discharge power. You can change them without reinstalling. [Options](../options.md)

## Stay connected

- **Discovery** finds batteries on your network. It also checks the custom ports owners often pick (30001–30004 and 30030), not only the default 30000.
- **IP following**: a background scan notices when the router gives the battery a new address and updates Home Assistant.
- **Firmware following**: after a battery firmware update, new controls appear (or unsafe ones disappear) by themselves.
- **Gentle polling**: Home Assistant asks the battery one thing at a time by default. It reads live power every 30 seconds and solar every 60 seconds. Diagnostics are read every 5 minutes, and only when you use them.

## Battery health (optional)

- **Usable capacity** and **state of health**, estimated from long charge and discharge runs.
- A **sidebar dashboard** and two **cards** for your own dashboards.
- Link a **smart plug** to measure round-trip efficiency and standby draw.

The estimate adds no traffic to the battery. The sidebar dashboard and smart-plug linking stay off until you turn them on. [Battery health](../usable-capacity.md)

## Use cases

- **Dynamic tariffs.** Let an electricity price integration (Nord Pool, Tibber, EPEX) set manual schedules or passive mode, so the battery charges in cheap hours.
- **Follow the house load.** Passive mode takes a target power in watts, so an automation can follow consumption or a solar forecast.
- **Peak shaving.** Some tariffs charge for your highest power peak. Per-phase grid power lets an automation discharge the battery before the house sets a new peak.
- **Make room for an EV or heat pump.** Stop discharging while the car charges.
- **Alerts without a cloud.** Low charge, high temperature, or a dropped Wi-Fi link, even when Marstek's servers are down.

Ready? [Check your battery](supported-devices.md), then follow [Getting started](getting-started.md).
