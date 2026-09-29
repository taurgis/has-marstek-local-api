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

Not modelled yet: the BMS and inverter on CAN, the CT meter and RS485. The firmware
therefore reports SoC, power and energy as 0, but every Open API method answers.

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
   if needed. The modem log is `/tmp/vnse3/fc41d.log`.

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
