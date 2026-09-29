# Firmware emulator (Renode)

Runs the **unmodified vendor Control firmware** of a Venus E 3.0 (VNSE3-0,
build 150) in [Renode](https://renode.io/), with the chips around the MCU
modelled well enough for the firmware to boot, join "Wi-Fi" and serve the
Local API on real UDP port 30000 of this host. Home Assistant then adds and
polls it like a physical device.

This complements `tools/mock_device/`: the mock is our reading of the protocol,
while this emulator is the vendor's own implementation, including its quirks
(reply formatting, dropped requests while the modem is busy, `set_result`).

## What is modelled

| Part | Model | File |
|------|-------|------|
| MCU (Cortex-M4F, STM32F1/GD32F30x-style map) | Renode `stm32f103` base, M4F core | `renode/vnse3.repl.in` |
| Clock tree, flash controller, RTC | always-ready register stubs | `renode/rcu.py`, `fmc.py`, `rtc.py` |
| Config EEPROM (I2C 0x50, 2-byte address) | file-backed 24Cxx | `renode/I2CEeprom.cs` |
| CH395 Ethernet (SPI2) | idle chip, answers 0x00 | `renode/spi2.py` |
| Block engine at 0xA0001000 | loopback FIFO | `renode/cau.py` |
| Quectel FC41D Wi-Fi/BLE modem (UART, IRQ 74) | AT-command emulator bridging `UDP SERVICE` sockets to host UDP | `fc41d.py` |
| CAN0 (BMS + inverter bus) | Renode `STMCAN` on a CAN hub, bridged to TCP 3457 | `renode/CanBridge.cs` |
| BMS and inverter | energy-counted battery, inverter that follows the MCU's setpoint | `can_peers.py` |
| CT003 meter | [AstraMeter](https://github.com/tomquist/AstraMeter) reading `can_peers.py`'s grid meter | `astrameter/config.ini` |

Not modelled: RS485, PV (the Venus E has none) and the cloud (HTTP/MQTT get `OK`
and no data).

## Run it

1. Download the firmware (blobs are Marstek/Hamedata copyright and stay out of git):

   ```bash
   python3 tools/firmware/fetch_firmware.py   # writes tools/firmware/blobs/
   ```

2. Install [Renode](https://github.com/renode/renode/releases) (the portable Linux
   build is enough; tested with 1.17).

3. Launch, reporting the IP that Home Assistant can reach this host on. For the
   devcontainer compose network that is the bridge gateway `172.28.0.1`:

   ```bash
   python3 tools/firmware_emulator/run_vnse3.py \
       --renode /opt/renode/renode --ip 172.28.0.1
   ```

   A fresh EEPROM (`/tmp/vnse3/eeprom.bin`) gets its defaults on the first boot.
   `--local-api-port` (default 30000) is then forced on. The launcher reboots once
   if needed, then starts `can_peers.py` (`--soc`, `--house-load A,B,C` in W).
   Logs land in `/tmp/vnse3/`: `fc41d.log`, `can_peers.log`, `renode.log`.

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
   `VenusE 3.0 v150 (MarstekEmu) - 172.28.0.1`.

Only the Venus E 3.0 image is wired up. The HMG-50 (Venus C / E 2.0) image targets
an STM32G4, which Renode has no platform for yet.

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
  addr << 8 | cmd`: MCU 0, inverter 4, types 2 and 3 not identified. Inverter
  replies use cmd 0x10. Page 4 gives `ongrid_power`/`offgrid_power`, page 5 the
  AC limits, and page 6 the lifetime grid energy.
- The MCU drives the inverter with cmd 0x01, a signed 32-bit AC setpoint in W
  (+ = discharge). It sends each new value three times, clamped to the BMS
  current limits × voltage. After boot it writes its stored energy counters
  back with cmd 0x16/0x17.
- Auto mode first runs a CT phase test: about 20 s of ±800 W, discharging above
  50 % SoC and charging below. The BMS permission bit for that direction must be
  set. The detected phase is stored at EEPROM 0x369.

## Firmware debug log

The firmware logs to USART2 (`/tmp/vnse3/usart2.txt`) once enabled from the
Renode monitor (`telnet 127.0.0.1 41234`, after the launcher prints "Running"):

```
sysbus WriteByte 0x20000132 1   # logging on
sysbus WriteByte 0x2000012a 5   # all modules
```

That log shows the Venus E 3.0's own form of the HMG-50 shared-channel problem.
An Open API request that arrives while a CT reply is being handled logs
`Extract_udp_data ... ct failed to get the mutex lock!`. The request then gets
`Parse error` (`-32700`, data `403`) with `"id": 0`.
