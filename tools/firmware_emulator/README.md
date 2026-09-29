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
| Clock tree, flash controller | always-ready register stubs | `renode/rcu.py`, `fmc.py` |
| RTC (BCD calendar) and backup registers | calendar on the host's wall clock; backup DATA0 preset | `renode/rtc.py`, `bkp.py` |
| Config EEPROM (I2C 0x50, 2-byte address) | file-backed 24Cxx | `renode/I2CEeprom.cs` |
| CH395 Ethernet (SPI2) | idle chip, answers 0x00 | `renode/spi2.py` |
| Block engine at 0xA0001000 | loopback FIFO | `renode/cau.py` |
| Quectel FC41D Wi-Fi/BLE modem (UART, IRQ 74) | AT-command emulator bridging `UDP SERVICE` sockets to host UDP | `fc41d.py` |
| CAN0 (BMS + inverter bus) | Renode `STMCAN` on a CAN hub, bridged to TCP 3457; inbound frames paced 1 per ms of virtual time | `renode/CanBridge.cs` |
| BMS and inverter | energy-counted battery (`--packs`, `--capacity`), inverter that follows the MCU's setpoint within the family's AC limits | `can_peers.py --family` |
| PV MPPT (Venus A/D) | up to four constant-power channels that charge the battery, day/month/year 0.01 kWh yield counters (persisted next to the EEPROM), MCU on/off | `can_peers.py --pv`, `--pv-energy`, `--pv-state` |
| EPS socket (Venus A) | constant off-grid load served from the battery | `can_peers.py --offgrid-load` |
| HMG-50 board (Venus C / E 2.0): STM32G474 map, USART/LPUART FIFO mode, RCC/PWR/FLASH/RTC/HRTIM/ADC/CORDIC stubs, FDCAN | Renode STM32 models + G4 stubs | `renode/hmg50.repl.in`, `STM32G4_USART.cs`, `g4*.py` |
| CT003 meter | [AstraMeter](https://github.com/tomquist/AstraMeter) reading `can_peers.py`'s grid meter | `astrameter/config.ini` |

Not modelled: RS485, inverter pages 2 and 7 (and 1 and 3 outside Venus D),
the per-pack BMS PGNs (the firmware only forwards them to the cloud and the
LEDs; no Open API field reads them) and the cloud (HTTP/MQTT get `OK` and no
data). Without the cloud, the RTC is the only clock source; see
[Deviations](#deviations-from-a-real-device).

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
reply is being handled; see [Firmware debug log](#firmware-debug-log). On an
overloaded host (load average about 20 on 4 cores) the CT sidecar left roughly half
of all Local API requests without a usable reply, since virtual time then runs
at about half speed and each CT reply blocks the parser for longer. Home
Assistant writes needed up to four attempts. Venus A and
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
   build is enough; tested with the 1.17.0 release and a 1.17.0 nightly). Or skip
   to [Docker](#docker), which brings its own.

3. Launch, reporting the IP that Home Assistant can reach this host on. For the
   devcontainer compose network that is the bridge gateway `172.28.0.1`:

   ```bash
   python3 tools/firmware_emulator/run_firmware.py \
       --renode /opt/renode/renode --ip 172.28.0.1 \
       --firmware VNSD-0:150 --work /tmp/marstek-fw/vnsd-150 \
       --pv 400,350,300,250 --ble-mac 02:ee:00:00:0d:02
   ```

   `--firmware DEVICE:VERSION` picks an image from the catalog (default: the
   VNSE3-0 150 blob; `--blob PATH` takes any file). A fresh EEPROM
   (`WORK/eeprom.bin`) gets its defaults on the first boot. `--local-api-port`
   (default 30000) is then forced on. The launcher reboots once if needed, then
   starts `can_peers.py` with `--soc`, `--house-load A,B,C`, `--pv` (W per
   channel, Venus A/D), `--pv-energy` (initial MPPT year yield in kWh),
   `--offgrid-load` (EPS socket W, Venus A), `--packs N`, `--capacity WH` and
   `--ac-limits DISCHARGE,CHARGE` (W). The last three default per family, taken
   from the catalog device type: VNSA-0 2080 Wh and 1500/1500 W; VNSD-0 2560 Wh
   per pack and 2500/2500 W; everything else 5120 Wh and 800/2500 W (Venus E
   3.0). Logs land in
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
       ghcr.io/tomquist/astrameter:2.3.1
   ```

   On a host with several emulators or other Marstek devices, every AstraMeter
   answers every device's CT003 broadcast. The Docker setup avoids that with
   `--ct-loopback`; see [Deviations](#deviations-from-a-real-device).

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

## Docker

`Dockerfile` builds one image for every Venus E 3.0 / A / D image. It installs
the **Renode 1.17.0** portable release (the current stable one, 2026-09-07),
checks the tarball against the SHA256 on the
[release page](https://github.com/renode/renode/releases/tag/v1.17.0), and adds
Debian's Python 3. The emulator and the committed blobs are copied in, so nothing
is fetched at run time. The portable build bundles .NET 8 and needs GTK only for
the GUI, which `--disable-gui` turns off
([Renode README](https://github.com/renode/renode/blob/master/README.md)). The
[`antmicro/renode`](https://hub.docker.com/r/antmicro/renode) image would also
work, but it tracks `latest` and ships a desktop stack this headless use does not need.

The entrypoint is `run_firmware.py --ip auto --eeprom /state/eeprom.bin
--ct-loopback --quantum 0.01 --log-max-mb 20`, and the command adds the rest:

```bash
docker build -f tools/firmware_emulator/Dockerfile -t marstek-fw-emulator .
docker run -d --name venus-d --network devcontainer_marstek_net --ip 172.28.0.52 \
    -v venus-d-state:/state marstek-fw-emulator \
    --firmware VNSD-0:150 --ble-mac 02:e0:00:00:00:52 --wifi-mac 02:e1:00:00:00:52 \
    --pv 400,350,300,250
```

- `--ip auto` reports the address the container routes to `--gateway` from, so
  the device reports its own container IP.
- `/state` holds the EEPROM (the firmware's own settings, mode included). Mount
  a named volume on it. Logs stay in `/tmp/marstek-fw` in the container
  (`docker exec <name> tail -f /tmp/marstek-fw/fc41d.log`) and rotate at 20 MB.
- The image's `HEALTHCHECK` (`healthcheck.py`) is healthy once
  `Marstek.GetDevice` answers on UDP 30000. It probes every 60 s, and every 10 s
  during a 240 s start period
  ([Dockerfile reference](https://docs.docker.com/reference/dockerfile/#healthcheck)).
  A fresh EEPROM boots twice, so the first start takes about 100-130 s.
- If a TLS-intercepting proxy is in the way of the Renode download, pass its
  CA bundle as the optional build secret `build_ca`:
  `docker build --secret id=build_ca,src=/path/ca.pem ...`. In compose, set
  `MARSTEK_BUILD_CA=/path/ca.pem`.

### In the devcontainer compose file

`.devcontainer/docker-compose.yml` runs the emulators as `fw-*` services on fixed
IPs, each with a unique BLE MAC (`02:e0:00:00:00:<last octet>`), Wi-Fi MAC
(`02:e1:…`) and named volume (`marstek-fw-<service>`):

| Service | IP | Firmware | Profile |
|---------|----|----------|---------|
| `fw-venus-e-150` | 172.28.0.50 | VNSE3-0 150 | default |
| `fw-venus-a-150` | 172.28.0.51 | VNSA-0 150, PV 420/360 W | default |
| `fw-venus-d-150` | 172.28.0.52 | VNSD-0 150, PV 400/350/300/250 W | default |
| `fw-venus-e-144`, `-147`, `-1476`, `-148`, `-149` | .53-.57 | VNSE3-0 | `firmware-all` |
| `fw-venus-a-148`, `-1487`, `-149`, `-1508`, `-1509` | .58-.62 | VNSA-0, PV 420/360 W | `firmware-all` |
| `fw-venus-d-147`, `-149`, `-1492` | .63-.65 | VNSD-0, four PV channels | `firmware-all` |

```bash
cd .devcontainer
docker compose up -d --build                  # HA, the mocks and the three defaults
docker compose --profile firmware-all up -d   # all sixteen emulators
docker compose --profile firmware-all stop fw-venus-e-144 fw-venus-e-144-ct   # one by one
```

Services without a profile always start. Profiled ones start only when their
profile is enabled with `--profile` or `COMPOSE_PROFILES`
([profiles](https://docs.docker.com/compose/how-tos/profiles/)).

Each `fw-*-ct` sidecar is AstraMeter 2.3.1, pinned by digest, with
`network_mode: service:fw-…`. It shares the emulator's network namespace, so it
reads that emulator's `can_peers.py` meter on `127.0.0.1:8099` and receives its
CT003 polls on loopback
([`network_mode`](https://docs.docker.com/reference/compose-file/services/#network_mode)).
It waits for `condition: service_healthy`, because can_peers only serves the
meter once the firmware runs
([`depends_on`](https://docs.docker.com/reference/compose-file/services/#depends_on)),
and restarts on failure. When an emulator container is recreated, compose
recreates its sidecar as well. After a plain `docker restart` of the emulator,
restart the sidecar too, since the namespace it joined is gone. EEPROM volumes
are [named volumes](https://docs.docker.com/reference/compose-file/volumes/):
`docker volume rm marstek-fw-<service>` gives that device a factory-fresh EEPROM.

CPU: the firmware never sleeps, so Renode spins at full emulated speed. Renode's
default 100 µs quantum costs about 1.4 cores per emulator. The image uses a
10 ms quantum (`--quantum 0.01`), which brings that down to about 0.3-0.6 cores.
The `firmware-all` services also run a slower MCU (`--mips 40`, Renode default
100), which keeps timers and baud rates but gives the firmware fewer
instructions per second. Sixteen emulators still oversubscribe a 4-core host
(load around 60). Start the profile to check an image, then stop it.

The HMG-50 (STM32G4) images run from the same image: `--firmware HMG-50:VERSION`
picks the `hmg50` board (`renode/hmg50.repl.in`), and the `fw-venus-c-*`
services below run them. `fw-venus-c-156` (172.28.0.66, 2560 Wh) starts by
default; 153, 155 and the 5120 Wh `fw-venus-e2-156` are under the
`firmware-hmg50` and `firmware-all` profiles.

HMG-50 replies to the Local API port (30000), not to the sender's port, so
`healthcheck.py` also accepts the reply the modem logs as sent to 127.0.0.1.
Expect a slow start in Home Assistant: the firmware drops most requests that
arrive while it talks to the modem (see [HMG-50 and issue
#82](#hmg-50-and-issue-82)). On a loaded host, the config flow can need a
second try, and the first full poll can take several minutes.

## Deviations from a real device

- **CT003 on loopback (`--ct-loopback`).** A real Venus broadcasts its CT003
  poll (UDP 12345) to the LAN, and every CT on the LAN hears it. With `fc41d.py
  --loopback-port 12345`, the modem binds the firmware's CT socket to
  `127.0.0.1` and sends every datagram for port 12345 to `127.0.0.1`,
  broadcasts included. Only the sidecar in the same namespace answers, with its
  own house, and the firmware sees `127.0.0.1` as the meter's address. Without
  this, each device's AstraMeter would answer every other device with the wrong
  house load.
- **`wifi_mac` is the modem's BSSID answer.** `Marstek.GetDevice` reports the
  `bssid=` field of `AT+QGETWIFISTATE` as `wifi_mac`. `--wifi-mac` sets that
  field (`fc41d.py --bssid`). Whether a real FC41D reports the AP's BSSID or its
  own MAC there is not verified.
- **Reply port.** The emulated modem replies to the requester's source port.
  Real firmware replies to the listen port
  (see `custom_components/marstek/helpers/udp_clients.py`), so `healthcheck.py`
  can use an ephemeral port, while Home Assistant's port-30000 client sees the
  same thing as with a real device.
- **CAN pacing.** Inbound CAN frames are spread 1 ms apart in virtual time.
  With a 10 ms quantum, a burst injected at once overflowed the 3-deep RX FIFO,
  and the lost BMS frames showed up as SoC 0 for minutes at a time.
- **Parse errors under load.** On a busy host the firmware answers a share of
  intact requests with `Parse error` (`data` 402: no payload buffer, 403:
  cJSON rejected the bytes it read), always with `id` 0. It retries fine, but
  config flows can need a second attempt, and long requests (a manual schedule)
  can fail every retry. Treat a burst of these as an emulator artefact, not
  firmware behaviour, until a real device shows it.
- **Clock.** A real unit gets its time from the cloud. The emulated RTC always
  reads the host's local time (UTC in the container unless `TZ` is set), so
  manual schedules fire on the host's wall clock. `bkp.py` presets the backup
  register that tells the firmware its clock kept running, so it does not rewind
  the RTC to its last saved time (see [Clock and write paths](#clock-and-write-paths-vnse3-0-144-150)).
- **Timing.** `--mips 40` (profile services) is a slower MCU than the GD32F30x.
  Replies can take a second or more under load. Home Assistant tolerates that.
  On an overloaded host (load well above the core count), the emulated MCU falls
  behind real time and the CT reply window grows. Long requests such as a
  Manual `ES.SetMode` (about 190 bytes) then hit the `Parse error` 403 path far
  more often than short polls do, and can fail all three of Home Assistant's
  attempts.

## FC41D facts learned from the firmware

- `+QBLEADDR:` has no space after the colon; the MAC colons are checked at fixed offsets.
- `+QGETIP:` must be `ip:…,gateway:…,mask:…,dns:…` with no leading CRLF. The MCU
  scans `ip:` up to `gate` and `mask:` up to `dns`, then copies up to the first CRLF.
- `AT+QISEND=<id>,<len>,"<bytes>","<ip>",<port>` carries the payload inline. An
  empty IP means broadcast.
- The CT003 client is connection 1: `AT+QIOPEN=1,"UDP SERVICE","",12345,22222,1`.
  It polls the directed broadcast of the reported IP/mask (e.g. `172.28.255.255`)
  about once a second.
- `Marstek.GetDevice`'s `wifi_mac` comes from the `bssid=` field of `AT+QGETWIFISTATE`.
- BLE advertising: `Ble.Adv` stores `enable` as the wanted state (EEPROM 0x36bd)
  and the BLE task sends `AT+QBLEADVSTART` for `1` and `AT+QBLEADVSTOP` for `0`,
  re-sending until `AT+QBLESTAT` reports `ADVERTISING` / `NOADVERTISING`.
  `fc41d.py` tracks that state; a fixed `ADVERTISING` left every disable
  request unanswered. The firmware only re-reads `AT+QBLESTAT` around a
  request, so a second change right after the first can be a no-op.
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
  current and power in 0.1 units). Venus E images parse MPPT frames too.
  Inverter page 4 gives `ongrid_power`/`offgrid_power`, page 5 the
  temperatures and AC limits, and page 6 the lifetime grid energy.
- MPPT pages 1-7 land in one 0x38-byte struct (debug names in brackets): 1
  state/err/war/temp, 2-4 the channels, 5 `capd` u32 + `pve`/`pvs` u8 + `pow` u16,
  6 `capm` u32 + `capy` u32, 7 MPPT-side battery readings. The yield counters
  are 0.01 kWh. `ES.GetStatus` reports `capy` unscaled as `total_pv_energy` on
  VNSA-0 1487/149/150/1508/1509 and VNSD-0 149/1492/150; Venus A 148 hard-codes
  `pv_power` and `total_pv_energy` to 0. `PV.GetStatus` reports PV1 power raw
  (0.1 W) and every other channel field ×0.1, truncated, on all six VNSA-0 builds.
- The MCU switches the MPPT with cmd 0x01 to type 2 addr 1 (byte 4: 1 on,
  0 off). It sends off only after an hour with no channel above 14 V and the
  inverter idle.
- Inverter page 1 is state/flags/warnings (byte 3: backup output enabled), 2
  error words, 3 grid volt and frequency, 7 two extra energy counters. The
  Venus D reads pages 1 and 3; `can_peers.py --family VNSD-0` sends them. None
  reaches the Open API, except that state 4 with `ongrid_power` 0 makes the
  firmware report off-grid power. `ES.GetStatus` hard-codes `total_load_energy`
  to 0 on every VNSA-0 build.
- The MCU switches the backup (EPS) output with cmd 0x02 (1 byte, 1 = on);
  `can_peers.py --family VNSD-0` echoes it in page 1.
- The MCU drives the inverter with cmd 0x01, a signed 32-bit AC setpoint in W
  (+ = discharge). It sends each new value three times, clamped to the BMS
  current limits × voltage. After boot it writes its stored energy counters
  back with cmd 0x16/0x17.
- Auto mode first runs a CT phase test: about 20 s of ±800 W, discharging above
  50 % SoC and charging below. The BMS permission bit for that direction must be
  set. The detected phase is stored at EEPROM 0x369.

## Venus D facts learned from the firmware

Checked by disassembling the VNSD-0 147, 149, 1492 and 150 images and on the
emulator with Home Assistant.

| | 147 | 149, 1492 | 150 |
|---|---|---|---|
| `ES.GetStatus` `pv_power`, `total_pv_energy` | always 0 | MPPT sum; year yield in 10 Wh | same as 149 |
| `ES.SetMode` UPS, `DOD.SET`, `BLE.Adv`, `Led.Ctrl` | `Method not found` | present | present |

- `bat_cap` is the BMS 0x1802 capacity (2560 Wh per pack). `total_pv_energy` is
  the MPPT year counter (`PV_Year_Cap_10Wh` in the firmware's debug strings),
  reported raw, so 1 = 10 Wh. `total_load_energy` is always 0.
- `PV.GetStatus` is the same on all four: `pv1_power` in 0.1 W, `pv2`-`pv4`
  in W, volts and amps rounded from 0.1 units, `pvN_state` = voltage above 14 V.
- On 150, a fresh EEPROM starts in Manual mode with DOD 88 and the AC limits 800 W
  discharge / 2500 W charge. The socket setting (`Set.Ver`, via the app) accepts
  800, 2200 and 2500 W; the inverter's own limit frame caps it further.
- `ES.GetMode` fills its CT fields (`ct_state`, `a_power`...) only in AI mode.

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
  `UPS` and the setpoint goes to full charge (about -2500 W). On 148 a later
  Manual `ES.SetMode` runs its slot but `ES.GetMode` still reports `UPS`; on
  150 Manual clears it. Home Assistant offers UPS only from 150, so on 147-149
  its select shows `unknown` while the device reports `UPS`.
- **DOD.SET** (147+, 30-88): the firmware stores the value and forwards it to the
  inverter as cmd 0x0A, byte 0 = 100 - DoD (the minimum SoC; EEPROM 0x201).
  `can_peers.py` moves its discharge floor and the 0x1802 DoD byte to match. A
  discharging Manual slot stays at 0 W while the SoC is below the floor. 144
  answers `Method not found`.
- **Ble.Adv / Led.Ctrl** (1476+): answer `set_result`. Ble.Adv drives
  `AT+QBLEADVSTART`/`STOP`, which `fc41d.py` acknowledges.
- `Bat.GetStatus` copies the whole 0x1803 permission byte into both
  `charg_flag` and `dischrg_flag`, so both stay true until the BMS withdraws
  charge *and* discharge. `can_peers.py` clears bit 0 at 100 % and bit 1 at
  the DoD floor.

In every VNSE3-0 build (144-150), both `Marstek.GetDevice` and
`Wifi.GetStatus` copy `wifi_mac` from the buffer the `bssid=` parser of
`+QGETWIFISTATE` fills (build 150: 0x2001a626). If the module reports the
access point there, as the field name says, all batteries on one AP share a
`wifi_mac`. Give each emulated device its own `--wifi-mac`. Two devices that
share one make Home Assistant's scanner treat them as one device and move the
first one's config entry to the other's IP (seen in the devcontainer).

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
