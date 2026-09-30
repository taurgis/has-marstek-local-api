# Integration findings from the firmware emulator audits

The Venus E 3.0 (VNSE3-0 144-151), Venus A (VNSA-0 148-1509), Venus D (VNSD-0
147-151) and HMG-50 (153-156) images were run in Renode against Home Assistant
and checked against their disassembly. Bugs 1-5 and 7-11 are fixed in
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
| 7 | `pymarstek/data_parser.py` | While the BMS is silent the firmware keeps answering but reports the battery as zeros: `ES.GetMode` `bat_soc` 0 first (about 15 s), then `ES.GetStatus` `bat_soc`/`bat_cap` 0 and `Bat.GetStatus` `soc`, `bat_capacity`, `rated_capacity` and `bat_temp` 0 (about 40 s). Each field drops on its own schedule. HA showed *Battery total capacity* 0 Wh and SoC 0 %. | Freezing `can_peers.py` (SIGSTOP) on Venus E 151 reproduces it every time. The images also hit it on their own when CAN frames are lost: Venus E 1476 sent `bat_soc 0, bat_cap 0` and Venus D 150 sent `bat_soc 76, bat_cap 0`. | Fixed: `bat_cap` 0 or `rated_capacity` 0 marks that reply's battery fields as placeholders. They are dropped, so the last reading stays and a first poll shows unknown, never 0. An `ES.GetMode` SoC of 0 is ignored. A drained pack (SoC 0 with a real capacity) still reads 0. |
| 8 | `pymarstek/command_builder.py`, `discovery.py` | Requests were sent with `json.dumps` default separators. A manual `ES.SetMode` was 205 bytes instead of 183. A request that arrives while the CT task holds the modem is dropped (`Extract_udp_data ... ct failed to get the mutex lock!`, reply `Parse error` data 403), and a longer datagram stays on the modem UART longer. | Randomised, 20 of each per image, acknowledged spaced vs compact: Venus E 150 5 vs 17, Venus E 151 4 vs 18, Venus A 150 2 vs 19, Venus D 150 0 vs 15. It is not a size limit: padded reads up to 198 bytes were accepted. | Fixed: every request is encoded with `(",", ":")` separators (`json_dumps_wire`). RFC 8259 section 2 makes the whitespace optional. |
| 9 | `pymarstek/udp.py`, `throttle.py` | After the firmware answers any request, it drops what arrives in the next second or so. The CT task catches up on the exchanges it missed while the reply held the mutex (the log shows about six CT exchanges back to back). The 0.3 s throttle counted from the send, so the verify `ES.GetMode` 60 ms after a `SetMode` acknowledgement, or a second SYS write, always went unanswered. | Venus E 150 and 151: 0/23 follow-up requests (reads and writes, after reads, writes and `SetMode`) answered within 1 s of a reply, 13/13 after 2 s. | Fixed: throttled requests also wait `POST_REPLY_QUIET_INTERVAL` (2 s) after the device's last datagram. The 5 s poll spacing is unaffected. |
| 10 | `helpers/sys_write.py` | `DOD.SET`, `Led.Ctrl` and `Ble.Adv` were sent once. In HA the DOD number and both switches failed with *timed out* on every run on Venus E 150. | With HA polling, Venus E 151 acknowledged 17/30 SYS writes on the first try and 28/30 within three. | Fixed: a timeout is retried up to three times. The methods set an absolute value, so a copy that lands after a lost acknowledgement changes nothing. HA then passed 16/16 SYS writes on the four 150/151 images. |
| 11 | `pymarstek/data_parser.py` | Venus A 148 reports `ES.GetStatus` `pv_power` 0, and the PV-channel correction ran only on polls that carried `PV.GetStatus`, a medium-tier read. In between, HA showed *PV power* 0 W and the battery *idle*, then 780 W *charging* again: 12 flips in 17 minutes. | Venus A 148 in HA, from the state history. | Fixed: the correction uses the channels kept from the previous PV read. On the same image, HA then held 780 W *charging* with no flip. |

Control 151 (VNSE3-0, VNSD-0) needed no integration change: same Open API as
150, only `src` now names the SKU. See `tools/firmware/ANALYSIS.md`.

## Conservative gates

The firmware has more than the profile enables. Widen these only with real-device confirmation.

- VNSE3-0: `DOD.SET` and UPS exist from 147, `Ble.Adv`/`Led.Ctrl` from 1476. The profile enables them from 150.
- VNSA-0 148/149 and VNSD-0 149/1492 answer DOD, LED, BLE and UPS writes with `set_result: true`.
- On 147-149 the mode select shows `unknown` while the device reports `UPS`. On VNSE3-0 147/1476 and VNSA-0 148, a later Manual `ES.SetMode` still leaves `ES.GetMode` at `UPS`.
- Venus D power cap is 2200 W in `_FAMILY_POWER_LIMITS`; the firmware's socket setting accepts 800, 2200 and 2500 W.

## Firmware quirks (document, don't fix)

- `Bat.GetStatus` copies one BMS permit byte into both `charg_flag` and `dischrg_flag`.
- `total_load_energy` is always 0. Venus A 148 hard-codes `pv_power` and `total_pv_energy` to 0, and Venus D 147 does the same in `ES.GetStatus`.
- A manual slot whose window already includes the current time is acknowledged much less often than one that starts later. Venus E 151, both sent from Auto with compact JSON: 7/16 within three tries against 7/8. The failures are `Parse error` 403 or silence, so the three retries already in place are the only mitigation.
- Venus A `ongrid_power` (in both `ES.GetStatus` and `ES.GetMode`) follows a new setpoint only after tens of seconds; on 1487 it read 0, then 600, then 0 again. Right after a mode change the fallback battery power is therefore PV only. Nothing better is on the wire, so the parser keeps it.
- The emulators lose BMS CAN frames now and then, which triggers bug 7 without any fault injected. Treat a zero battery reading in a campaign as that, not as a new bug.
- HMG-50 answers only a fraction of Local API requests while it services its CT, see [issue #82](https://github.com/taurgis/has-marstek-local-api/issues/82) and the README.

## Reproduce

Start the images a finding is about by name, a few at a time (AGENTS.md,
*Sandbox resource budget*), for example bug 1:

```bash
cd .devcontainer
docker compose up -d homeassistant fw-venus-a-1487-ct fw-venus-d-149-ct fw-venus-d-1492-ct
docker compose rm -sf fw-venus-a-1487-ct fw-venus-a-1487 fw-venus-d-149-ct fw-venus-d-149 fw-venus-d-1492-ct fw-venus-d-1492
```

`--profile firmware-all up -d` starts all twenty-two, which only a large host carries.

The service table is in `tools/mock_device/README.md`.
