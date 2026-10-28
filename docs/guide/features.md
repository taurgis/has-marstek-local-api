---
title: Features
description: Everything the Marstek integration for Home Assistant can do, grouped by task, with what each feature needs.
---

# Features

Grouped by what you are trying to do. Each item links to the page with the details.

## See what the battery is doing

- **State of charge, battery power, and status** (charging, discharging, idle). [Entities](../entities.md)
- **Grid power** at the meter, in total and per phase, when a CT meter is connected. [Entities](../entities.md)
- **Solar (PV) power, voltage, and current** per input, on Venus A and Venus D. Venus D reports up to four inputs. [Entities](../entities.md#pv-channel-sensors-pv1-to-pv4)
- **Lifetime energy totals** for battery charge and discharge, solar, and, when the CT meter reports them, grid import and export. [Energy dashboard](../energy_dashboard.md)
- **Diagnostics**: a **Restarts** counter that tells real restarts from Wi-Fi dropouts, plus Wi-Fi signal, CT connection, and firmware version sensors you can turn on. [Why a battery goes offline](../firmware-resets.md)
- **Battery details** such as temperature and remaining capacity. Off by default, because the request behind them has been linked to resets on some firmware. [Entities](../entities.md#diagnostic-sensors-disabled-by-default)

## Control the battery

| You want to… | Use | Notes |
|---|---|---|
| Let the battery run self-consumption | **Operating mode** select: **Auto** | The battery follows its grid meter. |
| Use Marstek's AI mode | **Operating mode** select: **AI** | Marstek's own optimization. |
| Charge or discharge at a set power, now | **Charge battery** / **Discharge battery** device actions | Runs until **Stop**. |
| Charge or discharge for a fixed time | `marstek.set_passive_mode` | Negative power charges. Ends on its own. Not on Venus E mini 301. |
| Charge or discharge on a weekly timetable | `marstek.set_manual_schedule(s)` | Ten slots (six on Venus E mini). Stored on the battery. |
| Switch to UPS (backup) mode | **Operating mode** select: **UPS** | Firmware 150+ on Venus A, D, E 3.0, E mini. |
| Limit how deep the battery discharges | **Depth of discharge** number | Firmware 150+ on Venus A, D, E 3.0. Venus E mini. |
| Turn off the panel LED or Bluetooth | **Panel LED** / **Bluetooth advertising** switches | Firmware 150+ on Venus A, D, E 3.0. Venus E mini. |
| Refresh readings right now | `marstek.request_data_sync` | Handy in an automation after a change. |

Copy-paste examples are in [Automations and services](../services.md).

## Fit into Home Assistant

- **Energy dashboard** sources for grid and solar. [Energy dashboard](../energy_dashboard.md)
- **Device actions** in the visual automation editor, no YAML needed.
- **Repairs**: Home Assistant tells you when the battery is unreachable for a while, or when its firmware is known to be fragile. [Repairs](../repairs.md)
- **Diagnostics download** with MAC addresses and the configured IP address redacted, for bug reports. [Troubleshooting](../troubleshooting.md#what-to-include-in-a-bug-report)
- **Options** for polling speed, timeouts, and default charge and discharge power, changeable without reinstalling. [Options](../options.md)

## Stay connected

- **Discovery** finds batteries on your network, including on the common custom ports 30001–30004 and 30030.
- **IP following**: a background scan notices when the router gives the battery a new address and updates Home Assistant.
- **Firmware following**: after a firmware update, new controls appear (or unsafe ones disappear) by themselves.
- **Gentle polling**: one request at a time by default, fast data every 30 seconds, solar every 60 seconds, diagnostics every 5 minutes and only when you use them.

## Battery health (optional)

- **Usable capacity** and **state of health**, estimated from long charge and discharge runs.
- A **sidebar dashboard** and two **cards** for your own dashboards.
- Link a **smart plug** to measure round-trip efficiency and standby draw.

The estimate adds no traffic to the battery. The sidebar dashboard and smart-plug linking stay off until you turn them on. [Battery health](../usable-capacity.md)

## Use cases

- **Dynamic tariffs.** Drive manual schedules or passive mode from a price integration (Nord Pool, Tibber, EPEX) so the battery charges on cheap hours.
- **Follow the house load.** Passive mode takes a watt setpoint, so an automation can track consumption or a solar forecast.
- **Peak shaving.** Per-phase grid power lets an automation discharge before the house sets a new peak.
- **Make room for an EV or heat pump.** Stop discharging while the car charges.
- **Alerts without a cloud.** Low charge, high temperature, or a dropped Wi-Fi link, even when Marstek's servers are down.

Ready? [Check your battery](supported-devices.md), then follow [Getting started](getting-started.md).
