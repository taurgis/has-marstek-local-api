# Marstek Home Assistant Integration

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.10%2B-blue.svg)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

Monitor and control Marstek batteries from Home Assistant, on your own network. See what each pack still holds, compare every battery on one screen, and charge or discharge them from automations.

This is an independent community project. It is not affiliated with or endorsed by Marstek.

## See it

**Battery health** in the sidebar. **Your batteries** totals usable energy, energy stored now, battery power, and power at a linked plug. Each battery has its own card: Venus E already has an estimate, Venus A is still measuring.

<img src="docs/screenshots/battery-health-dashboard-desktop.png" alt="Battery health dashboard. Your batteries compares Venus A and Venus E on one kWh scale. Venus E is Healthy at 4.86 kWh, 95 percent of its 5.12 kWh rating, with a weekly capacity chart." width="840" />

The same cards drop onto any dashboard. **Capacity** is the weekly chart, **Runs** is every 20-point charge or discharge (with a CSV download), and **Energy** is lifetime AC energy plus solar and the backup socket where the battery reports them.

<img src="docs/screenshots/battery-health-runs-desktop.png" alt="Runs tab listing charge and discharge runs, each compared with the median, and a Download runs CSV link." width="840" />

<img src="docs/screenshots/battery-health-energy-desktop.png" alt="Energy tab. Venus E shows lifetime energy charged from AC and delivered to AC. Venus A also shows solar and backup-socket energy." width="840" />

<img src="docs/screenshots/battery-health-mobile.png" alt="The same Battery health dashboard on a phone, with Your batteries in two columns and the cards stacked." width="280" />

The device page is where sensors, operating mode, and the disabled-by-default diagnostics live. Automations can charge, discharge, or stop the battery with a device action.

<img src="docs/screenshots/device-details.png" alt="Marstek device page in Home Assistant with battery sensors and controls." width="420" />
<img src="docs/screenshots/automation-action.png" alt="Automation editor with a Marstek device action to charge, discharge, or stop the battery." width="420" />

The full walkthrough, including the card picker, the visual editor, and the smart-plug options, is the [visual tour](docs/screenshots.md).

## Quick start

1. In the Marstek app, enable **Open API** on the battery. Home Assistant has to be on the same network. The usual port is **30000**.
2. Install the integration with [HACS](#installation) (or copy it in by hand) and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**, search for **Marstek**, and pick the discovered battery. If nothing is found, enter the IP address and port.
4. Optional: open the device, choose **Configure → Battery health dashboard**, and turn on **Show Battery health in the sidebar**. To measure the real round trip, also open **Energy meter (optional)** and link a smart plug. Both are in the [visual tour](docs/screenshots.md).

## Installation

### HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add this repository's URL and choose **Integration**.
4. Search for **Marstek** and install it.
5. Restart Home Assistant.

### Manual installation

Copy `custom_components/marstek` into your Home Assistant config as `config/custom_components/marstek`, then restart.

```bash
cd /config
mkdir -p custom_components
cp -r /path/to/has-marstek-local-api/custom_components/marstek custom_components/
```

### Removing the integration

1. Go to **Settings → Devices & services**.
2. Open **Marstek**.
3. On the device, open the three-dot menu and choose **Delete**. Repeat for each battery. Each battery is its own config entry.
4. Deleting the entry removes its device, its entities and their history, releases the UDP socket, and clears Repairs issues that entry raised. The network scanner stops once the last entry is gone.

To remove the integration as well:

- **HACS**: **HACS → Integrations → Marstek →** three-dot menu **→ Remove**, then restart Home Assistant.
- **Manual install**: delete `config/custom_components/marstek/` and restart.

**Open API** can stay enabled in the Marstek app. A battery you delete while it is still on the network is offered again under **Settings → Devices & services**. Choose **Ignore** on that discovery card to leave it out.

More detail: [Installation](docs/installation.md).

## What you can do

- **Battery level, power, and status**, plus operating mode (Auto, AI, Manual, Passive, and UPS on firmware that allows it).
- **Usable capacity** and **battery state of health**, once the battery has completed three long charge or discharge runs. Unknown until then. See [Battery health](docs/usable-capacity.md).
- **Battery health cards.** **Marstek battery health** shows one pack against its rating, with plain-language insights and its runs (downloadable as CSV). **Marstek battery fleet** compares every battery on one kWh scale. Add them from **Add card**. The integration registers the cards, so there is no Lovelace resource to add by hand.
- **Battery health dashboard.** **Configure → Battery health dashboard** adds an optional **Battery health** sidebar page (Dutch: **Batterijgezondheid**). It starts off.
- **A linked smart plug.** **Configure → Energy meter (optional)** accepts a HomeWizard, Shelly, or any other meter and then shows live plug watts, round-trip efficiency at the plug (standby included), and standby draw.
- **Solar** on Venus A and Venus D, **on-grid and off-grid power**, per-phase meter power, and lifetime energy for the Energy dashboard.
- **Charge, discharge, and stop** from the automation editor, plus `marstek.set_passive_mode` and manual schedules.
- **Depth of discharge, Bluetooth advertising, and the panel LED** on firmware that accepts those writes.
- **Diagnostics and Repairs** for connection loss and firmware warnings.

**Battery temperature** and the other battery-detail sensors start disabled, because the request behind them has been suspected of resetting some batteries ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)). Enable them from the device page when you want them. See [Entities](docs/entities.md).

