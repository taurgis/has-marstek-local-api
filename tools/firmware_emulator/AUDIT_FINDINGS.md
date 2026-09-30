# Integration findings from the firmware emulator audits

The Venus E 3.0 (VNSE3-0 144-151), Venus A (VNSA-0 148-1509), Venus D (VNSD-0
147-151) and HMG-50 (153-156) images were run in Renode against Home Assistant
and checked against their disassembly. Bugs 1-5 are fixed in
`custom_components/marstek/` with tests and a changeset each; 6 is kept by design.

## Bugs

| # | Where | Finding | Evidence | Status |
|---|---|---|---|---|
| 1 | `firmware_profile.py` `scaled_pv_energy` | Solar energy reads 10× too low on Venus A 1487 and Venus D 149 and 1492. These report `total_pv_energy` as the MPPT year counter in 0.01 kWh, like Venus A 149/150 and Venus D 150. The profile scales Venus D only from 150 and treats 1487 as generation 148. | Debug label `PV_Year_Cap_10Wh`; the fill loads MPPT page 6 at 0x8020848 (VNSD 149), 0x8020da8 (1492), 0x8021370 (150). On 149, HA showed 15 Wh for a device value of 21 (210 Wh). | Fixed: the profile scales Venus A from 1487 and Venus D from 149. Both emulators report 1.01 kWh as `total_pv_energy: 101`. |
| 2 | `scanner.py` | The scanner matched devices by `wifi_mac` and moved one config entry to another device's IP. In every VNSE3-0 build, `wifi_mac` is the access point's BSSID (filled by the `bssid=` parser of `+QGETWIFISTATE`, 150: 0x2001a626), so batteries on one AP share it. | Seen in the devcontainer when two emulators shared a BSSID. Match on the BLE MAC. | Fixed: `mac`/`wifi_mac` identify a device only when it reports no BLE MAC. Reconfigure and repairs, which confirm a host the user chose, still accept any reported MAC. The device registry no longer registers the BSSID as a network MAC either, and setup removes the one older releases added, so DHCP `registered_devices` no longer matches the access point's lease. |
| 3 | `discovery.py` / `config_flow.py` | The manual and discovery flows miss devices once an entry uses port 30000. The flow's socket and the pooled client share the port, so the reply can go to the wrong socket. | Adding a device needed 2 or more attempts, and the logs show `No valid response`. | Fixed: while pooled clients are paused, broadcast discovery reads their sockets (`MarstekUDPClient.paused_socket`) instead of binding the port again. |
| 4 | `switch.py` / `const.py` | The Bluetooth advertising switch polarity looks inverted (`BLE_ADV_ENABLED = 0`). | Needs a check against `Ble.Adv` on a real device before flipping. | Fixed: `BLE_ADV_ENABLED = 1`. Disassembly of VNSE3-0 150 (handler 0x801f4c8, send helper 0x800be70, BLE task 0x8006a20) and VNSA-0 150 sends `AT+QBLEADVSTART` for 1 and `AT+QBLEADVSTOP` for 0; a fresh EEPROM defaults to 1 (advertising). The PDF table is reversed. |
| 5 | battery power fallback | Venus A doesn't report battery power, and the fallback (PV minus grid) leaves out `offgrid_power` (the EPS socket). | With 80 W on the socket, HA showed -300 W instead of about -216 W. | Fixed: the fallback is `pv_power - ongrid_power - offgrid_power`, also in the PV-channel recalculation. |
| 6 | family detection | Venus A 1508 reports `device: "VenusE Pro"` and is treated as an unknown family (no PV, energy or SYS/UPS). The firmware is otherwise Venus A 150. | GetDevice reply of the 1508 image | Kept: the image banners `VEPRO-0`, a different product line. `tests/test_firmware_profile.py` pins that the family is not guessed; add it once a real VenusE Pro capture confirms its capabilities. |

Control 151 (VNSE3-0, VNSD-0) needed no integration change: same Open API as
150, only `src` now names the SKU. See `tools/firmware/ANALYSIS.md`.

## Conservative gates

The firmware has more than the profile enables. Widen these only with real-device confirmation.

- VNSE3-0: `DOD.SET` and UPS exist from 147, `Ble.Adv`/`Led.Ctrl` from 1476. The profile enables them from 150.
- VNSA-0 148/149 and VNSD-0 149/1492 answer DOD, LED, BLE and UPS writes with `set_result: true`.
- On 147-149 the mode select shows `unknown` while the device reports `UPS`. On 148, a later Manual `ES.SetMode` still leaves `ES.GetMode` at `UPS`.
- Venus D power cap is 2200 W in `_FAMILY_POWER_LIMITS`; the firmware's socket setting accepts 800, 2200 and 2500 W.

## Firmware quirks (document, don't fix)

- `Bat.GetStatus` copies one BMS permit byte into both `charg_flag` and `dischrg_flag`.
- `total_load_energy` is always 0. Venus A 148 hard-codes `pv_power` and `total_pv_energy` to 0, and Venus D 147 does the same in `ES.GetStatus`.
- HMG-50 answers only a fraction of Local API requests while it services its CT, see [issue #82](https://github.com/taurgis/has-marstek-local-api/issues/82) and the README.

## Reproduce

```bash
docker compose -f .devcontainer/docker-compose.yml --profile firmware-all up -d
```

The service table is in `tools/mock_device/README.md`.
