# Integration findings from the firmware emulator audits

The Venus E 3.0 (VNSE3-0 144-150), Venus A (VNSA-0 148-1509), Venus D (VNSD-0
147-150) and HMG-50 (153-156) images were run in Renode against Home Assistant
and checked against their disassembly. None of these are fixed yet; each needs a
change in `custom_components/marstek/` with tests and a changeset.

## Bugs

| # | Where | Finding | Evidence |
|---|---|---|---|
| 1 | `firmware_profile.py` `scaled_pv_energy` | Solar energy reads 10× too low on Venus A 1487 and Venus D 149 and 1492. These report `total_pv_energy` as the MPPT year counter in 0.01 kWh, like Venus A 149/150 and Venus D 150. The profile scales Venus D only from 150 and treats 1487 as generation 148. | Debug label `PV_Year_Cap_10Wh`; the fill loads MPPT page 6 at 0x8020848 (VNSD 149), 0x8020da8 (1492), 0x8021370 (150). On 149, HA showed 15 Wh for a device value of 21 (210 Wh). |
| 2 | `scanner.py` | The scanner matched devices by `wifi_mac` and moved one config entry to another device's IP. In every VNSE3-0 build, `wifi_mac` is the access point's BSSID (filled by the `bssid=` parser of `+QGETWIFISTATE`, 150: 0x2001a626), so batteries on one AP share it. | Seen in the devcontainer when two emulators shared a BSSID. Match on the BLE MAC. |
| 3 | `discovery.py` / `config_flow.py` | The manual and discovery flows miss devices once an entry uses port 30000. The flow's socket and the pooled client share the port, so the reply can go to the wrong socket. | Adding a device needed 2 or more attempts, and the logs show `No valid response`. |
| 4 | `switch.py` / `const.py` | The Bluetooth advertising switch polarity looks inverted (`BLE_ADV_ENABLED = 0`). | Needs a check against `Ble.Adv` on a real device before flipping. |
| 5 | battery power fallback | Venus A doesn't report battery power, and the fallback (PV minus grid) leaves out `offgrid_power` (the EPS socket). | With 80 W on the socket, HA showed -300 W instead of about -216 W. |
| 6 | family detection | Venus A 1508 reports `device: "VenusE Pro"` and is treated as an unknown family (no PV, energy or SYS/UPS). The firmware is otherwise Venus A 150. | GetDevice reply of the 1508 image |

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