## Use cases

- **Dynamic tariff arbitrage.** Drive `marstek.set_manual_schedule` from a price integration (Nord Pool, Tibber, EPEX) so the battery charges on cheap hours and discharges on expensive ones.
- **Closed-loop power.** `marstek.set_passive_mode` sets a watt setpoint with a countdown, so an automation can follow house load or a solar forecast.
- **Solar self-consumption.** On Venus A and Venus D the PV sensors feed the Energy dashboard. See [Energy dashboard](docs/energy_dashboard.md).
- **Grid-peak avoidance.** Meter sensors expose import and export per phase, so an automation can discharge when the house would otherwise set a new peak.
- **Make room for an EV or a heat pump.** **Charge**, **Discharge**, and **Stop** are device actions in the automation editor.
- **Alerts without a cloud account.** State of charge, temperature, firmware, and Wi-Fi RSSI stay available when Marstek's servers do not.

Copy-paste YAML is in [Automations and services](docs/services.md).

## Requirements

| Requirement | Details |
|-------------|---------|
| Home Assistant | Core 2025.10.0 or newer |
| Network | Same LAN segment as the battery |
| Marstek app | **Open API** enabled |
| UDP port | 30000 by default. The port is configurable per device in the Marstek app. Discovery also probes 30001–30004 and 30030 |

> **Warning**: Venus **E2.0** is not compatible. Polling it can disconnect the device from CT003.

> **Warning**: **Venus C** shares one Wi-Fi receive channel between the Local API and the device's own meter, so polling can stop Self-consumption (Auto) from charging on excess solar ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)). Use slow polling intervals, and read grid power from the meter directly if Auto mode stalls. See [troubleshooting](docs/troubleshooting.md#venus-c-stops-charging-from-excess-solar-in-auto-mode).

## Options

Open the device and choose **Configure**. Five sections are there: **Polling**, **Network & requests**, **Power / behavior**, **Battery health dashboard**, and **Energy meter (optional)**. Only Polling starts expanded.

Day to day, the two you are most likely to open are:

- **Show Battery health in the sidebar**, under **Battery health dashboard**. Off by default. Turning it off hides the sidebar entry and leaves the dashboard in place.
- **Energy into the battery** and **Energy out of the battery**, under **Energy meter (optional)**. On a HomeWizard socket those are **Energy import** and **Energy export**. Enable **Energy export** on the socket's device page first. Fill both or leave both empty.

<img src="docs/screenshots/options-battery-health-dashboard.png" alt="Configure dialog with Show Battery health in the sidebar turned on." width="640" />

<img src="docs/screenshots/options-energy-meter.png" alt="Configure dialog with Energy into the battery and Energy out of the battery set to total-energy sensors." width="640" />

Polling intervals, parallel requests, charge and discharge defaults, and the socket limit are documented in [Options](docs/options.md).

## Documentation

- [Documentation index](docs/README.md) — every page, grouped by what you are trying to do.
- [Visual tour](docs/screenshots.md)
- [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [Options](docs/options.md)
- [Battery health](docs/usable-capacity.md) · [Energy dashboard](docs/energy_dashboard.md) · [Automations and services](docs/services.md)
- [Entities](docs/entities.md) · [Repairs](docs/repairs.md) · [Troubleshooting](docs/troubleshooting.md)

## Supported devices

Capabilities depend on the model and the firmware version. A feature the battery cannot do is left off the device page.

| Device | Status | Notes |
|--------|--------|-------|
| Venus A | Supported, with solar | SYS and UPS from firmware 150 |
| Venus D | Supported, with solar | SYS and UPS from firmware 150 |
| Venus C | Supported, with a caveat | No SYS/UPS. Polling can stall Auto-mode charging on the shared Wi-Fi channel ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)) |
| Venus E 3.0 | Supported | SYS and UPS from firmware 150. Ten manual schedule slots (0–9) |
| Venus E mini | Supported | Reports `VNSEM-0`. Six manual schedule slots (0–5). Add it by IP |
| Venus E 2.0 | Not compatible | Can disconnect the device from its meter |
| Other Open API devices | May work, untested | Treated as an unknown model, with the safest feature set |

