# Firmware emulator (Renode)

Runs the **unmodified vendor Control firmware** of a Venus E 3.0, Venus A or
Venus D in [Renode](https://renode.io/), with the chips around the MCU
modelled well enough for the firmware to boot, join "Wi-Fi" and serve the
Local API on real UDP port 30000 of this host. Home Assistant then adds and
polls it like a physical device.

This complements `tools/mock_device/`: the mock is our reading of the protocol,
while this emulator is the vendor's own implementation, including its quirks
(reply formatting, dropped requests while the modem is busy, `set_result`).

## What is modelled

| Part | Model | File |
|------|-------|------|
| MCU (Cortex-M4F, STM32F1/GD32F30x-style map) | Renode `stm32f103` base, M4F core | `renode/control.repl.in` |
| Clock tree, flash controller | always-ready register stubs | `renode/rcu.py`, `fmc.py` |
| RTC (BCD calendar) and backup registers | calendar on the host's wall clock; backup DATA0 preset | `renode/rtc.py`, `bkp.py` |
| Config EEPROM (I2C 0x50, 2-byte address) | file-backed 24Cxx | `renode/I2CEeprom.cs` |
| CH395 Ethernet (SPI2) | idle chip, answers 0x00 | `renode/spi2.py` |
| Block engine at 0xA0001000 | loopback FIFO | `renode/cau.py` |
| Quectel FC41D Wi-Fi/BLE modem (UART, IRQ 74) | AT-command emulator bridging `UDP SERVICE` sockets to host UDP | `fc41d.py` |
| CAN0 (BMS + inverter bus) | Renode `STMCAN` on a CAN hub, bridged to TCP 3457 | `renode/CanBridge.cs` |
| BMS and inverter | energy-counted battery, inverter that follows the MCU's setpoint | `can_peers.py` |
| PV MPPT (Venus A/D) | up to four constant-power channels that charge the battery | `can_peers.py --pv` |
| CT003 meter | [AstraMeter](https://github.com/tomquist/AstraMeter) reading `can_peers.py`'s grid meter | `astrameter/config.ini` |

Not modelled: RS485, the lifetime PV energy counter (`total_pv_energy` stays 0),
inverter pages 1-3 and 7 and the per-pack BMS PGNs (the firmware only forwards
them to the cloud and the LEDs; no Open API field reads them) and the cloud
(HTTP/MQTT get `OK` and no data).

## Images

Every VNSE3-0, VNSA-0 and VNSD-0 image in `tools/firmware/catalog.json` boots on
the same board and serves the Local API. Checked with `Marstek.GetDevice`,
`ES.GetStatus`, `ES.GetMode`, `Bat.GetStatus`, `PV.GetStatus`, `EM.GetStatus`,
`Wifi.GetStatus` and `BLE.GetStatus`:

| Family | Versions | Reports |
|--------|----------|---------|
| VNSE3-0 | 144, 147, 1476, 148, 149, 150 | `VenusE 3.0`; `PV.GetStatus` is `Method not found` |
| VNSA-0 | 148, 1487, 149, 150, 1509 (ems) | `Venus A` |
| VNSA-0 | 1508 (banner VEPRO-0) | `VenusE Pro` |
| VNSD-0 | 147, 149, 1492, 150 | `Venus D` |

Any method can come back as `Parse error` (data 403) or time out while a CT
reply is being handled; see [Firmware debug log](#firmware-debug-log). Venus A and
D report `pv1_power` in 0.1 W and the other channels in W, as the real devices do.
Home Assistant, which corrects that, shows the configured watts.

The HMG-50 images (Venus C / E 2.0: 153, 155, 156) target an STM32G4, which
Renode has no platform for yet.

## Run it

1. The firmware images are in the repository under `tools/firmware/blobs/`
   (Marstek/Hamedata copyright). Check them against the catalog with:

   ```bash
   python3 tools/firmware/fetch_firmware.py   # "ok ... (cached)" for each image
   ```

2. Install [Renode](https://github.com/renode/renode/releases) (the portable Linux
   build is enough; tested with 1.17).

3. Launch, reporting the IP that Home Assistant can reach this host on. For the
   devcontainer compose network that is the bridge gateway `172.28.0.1`:

   ```bash
   python3 tools/firmware_emulator/run_firmware.py \
       --renode /opt/renode/renode --ip 172.28.0.1 \
       --firmware VNSD-0:150 --work /tmp/marstek-fw/vnsd-150 \
       --pv 400,350,300,250 --ac-limits 2200,2500 --ble-mac 02:ee:00:00:0d:02
   ```

   `--firmware DEVICE:VERSION` picks an image from the catalog (default: the
   VNSE3-0 150 blob; `--blob PATH` takes any file). A fresh EEPROM
   (`WORK/eeprom.bin`) gets its defaults on the first boot. `--local-api-port`
   (default 30000) is then forced on. The launcher reboots once if needed, then
   starts `can_peers.py` with `--soc`, `--house-load A,B,C`, `--pv` (W per
   channel, Venus A/D) and `--ac-limits DISCHARGE,CHARGE` (W). Logs land in
   `WORK` (default `/tmp/marstek-fw/`): `fc41d.log`, `can_peers.log`,
   `renode.log`. Keep one work directory per image: the EEPROM layout is the
   firmware's own.

   The BLE MAC is the integration's unique ID. Give each emulated device its own
   `--ble-mac`, or Home Assistant keeps the first one's config entry.

   For the CT, run AstraMeter on the host network. It needs no Home Assistant:
   it polls `can_peers.py` on `127.0.0.1:8099`, where the grid power is the house
   load minus the inverter's output on phase A.

   ```bash
   docker run -d --name astrameter --network host \
       -v "$PWD/tools/firmware_emulator/astrameter/config.ini:/app/config.ini:ro" \
       ghcr.io/tomquist/astrameter:latest
   ```

4. Check it answers (from another host/container; it listens on the host's port 30000):

   ```bash
   docker exec -i marstek-ha-dev python3 -c 'import json,socket
   s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(20); s.bind(("",30000))
   s.sendto(json.dumps({"id":1,"method":"Marstek.GetDevice","params":{"ble_mac":"0"}}).encode(),("172.28.0.1",30000))
   print(s.recvfrom(4096)[0].decode())'
   ```

5. In Home Assistant, **Add integration → Marstek**. Broadcast discovery lists
   the image, e.g. `Venus D v150 (MarstekEmu) - 172.28.0.1`.

## FC41D facts learned from the firmware

- `+QBLEADDR:` has no space after the colon; the MAC colons are checked at fixed offsets.
- `+QGETIP:` must be `ip:…,gateway:…,mask:…,dns:…` with no leading CRLF. The MCU
  scans `ip:` up to `gate` and `mask:` up to `dns`, then copies up to the first CRLF.
- `AT+QISEND=<id>,<len>,"<bytes>","<ip>",<port>` carries the payload inline. An
  empty IP means broadcast.
- The Local API is connection 3: `AT+QIOPEN=3,"UDP SERVICE","<ip>",2025,<port>,1`.
- EEPROM byte 0 marks an initialised store. The Local API enable flag is at 0x371
  and the port at 0x372 (uint16 LE).

## CAN bus facts learned from the firmware

Every frame is 29-bit extended. `can_peers.py` documents the payloads it sends.

- BMS frames are `(PGN << 16) | 0xAA00 | src`. The MCU looks the PGN up in a
  17-entry table (0x1801-0x1804, 0x1807, 0x1820-0x1823, 0x1830-0x1833, 0x1840,
  0x1841, 0x1852, 0x1853) and copies the payload raw into one status struct.
  `Bat.GetStatus` and the SoC in `ES.GetStatus` come straight from 0x1801/0x1802.
  The MCU polls with `1801ffaa`.
- Other devices use `page << 24 | src_type << 20 | 1 << 16 | dst_type << 12 |
  addr << 8 | cmd`: MCU 0, PV MPPT 2, inverter 4, type 3 not identified.
  Inverter replies use cmd 0x10, MPPT replies cmd 0x03 (pages 2-4: PV1-PV4 volt,
  current and power in 0.1 units). Venus E images parse MPPT frames too. Page 4 gives `ongrid_power`/`offgrid_power`, page 5 the
  AC limits, and page 6 the lifetime grid energy.
- The MCU drives the inverter with cmd 0x01, a signed 32-bit AC setpoint in W
  (+ = discharge). It sends each new value three times, clamped to the BMS
  current limits × voltage. After boot it writes its stored energy counters
  back with cmd 0x16/0x17.
- Auto mode first runs a CT phase test: about 20 s of ±800 W, discharging above
  50 % SoC and charging below. The BMS permission bit for that direction must be
  set. The detected phase is stored at EEPROM 0x369.

## Clock and write paths (VNSE3-0 144-150)

The RTC is a BCD calendar with the STM32F4 register layout (TR, DR, ISR with
INIT/INITF/RSF, WPR 0xCA/0x53). `rtc.py` serves the host's wall clock in the
Renode process's local time, so pass `TZ` (for example `-e TZ=Europe/Brussels`
with Docker). A real device sets its clock from the cloud (HTTP `getDateInfo`,
MQTT "Set local time", BLE), which the emulator cannot do. At boot the firmware
checks backup register DATA0 (0x40006C04) for 0xA5A5. If the value is missing,
it rewinds the RTC to the time it last saved in EEPROM (0x160) or to
2019-11-20. `bkp.py` presets 0xA5A5, as on a device whose clock kept running.
A time written in init mode still moves the calendar, as an offset from host
time.

What the firmware does with the writes, from its own code:

- **Manual**: slots run on the RTC, with `week_set` bit 0 = Monday and the
  RTC's weekday numbering 1 = Monday .. 7 = Sunday. A slot that covers the
  current time sends its power as the inverter setpoint; outside the slot the
  setpoint is 0.
- **Passive**: `power` must be within ±2500 W. `cd_time` counts the firmware's
  own seconds tick, which is Renode virtual time: under load it runs slower
  than the wall clock, so the countdown lasts longer. When it expires the setpoint
  goes to 0 and `ES.GetMode` keeps reporting `Passive`.
- **UPS**: `ES.SetMode` accepts `ups_cfg` from 147 on; `ES.GetMode` then reports
  `UPS`.
- **DOD.SET** (147+, 30-88): the firmware stores the value and forwards it to the
  inverter as cmd 0x0A (byte 0). `can_peers.py` moves its discharge floor and
  the 0x1802 DoD byte to match.
- **Ble.Adv / Led.Ctrl** (1476+): answer `set_result`. Ble.Adv drives
  `AT+QBLEADVSTART`/`STOP`, which `fc41d.py` acknowledges.
- `Bat.GetStatus` copies the whole 0x1803 permission byte into both
  `charg_flag` and `dischrg_flag`, so both stay true until the BMS withdraws
  charge *and* discharge. `can_peers.py` clears bit 0 at 100 % and bit 1 at
  the DoD floor.

`wifi_mac` in `Marstek.GetDevice` and `Wifi.GetStatus` is the `bssid=` of
`+QGETWIFISTATE`, i.e. the access point, in every VNSE3-0 build. Real
batteries on one AP therefore share it. Give each emulated device its own
BSSID, or Home Assistant's scanner treats two of them as one device and moves a
config entry to the other's IP.

## Firmware debug log

The firmware logs to USART2 (`WORK/usart2.txt`) once enabled from the Renode
monitor (`telnet 127.0.0.1 41234`, after the launcher prints "Running"). The two
flags sit at the same addresses in every VNSE3-0, VNSA-0 and VNSD-0 image:

```
sysbus WriteByte 0x20000132 1   # logging on
sysbus WriteByte 0x2000012a 5   # all modules
```

That log shows the Venus E 3.0's own form of the HMG-50 shared-channel problem.
An Open API request that arrives while a CT reply is being handled logs
`Extract_udp_data ... ct failed to get the mutex lock!`. The request then gets
`Parse error` (`-32700`, data `403`) with `"id": 0`.
