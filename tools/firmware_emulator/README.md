# Firmware emulator (Renode)

Runs the **unmodified vendor Control firmware** of a Venus E 3.0, Venus A,
Venus D, Venus C or Venus E 2.0 in [Renode](https://renode.io/), with the chips around the MCU
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
| Clock tree, flash controller, RTC | always-ready register stubs | `renode/rcu.py`, `fmc.py`, `rtc.py` |
| Config EEPROM (I2C 0x50, 2-byte address) | file-backed 24Cxx | `renode/I2CEeprom.cs` |
| CH395 Ethernet (SPI2) | idle chip, answers 0x00 | `renode/spi2.py` |
| Block engine at 0xA0001000 | loopback FIFO | `renode/cau.py` |
| Quectel FC41D Wi-Fi/BLE modem (UART, IRQ 74) | AT-command emulator bridging `UDP SERVICE` sockets to host UDP | `fc41d.py` |
| CAN0 (BMS + inverter bus) | Renode `STMCAN` on a CAN hub, bridged to TCP 3457 | `renode/CanBridge.cs` |
| BMS and inverter | energy-counted battery, inverter that follows the MCU's setpoint | `can_peers.py` |
| PV MPPT (Venus A/D) | up to four constant-power channels that charge the battery | `can_peers.py --pv` |
| HMG-50 board (Venus C / E 2.0): STM32G474 map, USART/LPUART FIFO mode, RCC/PWR/FLASH/RTC/HRTIM/ADC/CORDIC stubs, FDCAN | Renode STM32 models + G4 stubs | `renode/hmg50.repl.in`, `STM32G4_USART.cs`, `g4*.py` |
| CT003 meter | [AstraMeter](https://github.com/tomquist/AstraMeter) reading `can_peers.py`'s grid meter | `astrameter/config.ini` |

Not modelled: RS485, the lifetime PV energy counter (`total_pv_energy` stays 0)
and the cloud (HTTP/MQTT get `OK` and no data).

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

The HMG-50 images (Venus C / E 2.0) run on their own STM32G474 board
(`renode/hmg50.repl.in`, picked from the catalog's device type or with
`--board hmg50`). The app sits at 0x08002800 above a 10 KiB bootloader:

| Version | SP / reset | Reports | Notes |
|---------|------------|---------|-------|
| 153 | 0x20016a98 / 0x08002aad | `VenusC` (`--capacity 2560`) | `Bat.GetStatus` in raw units (temp 250, voltage 5270); `EM.GetStatus` is `{"parse_state":…}`; `Wifi.GetStatus` RSSI positive |
| 155 | 0x20016cb0 / 0x08002aad | `VenusE` (5120 Wh) | EM server with `ct_state` and phase powers |
| 156 | 0x20017180 / 0x08002ab1 | `VenusE` (5120 Wh) | as 155; `Bat.GetStatus` scaled (temp 25.0) |

On every version, `Marstek.GetDevice`, `ES.GetStatus`, `ES.GetMode`,
`Bat.GetStatus`, `EM.GetStatus` and `Wifi.GetStatus` all answer. Only about one
request in four gets a reply, though (see
[HMG-50 and issue #82](#hmg-50-and-issue-82)). The family name comes from the
BMS rated energy in CAN 0x355: 1-3000 Wh is HMG-25 (`VenusC`), more is HMG-50
(`VenusE`), and no BMS gives `Venus`.

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

An HMG-50 unit that runs next to another emulator needs its own ports. Bind its
sockets to the bridge address so that HA's own port 30000 stays free:

```bash
python3 tools/firmware_emulator/run_firmware.py \
    --renode /opt/renode/renode --firmware HMG-50:156 --work /tmp/marstek-fw/hmg-156 \
    --ip 172.28.0.1 --bind-ip 172.28.0.1 --ble-mac 02:ee:00:00:c5:02 \
    --bssid 02:ee:00:00:c5:01 --monitor-port 41300 --uart-port 3556 --can-port 3557 \
    --meter-port 8199 --meter-bind 172.17.0.1 --broadcast-to 172.17.0.2 --capacity 5120
```

`--broadcast-to` sends the firmware's CT broadcasts to AstraMeter. In this
example AstraMeter runs on the default Docker bridge (`172.17.0.2`) and polls
`http://172.17.0.1:8199/`. Give each unit its own `--bssid` too: HMG-50 reports
the AP BSSID as `wifi_mac`, and HA rejects a repeated one as
`already_configured`. Broadcast discovery can't reach a socket bound to one
address, so add the unit by its IP.

## FC41D facts learned from the firmware

- `+QBLEADDR:` has no space after the colon; the MAC colons are checked at fixed offsets.
- `+QGETIP:` must be `ip:…,gateway:…,mask:…,dns:…` with no leading CRLF. The MCU
  scans `ip:` up to `gate` and `mask:` up to `dns`, then copies up to the first CRLF.
- `AT+QISEND=<id>,<len>,"<bytes>","<ip>",<port>` carries the payload inline. An
  empty IP means broadcast.
- The Local API is connection 3: `AT+QIOPEN=3,"UDP SERVICE","<ip>",2025,<port>,1`.
- EEPROM byte 0 marks an initialised store. The Local API enable flag is at 0x371
  and the port at 0x372 (uint16 LE).
- HMG-50 (`--dialect hmg50`):
  - The modem is on LPUART1.
  - `AT+QISEND` waits for `CONNECT` instead of `>`.
  - The CT socket is connection 0 and the Local API is connection 3.
  - EEPROM: 0x37C is the Local API flag, 0x37D its port, 0x371 `choose_meter`,
    0x372 the CT type (defaults 1 and 4), and 0x3000 the device type string.

## CAN bus facts learned from the firmware

On the Control board every frame is 29-bit extended, and `can_peers.py`
documents the payloads it sends. HMG-50 uses 11-bit Pylontech-layout frames on
FDCAN2 instead (`can_peers.py --protocol pylontech`):

- 0x351: limits
- 0x355: SoC/SoH and rated Wh
- 0x356: voltage, current and temperature
- 0x359: alarms
- 0x35C: request flags. The BMS counts as lost 20 s after the last one.

The MCU itself sends 0x305 and 0x300.

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

## Firmware debug log

The firmware logs to USART2 (`WORK/usart2.txt`) once enabled from the Renode
monitor (`telnet 127.0.0.1 41234`, after the launcher prints "Running"). The two
flags sit at the same addresses in every VNSE3-0, VNSA-0 and VNSD-0 image:

```
sysbus WriteByte 0x20000132 1   # logging on
sysbus WriteByte 0x2000012a 5   # all modules
```

On HMG-50 156 the flags are `sysbus WriteByte 0x20003155 1` and
`sysbus WriteByte 0x200003ec 1`.

That log shows the Venus E 3.0's own form of the HMG-50 shared-channel problem.
An Open API request that arrives while a CT reply is being handled logs
`Extract_udp_data ... ct failed to get the mutex lock!`. The request then gets
`Parse error` (`-32700`, data `403`) with `"id": 0`.

## HMG-50 and issue #82

Reproduced on 155 and 156. The emulator shows Local API polling disturbing the
CT on the shared FC41D channel in two ways
([#82](https://github.com/taurgis/has-marstek-local-api/issues/82),
`tools/firmware/HMG50_METER_CHANNEL.md`):

1. **Dropped requests.** Before every AT command, the send routine (156: near
   0x080074a0) takes the modem mutex, clears the 512-byte RX buffer, then waits
   up to 400 ticks for the reply. It polls the CT every ~1.1 s (`QISEND` on
   connection 0). A `+QIURC: "recv",3,…` that arrives during that window is
   either erased or swallowed inside the `OK` chunk. At one request every 2 s,
   20-30 % of requests were answered.
2. **CT retarget.** The meter URC parser (0x08015010) takes the first
   `+QIURC:` in the buffer without checking the connection id. After a parse,
   `Get_meter_IP` (0x080156fc) copies that URC's IP into the meter address, and
   the firmware reopens the CT socket to it
   (`AT+QIOPEN=0,"UDP SERVICE","<poller IP>",…`). After about 55 s of polling,
   the CT socket pointed at the Local API client. `ct_state` (0x100070e2) went
   from 1 to 0 and stayed there for minutes before the firmware went back to
   broadcast. With no polling, `ct_state` stayed 1 for a 3-minute run.

How the emulator differs from a real unit:

- Virtual time runs at about half speed.
- Cloud HTTP/MQTT answer `ERROR`, so the firmware keeps retrying.
- There is no power stage (the ADC reads 0). Battery current stays 0 and
  self-consumption (Auto) moves no power.

So the emulator shows the CT being lost, but it cannot show charging stalling.