Per-firmware energy scaling, schedule limits, and the Docker test matrix are in [Development](docs/development.md#test-matrix). If your model or firmware is missing, [open a firmware coverage issue](https://github.com/taurgis/has-marstek-local-api/issues/new?template=firmware_coverage.yml).

## Compared with other community integrations

Last reviewed **2026-09-20**. Activity and release facts were taken from each repository on that date.

| Integration | Transport | Summary | Strengths | Tradeoffs |
|---|---|---|---|---|
| **This integration** ([taurgis/has-marstek-local-api](https://github.com/taurgis/has-marstek-local-api)) | Local UDP (Open API Rev 3.1) | Local polling with a scanner that tracks IP and firmware changes, plus battery-health cards. | Per-port UDP sockets, tiered polling, firmware-gated capabilities, device actions, diagnostics and Repairs. | One config entry per device. Custom HACS repository only. |
| **Marstek Local API** ([jaapp/ha-marstek-local-api](https://github.com/jaapp/ha-marstek-local-api)) | Local UDP (Open API) | Multi-battery integration with a virtual **Marstek System** device that aggregates fleet metrics. | Fleet aggregation under one config entry, wide sensor set, diagnostics. | No commits since **Nov 2025** and still published as a release candidate (`1.2.0.rc7`). |
| **MarstekEnergy (vendor)** ([marstekEnergy/ha_marstek](https://github.com/marstekEnergy/ha_marstek)) | Local UDP via `aiomarstek` | Staging copy of the vendor's in-review core integration. | Vendor-authored. Would ship built in if merged. | Core PR [#179635](https://github.com/home-assistant/core/pull/179635) is still open. Sensors only (no mode control or schedules). |
| **Omnibattery** ([ffunes/Omnibattery](https://github.com/ffunes/Omnibattery)) | Modbus TCP/RTU | Multi-brand battery manager, including Venus E v2, which the Open API does not cover. | Peak shaving and time slots. Actively maintained. | Needs a Modbus bridge on some models. GPL-3.0. |

> **Heads-up on the `marstek` domain**: Home Assistant Core PR [#179635](https://github.com/home-assistant/core/pull/179635) adds `homeassistant/components/marstek`, the same domain this custom component uses. Custom components take precedence over built-in ones, so an installed copy of this integration keeps working if that PR merges. Remove it first if you want to try the built-in one.

## Automations

Every service targets a device with `device_id`, except `marstek.request_data_sync`, where leaving it out refreshes every battery.

| Service | What it does |
|---------|----------------|
| `marstek.set_passive_mode` | Hold a charge or discharge setpoint for a number of seconds. Negative power charges. |
| `marstek.set_manual_schedule` | Write one schedule slot. |
| `marstek.set_manual_schedules` | Write several slots from a YAML list. |
| `marstek.clear_manual_schedules` | Clear the slots. |
| `marstek.request_data_sync` | Refresh now. |

**Charge**, **Discharge**, and **Stop** are also device actions in the automation editor. Parameters, YAML examples, and the screenshots are in [Automations and services](docs/services.md).

## Known limitations

These are constraints of the Open API or of this integration's design.

- **Polling only.** The battery pushes nothing. A change made in the Marstek app shows up on the next poll (30 seconds by default for live power).
- **No authentication.** Open API is plain UDP. Anyone on the same LAN can read and write the battery. The integration has no password to refresh. Its reauth step only updates a changed IP address.
- **One request at a time per battery.** A batch such as clearing every schedule slot takes one round trip per slot.
- **LAN only.** Discovery uses UDP broadcast. A battery on another VLAN needs its IP entered by hand.
- **Depth of discharge, Bluetooth advertising, and the panel LED cannot be read back.** Home Assistant shows the last value the battery accepted.
- **Passive and Manual are services, not choices on the mode dropdown.** Both need a power, a duration, or time slots.
- **One device per config entry.** Several batteries are several entries. The fleet card is the on-screen comparison.
- **Venus E 2.0 is not supported.**
- **Features the firmware lacks are omitted.** SYS and UPS need firmware 150 or newer on Venus A, D, and E. PV sensors exist on Venus A and Venus D.
- **Battery-detail entities start off.** See [Entities](docs/entities.md).
- **Energy totals stay monotonic.** When firmware reports a lower lifetime total than a moment ago, the integration keeps the last plausible value. A genuine counter reset on the device needs a statistics correction in Home Assistant.

## Troubleshooting

- **The battery is not discovered.** Confirm **Open API** is on, that Home Assistant is on the same network, and that UDP port 30000 is reachable. You can always type the IP and port.
- **Entities go unavailable.** Short UDP failures keep the last values until **Failures before unavailable** (default 3) is reached. Then check the device, and try `marstek.request_data_sync`.
- **Panel LED, Bluetooth, depth of discharge, or UPS is missing.** Those controls depend on firmware. The device page omits what the battery will reject. See [Troubleshooting](docs/troubleshooting.md).
- **Venus C stops charging from excess solar in Auto mode.** Polling shares the meter's Wi-Fi channel. See [Troubleshooting](docs/troubleshooting.md#venus-c-stops-charging-from-excess-solar-in-auto-mode).
- **The health card says Still measuring.** It needs three 20-point runs in one direction. See [Why does my card say Still measuring?](docs/usable-capacity.md#why-does-my-card-say-still-measuring).

## For developers

Tests, the firmware emulator matrix, the repository map, and the Changesets release flow are in [Development](docs/development.md). The protocol note is [Open API Rev 3.1](docs/marstek_device_openapi.MD).

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

Protocol documentation: [Marstek Open API specification](docs/marstek_device_openapi.MD).

## Disclaimer

This integration is provided as is, without warranty. The authors are not responsible for damage to your devices or for data loss. This project is not affiliated with, endorsed by, or connected to Marstek.
