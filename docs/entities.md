# Entities

All entities are **coordinator-backed** (no per-entity polling). Names below match the English UI strings.

> Note: Some entities are **Diagnostic** and **disabled by default** (can be enabled in the entity registry).

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
| Battery temperature | `bat_temp` | °C | — | Enabled |
| Total power | `em_total_power` | W | — | Enabled |
| Phase A power | `em_a_power` | W | — | Enabled |
| Phase B power | `em_b_power` | W | — | Enabled |
| Phase C power | `em_c_power` | W | — | Enabled |
| Total solar energy | `total_pv_energy` | Wh | — | Enabled |
| Total grid output energy | `total_grid_output_energy` | Wh | — | Enabled |
| Total grid input energy | `total_grid_input_energy` | Wh | — | Enabled |
| Total load energy | `total_load_energy` | Wh | — | Enabled |

### PV channel sensors (PV1–PV4)

Created when the device reports those values (typically Venus A/D with PV channels).

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
| Operating mode | `operating_mode` | Auto, AI, Manual, Passive |

> Manual and Passive require extra parameters and are set via services (see [Services](services.md)).

## Device grouping

Entities are grouped under one device, and unique IDs remain stable across IP changes.

<img src="screenshots/device-details-venusa.png" alt="Device details (Venus A)" width="560" />
