# Entities

All entities are **coordinator-backed** (no per-entity polling). Names below match the English UI strings.

Capability-gated entities (PV channels, UPS, SYS DOD/BLE/LED) are **created only when the firmware profile supports them**. Unsupported features are omitted from the device page rather than left permanently unavailable. After a firmware update that unlocks or removes those capabilities, the scanner reloads the config entry so the entity set matches the new profile.

Meter input/output energy sensors are created when `EM.GetStatus` (or the Rev 3.1 `ES.GetMode` fallback) actually reports those fields. Firmware `ver >= 150` on a known family scales the wire unit 0.1 Wh → Wh; older profiles leave a present value unscaled.

> Note: Some entities are **Diagnostic** and **disabled by default** (can be enabled in the entity registry).

> ⚠️ The `Bat.GetStatus` API call is suspected to trigger spontaneous device resets on some Marstek firmwares ([issue #14](https://github.com/taurgis/has-marstek-local-api/issues/14)). The entities that depend on it — Battery temperature (`bat_temp`), Battery remaining capacity (`bat_capacity`), Battery rated capacity (`bat_rated_capacity`), Charge permission (`bat_charg_flag`) and Discharge permission (`bat_dischrg_flag`) — are therefore **disabled by default on new installations**, and the integration only sends `Bat.GetStatus` while at least one of them is enabled. Enabling any of them resumes the call automatically on Control firmware **150+**.
>
> **Reset-prone firmware (Control generation below 150):** those five entities are **omitted** and `Bat.GetStatus` is never sent, even if they were previously enabled. After a firmware update to 150+, the scanner reloads the config entry so they can be created again (still disabled by default).
>
> **Upgrading an existing 150+ install?** Entities that were already registered stay enabled — upgrades never disable entities on firmware that can safely opt in. To stop `Bat.GetStatus` on a 150+ installation, disable the five entities above manually once (device page → entity → ⚙️ → *Enabled* off).

## Sensors

| Entity name | Key | Unit | Category | Default |
|---|---|---|---|---|
| Battery level | `battery_soc` | % | — | Enabled |
| Battery power | `battery_power` | W | — | Enabled |
| On-grid power | `ongrid_power` | W | — | Enabled (if supported) |
| Off-grid power | `offgrid_power` | W | — | Enabled (if supported) |
| PV power | `pv_power` | W | — | Enabled (if supported) |
| Device mode | `device_mode` | — | — | Enabled |
| Battery status | `battery_status` | — | — | Enabled |
| Total power | `em_total_power` | W | — | Enabled |
| Phase A power | `em_a_power` | W | — | Enabled |
| Phase B power | `em_b_power` | W | — | Enabled |
| Phase C power | `em_c_power` | W | — | Enabled |
| Meter input energy | `em_input_energy` | Wh | — | Enabled (if reported) |
| Meter output energy | `em_output_energy` | Wh | — | Enabled (if reported) |
| Total solar energy | `total_pv_energy` | Wh | — | Enabled |
| Total grid output energy | `total_grid_output_energy` | Wh | — | Enabled |
| Total grid input energy | `total_grid_input_energy` | Wh | — | Enabled |
| Total load energy | `total_load_energy` | Wh | — | Enabled |

### PV channel sensors (PV1–PV4)

Created when the device reports those values (typically Venus A/D with PV channels).
Values are stored in watts. Channel 1 is deciwatts on observed firmware
(including 148.3, 149, and 150.9); the parser divides by 10, matching 1.0.0.
That PV1 scale is independent of the solar-energy ×10 used for [#35](https://github.com/taurgis/has-marstek-local-api/issues/35).
Do not skip the ÷10 ([#57](https://github.com/taurgis/has-marstek-local-api/issues/57)).

| Entity name | Key | Unit | Category | Default |
|---|---|---|---|---|
| PV1 power | `pv1_power` | W | — | Enabled (if supported) |
| PV1 voltage | `pv1_voltage` | V | — | Enabled (if supported) |
| PV1 current | `pv1_current` | A | — | Enabled (if supported) |
| PV1 state | `pv1_state` | — | — | Enabled (if supported) |
| PV2 power | `pv2_power` | W | — | Enabled (if supported) |
| PV2 voltage | `pv2_voltage` | V | — | Enabled (if supported) |
| PV2 current | `pv2_current` | A | — | Enabled (if supported) |
| PV2 state | `pv2_state` | — | — | Enabled (if supported) |
| PV3 power | `pv3_power` | W | — | Enabled (if supported) |
| PV3 voltage | `pv3_voltage` | V | — | Enabled (if supported) |
| PV3 current | `pv3_current` | A | — | Enabled (if supported) |
| PV3 state | `pv3_state` | — | — | Enabled (if supported) |
| PV4 power | `pv4_power` | W | — | Enabled (if supported) |
| PV4 voltage | `pv4_voltage` | V | — | Enabled (if supported) |
| PV4 current | `pv4_current` | A | — | Enabled (if supported) |
| PV4 state | `pv4_state` | — | — | Enabled (if supported) |

### Diagnostic sensors (disabled by default)

| Entity name | Key | Unit | Category | Default |
|---|---|---|---|---|
| WiFi signal strength | `wifi_rssi` | dBm | Diagnostic | Disabled |
| Wi‑Fi IP address | `wifi_sta_ip` | — | Diagnostic | Disabled |
| Wi‑Fi gateway | `wifi_sta_gate` | — | Diagnostic | Disabled |
| Wi‑Fi subnet mask | `wifi_sta_mask` | — | Diagnostic | Disabled |
| Wi‑Fi DNS | `wifi_sta_dns` | — | Diagnostic | Disabled |
| Battery total capacity | `bat_cap` | Wh | Diagnostic | Disabled (if supported) |
| Battery temperature | `bat_temp` | °C | Diagnostic | Disabled |
| Battery remaining capacity | `bat_capacity` | Wh | Diagnostic | Disabled |
| Battery rated capacity | `bat_rated_capacity` | Wh | Diagnostic | Disabled |
| Device IP | `device_ip` | — | Diagnostic | Disabled |
| Device version | `device_version` | — | Diagnostic | Disabled |
| Wi‑Fi name | `wifi_name` | — | Diagnostic | Disabled |
| BLE MAC | `ble_mac` | — | Diagnostic | Disabled |
| Wi‑Fi MAC | `wifi_mac` | — | Diagnostic | Disabled |
| MAC address | `mac` | — | Diagnostic | Disabled |

## Binary sensors (diagnostic, disabled by default)

| Entity name | Key | Device class | Default |
|---|---|---|---|
| CT connection | `ct_connection` | connectivity | Disabled |
| Charge permission | `bat_charg_flag` | — | Disabled |
| Discharge permission | `bat_dischrg_flag` | — | Disabled |

## Select

| Entity name | Key | Options |
|---|---|---|
| Operating mode | `operating_mode` | Auto, AI, Manual, Passive. **UPS** is added only when the firmware profile reports `supports_ups`. |

| Availability | UPS on the select |
|---|---|
| Venus A/C/D/E and Venus E mini with `ver >= 150` | Yes |
| Venus E mini with a known `ver` below 150 | No (SYS still appears; UPS does not) |
| Unknown or unparseable `ver` | No |

> Auto, AI, and UPS (when listed) are selectable directly. Manual and Passive still require extra parameters and are set via services (see [Services](services.md)). `Set.Ver` and factory reset are not operating modes and are not offered here.

## Number (configuration)

Created only when the firmware profile reports SYS support. Venus A/C/D/E need firmware `ver >= 150`. Venus E mini needs a known integer `ver` (no 150 gate). Unknown or unparseable firmware omits these controls.

| Entity name | Key | Unit | Range | Category | Default |
|---|---|---|---|---|---|
| Depth of discharge | `depth_of_discharge` | % | 30–88, step 1 | Config | Enabled (if supported). Starts at 88 when nothing valid was restored. |

The Open API has no GET for DOD. Home Assistant restores the last value it successfully wrote. Changes made in the Marstek app or by another controller are not detected.

## Switch (configuration)

Same firmware/model availability as depth of discharge. There are no documented GET methods, so Home Assistant restores the last successful write. With no valid history the switch stays `unknown` until you set it.

| Entity name | Key | Category | Notes |
|---|---|---|---|
| Bluetooth advertising | `bluetooth_advertising` | Config | On enables advertising; off disables it. |
| Panel LED | `panel_led` | Config | On turns the panel LED on; off turns it off. |

`Set.Ver` and `Reset.Factory` are intentionally not exposed.

## Device grouping

Entities are grouped under one device, and unique IDs remain stable across IP changes.

Meter input/output energy sensors are created only when `EM.GetStatus` (or Rev 3.1 `ES.GetMode` fallback) reports those lifetime totals. They use the same BLE-MAC unique ID pattern as other sensors and are suitable for the Energy Dashboard (`device_class: energy`, `state_class: total_increasing`, native unit `Wh`).

<img src="screenshots/device-details-venusa.png" alt="Device details (Venus A)" width="560" />
