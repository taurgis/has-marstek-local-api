# Marstek Device Open API (Rev 3.1)

Local reference for the Marstek Device Open API **Rev 3.1**, mapped to the behavior this Home Assistant integration actually ships.

Vendor source: [Marstek Device Open API Rev 3.1 PDF](https://static-eu.marstekenergy.com/ems/resource/agreement/MarstekDeviceOpenApi.pdf) (same family also published at [eu.hamedata.com](https://eu.hamedata.com/ems/resource/agreement/MarstekDeviceOpenApi.pdf)). There is no separate HTML API.

> **Disclaimer:** The Local API is provided "as is" for local use only. Use at your own risk. Marstek is not liable for any damages, data loss, or legal issues caused by your use of the API. You are responsible for lawful and appropriate use.

> **Vendor PDF warning:** The official PDF is poorly typeset. JSON examples are shuffled across chapters, punctuation and capitalization are inconsistent, and several samples contain errors such as `set_result: ture`. **Field tables and shipped/tested behavior take precedence over malformed examples.** This reference therefore uses Markdown tables for commands, parameters, units, and firmware gates instead of copying those examples.

> **Protocol vs Home Assistant UI:** Chapter 4 of Rev 3.1 documents which *methods a device may accept*. This integration omits unsupported capability-gated entities rather than leaving them permanently unavailable, and it intentionally does **not** expose every documented write (see [Set.Ver and Reset.Factory](#setver-and-resetfactory-not-in-the-default-ui)). Unknown or unparseable discovery `ver` stays **legacy-safe**: no SYS/UPS writes, and energy scaling only where the firmware profile is known.

---

## I. Preface

This document introduces the Open API for Marstek devices. It is available to device owners and enables integration with third-party systems.

Marstek also offers an official mobile app and cloud services. This Open API is for local LAN control.

---

## II. General Description

Marstek devices communicate with third-party systems over a Local Area Network (LAN). Before using this API:

- The Marstek device must be connected to the home network.
- Open API must be enabled in the Marstek mobile app.

Different models support only a subset of the commands below. Enabling Open API may disable some built-in app features to prevent command conflicts. Model and firmware support is in [Chapter 4](#iv-devices-and-firmware).

### 2.1 Protocol Format

JSON messages over UDP. Commands are queries, configuration writes, or a few special SYS commands.

#### Command format

| Property | Type | Description |
|----------|------|-------------|
| `id` | number or string | Client-chosen request identifier |
| `method` | string | Method name, for example `ES.GetStatus` |
| `params` | object | Method parameters (often includes instance `id`) |

Typical request shape:

| Field | Example |
|-------|---------|
| `id` | `1` |
| `method` | `"ES.GetStatus"` |
| `params.id` | `0` |

#### Device response format

| Property | Type | Description |
|----------|------|-------------|
| `id` | number or string | Echo of the client identifier |
| `src` | string | Source name of the device |
| `result` | object | Present on success |
| `error` | object | Present on failure |

#### Error codes

| Code | Message | Meaning |
|------|---------|---------|
| -32700 | Parse error | Invalid JSON |
| -32600 | Invalid Request | Not a valid request object |
| -32601 | Method not found | Method missing or not available on this firmware |
| -32602 | Invalid params | Invalid method parameters |
| -32603 | Internal error | Internal JSON-RPC error |
| -32000 to -32099 | Server error | Implementation-defined server errors |

Legacy firmware that does not implement SYS or UPS returns **-32601 Method not found** for those writes. That is expected, not a transport failure.

### 2.2 API over UDP

#### 2.2.1 First-time use

1. Power on the device.
2. Bind it in the Marstek app and join Wi-Fi or Ethernet.
3. Enable Open API in the app and set the UDP port. Default is **30000**. The spec recommends **49152–65535**.

The device then accepts UDP commands from the same LAN.

#### 2.2.2 Discovering devices

LAN discovery is a UDP broadcast of `Marstek.GetDevice` with `ble_mac` set to `"0"`.

| Direction | Field | Value |
|-----------|-------|-------|
| Request `method` | `Marstek.GetDevice` | |
| Request `params.ble_mac` | `"0"` | Discover all devices |
| Response `result.device` | string | Model name |
| Response `result.ver` | number | Firmware integer used as the profile gate |
| Response `result.ble_mac` | string | Bluetooth MAC (stable unique ID) |
| Response `result.wifi_mac` | string | Wi-Fi MAC |
| Response `result.wifi_name` | string | SSID |
| Response `result.ip` | string | Current IPv4 address |

The IP can also come from the Marstek app or the router. A static IP is recommended for long-term use.

This integration uses the configured IP at setup. A background scanner repeats broadcast discovery to update the config entry when the IP changes. It does **not** discover during coordinator polling.

---

## III. Components

### 3.1 Marstek

Basic product identity. Used for discovery and device info.

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `Marstek.GetDevice` | Locate devices / read identity | Discovery, scanner, `sw_version` from `ver` |

#### 3.1.1 Marstek.GetDevice

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `ble_mac` | string | `"0"` to discover all; a specific MAC targets one device |

**Result**

| Property | Type | Description |
|----------|------|-------------|
| `device` | string | Device model |
| `ver` | number | Firmware version integer |
| `ble_mac` | string | Bluetooth MAC |
| `wifi_mac` | string | Wi-Fi MAC |
| `wifi_name` | string | Wi-Fi name |
| `ip` | string | Device IP |

---

### 3.2 WiFi

Network diagnostics.

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `Wifi.GetStatus` | Station IP, RSSI, gateway, DNS | Slow-tier diagnostic sensors; sent only while a Wi-Fi entity is enabled. Method name is case-sensitive: `Wifi.GetStatus` answers, `wifi.GetStatus` does not. |

#### 3.2.1 Wifi.GetStatus

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |

**Result**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |
| `wifi_mac` | string | Wi-Fi MAC |
| `ssid` | string or null | Wi-Fi name |
| `rssi` | number | Signal strength, dBm |
| `sta_ip` | string or null | Device IP |
| `sta_gate` | string or null | Gateway |
| `sta_mask` | string or null | Subnet mask |
| `sta_dns` | string or null | DNS |

---

### 3.3 Bluetooth

Connection status only. Advertising on/off is a **SYS** write (`Ble.Adv`), not this GET.

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `BLE.GetStatus` | Bluetooth connection state and MAC | Not polled as a dedicated entity source |

#### 3.3.1 BLE.GetStatus

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |

**Result**

| Property | Type | Description |
|----------|------|-------------|
| `state` | string | Bluetooth state. Observed values include `connect` and `disconnect`. |
| `ble_mac` | string | Bluetooth MAC |

---

### 3.4 Battery

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `Bat.GetStatus` | Temperature, remaining/rated capacity, charge/discharge flags | Slow-tier; **disabled by default** because some firmwares reset on this call ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)) |

#### 3.4.1 Bat.GetStatus

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |

**Result**

| Property | Type | Unit | Description |
|----------|------|------|-------------|
| `id` | number | — | Instance ID |
| `soc` | number | % | Battery SoC (PDF type text is inconsistent; devices return a number) |
| `charg_flag` | boolean | — | Charging permission |
| `dischrg_flag` | boolean | — | Discharge permission |
| `bat_temp` | number or null | °C | Battery temperature |
| `bat_capacity` | number or null | Wh | Remaining capacity |
| `rated_capacity` | number or null | Wh | Rated capacity |

---

### 3.5 PV (Venus A/D only)

Rev 3.1 documents PV for **Venus A and Venus D only**. Venus C, Venus E, and Venus E mini do not expose PV. The integration skips `PV.GetStatus` and omits PV entities on non-PV families.

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `PV.GetStatus` | MPPT power, voltage, current, state | Medium-tier; up to four channels when reported |

#### 3.5.1 PV.GetStatus

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |

**Result (single-channel form)**

| Property | Type | Unit | Description |
|----------|------|------|-------------|
| `id` | number | — | Instance ID |
| `pv_power` | number | see [PV power encoding](#pv-power-encoding) | PV charging power |
| `pv_voltage` | number | V | PV voltage |
| `pv_current` | number | A | PV current |
| `PV_state` | number | — | `1` work, `0` standby (capitalization varies in the PDF) |

**Result (multi-channel form observed on Venus A/D)**

| Property | Type | Unit | Description |
|----------|------|------|-------------|
| `pvN_power` | number | see [PV power encoding](#pv-power-encoding) | Channel N power, N = 1–4 |
| `pvN_voltage` | number | V | Channel N voltage |
| `pvN_current` | number | A | Channel N current |
| `pvN_state` | number | — | Channel N work/standby |

##### PV power encoding

The PDF labels PV power as watts. Observed firmware does not always do that.

| Profile | Channel 1 on the wire | Other channels | Home Assistant |
|---------|----------------------|----------------|----------------|
| Legacy Venus A/D (`ver < 150`, including unknown `ver`) | Deciwatts (divide by 10 → W) | Watts | W |
| Rev 3.1 Venus A/D (`ver >= 150`) | Watts | Watts | W |

The mock device encodes physical watts according to the selected profile. The parser always stores **W**.

---

### 3.6 ES (Energy System)

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `ES.GetStatus` | SoC, power, lifetime energy | Fast-tier sensors |
| `ES.SetMode` | Set Auto / AI / Manual / Passive / UPS | Operating-mode select, services, device actions |
| `ES.GetMode` | Current mode plus optional CT/power/energy fallbacks | Fast-tier; extra CT fields fill gaps only — no duplicate phase-power entities |

#### 3.6.1 ES.GetStatus

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number or null | Instance ID |

**Result — units are per field, not a blanket energy conversion**

| Property | Type | Wire unit (Rev 3.1 table / observed) | Home Assistant |
|----------|------|--------------------------------------|----------------|
| `id` | number or null | — | — |
| `bat_soc` | number or null | % | % |
| `bat_cap` | number or null | Wh | Wh |
| `pv_power` | number or null | W | W |
| `ongrid_power` | number or null | W | W |
| `offgrid_power` | number or null | W | W |
| `bat_power` | number or null | W | W (often omitted on Venus E 3.0 / firmware 150; integration falls back to `pv_power - ongrid_power`) |
| `total_pv_energy` | number or null | **0.01 kWh** on Rev 3.1 / observed Venus A `ver >= 149`; **Wh** on explicit legacy profiles | Wh (`raw × 10` when the profile uses 0.01 kWh) |
| `total_grid_output_energy` | number or null | **Wh** | Wh (never scaled with PV energy) |
| `total_grid_input_energy` | number or null | **Wh** | Wh (never scaled with PV energy) |
| `total_load_energy` | number or null | **Wh** | Wh |

Issue [#35](https://github.com/taurgis/has-marstek-local-api/issues/35) is the Venus A firmware 149 evidence that solar totals were stored about 10× too low when the 0.01 kWh wire unit was treated as Wh.

Unknown firmware does not invent PV energy scaling.

#### 3.6.2 ES.SetMode

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |
| `config` | object | Mode payload |

**`config`**

| Property | Type | Description |
|----------|------|-------------|
| `mode` | string | `"Auto"`, `"AI"`, `"Manual"`, `"Passive"`, `"UPS"` (PDF also writes `"Ups"`; this integration sends `"UPS"`) |
| `auto_cfg` | object | Auto: `enable` `1` |
| `ai_cfg` | object | AI: `enable` `1` |
| `manual_cfg` | object | One schedule slot |
| `passive_cfg` | object | Power and countdown |
| `ups_cfg` | object | UPS: `enable` `1` |

**`manual_cfg`**

| Property | Type | Description |
|----------|------|-------------|
| `time_num` | number | Slot index. **Venus A/C/D/E: 0–9. Venus E mini: 0–5.** Exact family matching matters; Venus E mini must not inherit Venus E slot counts by substring. |
| `start_time` | string | `HH:MM` |
| `end_time` | string | `HH:MM` |
| `week_set` | number | Bitmask, low 7 bits. `1` = Monday … `127` = all days |
| `power` | number | W (negative charge / positive discharge in this integration) |
| `enable` | number | `1` on, `0` off |

**`passive_cfg`**

| Property | Type | Description |
|----------|------|-------------|
| `power` | number | W |
| `cd_time` | number | Countdown seconds |

**`ups_cfg`**

| Property | Type | Description |
|----------|------|-------------|
| `enable` | number | `1` enable UPS. Selecting another mode disables it. |

**Result**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number | Instance ID |
| `set_result` | boolean | Success / failure (PDF examples misspell this as `ture`) |

UPS is an ES mode, not a SYS command. This integration offers UPS on the operating-mode **select** when `supports_ups` is true (ES-capable family and `ver >= 150`, including Venus E mini). Auto, AI, and UPS are selectable directly. Manual and Passive still require parameterized services so empty defaults are never applied.

#### 3.6.3 ES.GetMode

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number or null | Instance ID. Polling prefers `0` and falls back to `1` only when `0` times out or returns a JSON-RPC error. |

**Result**

| Property | Type | Unit | Description |
|----------|------|------|-------------|
| `id` | number or null | — | Instance ID |
| `mode` | string, integer, or null | — | String names `Auto`, `AI`, `Manual`, `Passive`, `UPS` **or** integers `0–4` (`0` Auto … `4` UPS). Reads accept both. `ES.SetMode` still sends the string names. |
| `ongrid_power` | number or null | W | Grid-tied power |
| `offgrid_power` | number or null | W | Off-grid power |
| `bat_soc` | number or null | % | SoC |
| `ct_state` | number or null | — | `0` disconnected, `1` connected (Auto/AI note in PDF) |
| `a_power` / `b_power` / `c_power` | number or null | W | Phase power (Auto/AI note in PDF) |
| `total_power` | number or null | W | CT total power |
| `input_energy` | number or null | **0.1 Wh** (`× 0.1` → Wh) | Cumulative input energy |
| `output_energy` | number or null | **0.1 Wh** (`× 0.1` → Wh) | Cumulative output energy |

`ES.GetMode` does **not** return `passive_cfg`. The integration stores the last Passive settings locally.

Some published Open API samples and some devices disagree on whether `mode` is a string or an integer, and whether `params.id` is `0` or `1`. **This integration prefers its own contract:** `ES.SetMode` sends string names, `ES.GetMode` is requested with `id=0` first, and battery power comes from `ES.GetStatus` (`bat_power`, never `abs(ongrid_power)`). Integer `mode` values and `id=1` are accepted on **read/fallback** only.

Venus E 3.0 firmware **150** answers both `id=0` and `id=1` with the string mode name (`"Auto"`, not `0`). It also includes the Rev 3.1 CT/power/energy keys, but those values can be zeros / `ct_state=0` while `EM.GetStatus` reports a live CT. This integration uses GetMode CT/energy only to fill empty coordinator keys; current and last-known `EM.GetStatus` values always win. Phase-power sensors are not duplicated.

---

### 3.7 EM (Energy Meter / CT)

| Method | Purpose | Integration use |
|--------|---------|-----------------|
| `EM.GetStatus` | CT connection, phase power, lifetime meter energy | Fast-tier; meter energy sensors exist only when those fields are present |

#### 3.7.1 EM.GetStatus

**Params**

| Property | Type | Description |
|----------|------|-------------|
| `id` | number or null | Instance ID |

**Result**

| Property | Type | Wire unit | Home Assistant |
|----------|------|-----------|----------------|
| `id` | number or null | — | — |
| `ct_state` | number or null | `0` / `1` | CT binary sensor |
| `a_power` / `b_power` / `c_power` | number or null | W | W |
| `total_power` | number or null | W | W |
| `input_energy` | number or null | **0.1 Wh** on Rev 3.1 (`ver >= 150` known families) | Wh (`raw × 0.1`) |
| `output_energy` | number or null | **0.1 Wh** on Rev 3.1 | Wh (`raw × 0.1`) |

Legacy profiles do not create EM lifetime energy sensors unless the payload actually reports those fields; the Rev 3.1 scale is applied only when the firmware profile says so.

---

### 3.8 SYS

Rev 3.1 adds system writes. **No GET methods are documented for DOD, Bluetooth advertising, or LED state.** This integration does not invent reads, does not poll SYS, and restores the last successful write (DOD defaults to **88** when nothing valid was restored; BLE/LED stay `unknown` until set).

| Method | Params | Semantics | HA UI |
|--------|--------|-----------|-------|
| `DOD.SET` | `value` integer **30–88**, default **88** | Depth of discharge | Number entity when SYS is supported |
| `Ble.Adv` | `enable`: **`0` enable advertising**, **`1` disable** | Polarity is inverted vs the English method name; implement from the table | Switch (on = advertising) |
| `Led.Ctrl` | `state`: **`1` on**, **`0` off** | Panel LED | Switch |
| `Set.Ver` | `version` | Charge/discharge power “version” (800 / 1200 VA / 1500 VA / 2200 VD / 2500 VE in the PDF) | **Not exposed** |
| `Reset.Factory` | `type` `1` clear totals, `2` keep totals (PDF wording) | Factory reset | **Not exposed** |

SYS writes pause coordinator polling like other controls.

#### 3.8.1 DOD.SET

| Property | Type | Description |
|----------|------|-------------|
| `value` | number | DOD 30–88. Device default 88 |

Result: `set_result` boolean.

#### 3.8.2 Ble.Adv

| Property | Type | Description |
|----------|------|-------------|
| `enable` | number | `0` enable Bluetooth advertising; `1` disable |

Result: `set_result` boolean.

#### 3.8.3 Led.Ctrl

| Property | Type | Description |
|----------|------|-------------|
| `state` | number | `1` panel LED on; `0` off |

Result: `set_result` boolean.

#### Set.Ver and Reset.Factory (not in the default UI)

These methods exist in Rev 3.1. They are destructive or poorly specified (no readable current value). This integration documents them here and **does not** offer them as entities, services, or recommended actions.

---

## IV. Devices and firmware

Exact family matching matters. **Venus E mini is not Venus E.**

### 4.1 Vendor-documented protocol support (Rev 3.1 chapter 4)

| Component | Venus C / Venus E | Venus A / Venus D | Venus E mini |
|-----------|-------------------|-------------------|--------------|
| Marstek | yes | yes | yes |
| WiFi | yes | yes | yes |
| Bluetooth | yes | yes | yes |
| Battery | yes | yes | yes |
| PV | **no** | **yes** | **no** |
| ES | yes | yes | yes |
| EM | yes | yes | yes |
| SYS DOD / BLE / LED | firmware **`ver >= 150`** | firmware **`ver >= 150`** | SYS subset **without** the 150 gate |
| UPS (`ES.SetMode`) | firmware that implements it (`ver >= 150` in this integration) | same | same (`ver >= 150`; unknown `ver` → no UPS) |

Venus **E2.0 is not supported** by this integration.

Discovery `device` strings may be display names (`VenusA 3.0`, `Venus D`, `VenusE 3.0`) **or** vendor SKUs (`VNSA-0`, `VNSD-0`, `VNSE3-0`). Both map to the same families. `VNSE2` is not mapped (E2.0 remains unknown).

### 4.2 Integration firmware-profile behavior (shipped)

Inputs: device family + integer discovery `ver`. Unknown/unparseable `ver` is legacy-safe.

| Family | `ver` | PV | SYS DOD/BLE/LED | UPS | Manual slots | PV energy | PV ch1 power | EM energy scale |
|--------|-------|----|-----------------|-----|--------------|-----------|--------------|-----------------|
| Venus A | unknown | yes | no | no | 0–9 | unscaled (legacy-safe) | deciwatt → W | none |
| Venus A | 149 | yes | no | no | 0–9 | 0.01 kWh → Wh (`×10`) | deciwatt → W | none |
| Venus A | >= 150 | yes | yes | yes | 0–9 | 0.01 kWh → Wh | watts | 0.1 Wh → Wh |
| Venus D | < 150 or unknown | yes | no | no | 0–9 | unscaled unless profile says otherwise | deciwatt → W | none |
| Venus D | >= 150 | yes | yes | yes | 0–9 | 0.01 kWh → Wh | watts | 0.1 Wh → Wh |
| Venus C / Venus E | < 150 or unknown | no | no | no | 0–9 | n/a (no PV) | n/a | none |
| Venus C / Venus E | >= 150 | no | yes | yes | 0–9 | n/a | n/a | 0.1 Wh → Wh |
| Venus E mini | known integer, `< 150` | no | **yes** (no 150 gate) | no | **0–5** | n/a | n/a | none |
| Venus E mini | >= 150 | no | yes | yes | **0–5** | n/a | n/a | 0.1 Wh → Wh |
| Unknown family | any | no | no | no | 0–9 | no invented scaling | deciwatt fallback unused without PV | no |

When the scanner sees a `ver` or model change that unlocks or removes setup-time capabilities, the config entry reloads so entities are created or omitted. Cosmetic metadata updates do not reload.

### 4.3 Observed Venus E 3.0 firmware 150 (LAN read, 2026-09-15)

Live GET-only capture against a `VenusE 3.0` with discovery `ver` **150**. UDP timeouts on Wi-Fi are common; retries eventually returned a JSON-RPC result or error for every method below. Identifiers (MAC, SSID, IP) are omitted here.

Firmware profile this integration applies: family **Venus E**, no PV, SYS DOD/BLE/LED **yes**, UPS **yes**, manual slots **0–9**, EM energy scale **0.1 Wh → Wh**.

| Method | `params.id` | Observed |
|--------|-------------|----------|
| `Marstek.GetDevice` | — | `device`: `"VenusE 3.0"`; `ver`: `150` (integer). Display name, not SKU `VNSE3-0`. |
| `ES.GetMode` | `0` (preferred) | `mode`: `"Auto"` (string). Also `ongrid_power`, `offgrid_power`, `bat_soc`, plus Rev 3.1 `ct_state` / phase power / `input_energy` / `output_energy`. CT/power/energy were **zeros** (`ct_state=0`) at the same time `EM.GetStatus` showed a connected CT. |
| `ES.GetMode` | `1` (fallback) | Same payload shape; `result.id` echoes `1`. First attempt timed out; a retry succeeded. |
| `ES.GetStatus` | `0` | `bat_soc`, `bat_cap` (5120 Wh), `pv_power` (0), `ongrid_power`, `offgrid_power`, lifetime energy fields. **`bat_power` omitted.** `total_pv_energy` / `total_load_energy` present as `0`. Grid totals in **Wh** (not 0.01 kWh). |
| `EM.GetStatus` | `0` | Live CT: `ct_state=1`, `a_power` / `total_power` non-zero, `b_power` / `c_power` 0. `input_energy` and `output_energy` present as `0` (scaled `× 0.1` → 0 Wh). |
| `Wifi.GetStatus` | `0` | Full station fields including `wifi_mac`, `ssid`, `rssi`, `sta_ip`, `sta_gate`, `sta_mask`, `sta_dns`. Lowercase `wifi.GetStatus` did not answer. |
| `BLE.GetStatus` | `0` | `state`: `"disconnect"`; `ble_mac` present. Slow / flaky on Wi-Fi; not polled by this integration. |
| `Bat.GetStatus` | `0` | `soc` number, `charg_flag` / `dischrg_flag` booleans, `bat_temp` °C, `bat_capacity` / `rated_capacity` Wh. |
| `PV.GetStatus` | `0` | JSON-RPC **`-32601 Method not found`**. Error object may include an extra integer `data` field (observed `424`). Matches chapter 4 (no PV on Venus E). This integration does not send `PV.GetStatus` for Venus E. |

`ES.GetStatus` still reports `pv_power` / `total_pv_energy` as zero on this non-PV family; those keys are not treated as a PV array.

The Docker mock `mock-marstek-2` (`172.28.0.25`, `--ver 150`) reproduces this wire shape. `mock-marstek` (`172.28.0.20`, `--ver 145`) stays on the older Venus E profile.

---

## V. Change logs

- **2025-08-09** Vendor PDF: Version number Rev 1.0 (historical).
- **2026-09-10** Vendor PDF metadata: Open API **Rev 3.1** (SYS, UPS, Venus E mini, field-specific energy units). Local reference updated to match that revision and the shipped firmware-profile integration.
- **2026-09-15** Reads accept integer `ES.GetMode` values `0–4` and fall back to instance id `1` when `id=0` fails. Writes, battery-power math, BLE-MAC unique IDs, and the `id=0` default stay this integration's contract. Vendor SKUs `VNSA-0` / `VNSD-0` / `VNSE3-0` map onto existing Venus families.
- **2026-09-15** Venus E 3.0 firmware 150 LAN capture: string `ES.GetMode` names, omitted `ES.GetStatus` `bat_power`, GetMode CT zeros vs live `EM.GetStatus`, `PV.GetStatus` `-32601`, case-sensitive `Wifi.GetStatus`. GetMode CT/energy fills empty keys only so Wi-Fi timeouts do not clobber last-known meter readings.
