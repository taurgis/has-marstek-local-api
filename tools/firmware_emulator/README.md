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
| CH395 Ethernet (SPI2) | idle chip, answers 0x00; with `--ethernet` a CH395 with an instant DHCP lease, UDP sockets bridged to host UDP, TCP that fails to connect | `renode/spi2.py`; `--ethernet`: `renode/ch395_spi.py` + `ch395.py` |
| Block engine at 0xA0001000 | loopback FIFO | `renode/cau.py` |
| Quectel FC41D Wi-Fi/BLE modem (UART, IRQ 74) | AT-command emulator bridging `UDP SERVICE` sockets to host UDP | `fc41d.py` |
| Marstek cloud (MQTT broker, HTTP servers behind the FC41D) | accepts every connect, publish and upload; `getDateInfo` returns host time; `--no-cloud` fails them | `cloud.py` |
| FC41D reset line (Control: PE4/PE5) | write hook on `gpioPortE` sends the level to UDP 3466; the modem drops its sockets while low, then boots (`--boot-seconds`, 1) and rejoins (`--rejoin-seconds`, 4) | `run_firmware.py` `MODEM_RESET_HOOK`, `fc41d.py --reset-port` |
| CAN0 (BMS + inverter bus) | Renode `STMCAN` on a CAN hub, bridged to TCP 3457; inbound frames paced 1 per ms of virtual time | `renode/CanBridge.cs` |
| BMS and inverter | energy-counted battery (`--packs`, `--capacity`), inverter that follows the MCU's setpoint within the family's AC limits | `can_peers.py --family` |
| PV MPPT (Venus A/D) | up to four constant-power channels that charge the battery, day/month/year 0.01 kWh yield counters (persisted next to the EEPROM), MCU on/off | `can_peers.py --pv`, `--pv-energy`, `--pv-state` |
| EPS socket (Venus A) | constant off-grid load served from the battery | `can_peers.py --offgrid-load` |
| Venus E mini pack, DCDC and inverter (UART AA links) | energy-counted 2S LFP pack (2009 Wh, `--soc`, `--capacity`) behind a DCDC that reports its voltage, current and temperature, and an inverter that follows the PMU's forced direction and power; the PMU counts SoC and energy itself | `mini_peers.py` |
| BMS charge counter | reported SoC from a coulomb counter that can miss charge or over-count discharge, and resets at full by a jump, a creep or a hold at 99% (`SocCounter`); honest by default | `can_peers.py --bms-charge-error`, `--bms-discharge-error`, `--bms-resync` (also on `run_firmware.py`) |
| HMG-50 board (Venus C / E 2.0): STM32G474 map, USART/LPUART FIFO mode, RCC/PWR/FLASH/RTC/HRTIM/ADC/CORDIC stubs, FDCAN | Renode STM32 models + G4 stubs | `renode/hmg50.repl.in`, `STM32G4_USART.cs`, `g4*.py` |
| CT003 meter | [AstraMeter](https://github.com/tomquist/AstraMeter) reading `can_peers.py`'s grid meter | `astrameter/config.ini` |

`tools/capacity_drive.py` cycles a device in Passive mode and feeds every poll to the
integration's capacity estimator, so a drifting BMS shows up as the correction Home Assistant
would record. `--out` saves the polls; `--replay FILE --seed-wh 1536` reruns them with a
published capacity, which is the only way a creep is visible.

Not modelled: RS485, inverter pages 2 and 7 (and 1 and 3 outside Venus D),
and the per-pack BMS PGNs (the firmware only forwards them to the cloud and the
LEDs; no Open API field reads them).

The Marstek cloud is a stand-in (`cloud.py`, behind `fc41d.py`): MQTT opens,
connects, subscribes and publishes succeed, `getDateInfo` returns the host's
local time, data uploads get `{"code":0}` and every other HTTP request gets
`{}`. Nothing is ever sent down to the battery. So the default emulators behave
like a battery with working internet. `--no-cloud` makes MQTT and HTTP fail the
way they do when a firewall blocks the battery, and Control 150 and newer then
reset the FC41D about every 30 minutes (see
[Network chip resets](../firmware/ANALYSIS.md#network-chip-resets)).

## Images

Every VNSE3-0, VNSA-0 and VNSD-0 image in `tools/firmware/catalog.json` boots on
the same board and serves the Local API. Checked with `Marstek.GetDevice`,
`ES.GetStatus`, `ES.GetMode`, `Bat.GetStatus`, `PV.GetStatus`, `EM.GetStatus`,
`Wifi.GetStatus` and `BLE.GetStatus`:

| Family | Versions | Reports |
|--------|----------|---------|
| VNSE3-0 | 144, 147, 1476, 148, 149, 150, 151 | `VenusE 3.0`; `PV.GetStatus` is `Method not found` |
| VNSA-0 | 148, 1487, 149, 1491, 150, 1509 (ems), 151 (ems) | `Venus A` |
| VNSA-0 | 1508 (banner VEPRO-0) | `VenusE Pro` |
| VNSD-0 | 147, 149, 1492, 150, 151 | `Venus D` |

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
   3.0). `--no-cloud` blocks the stand-in Marstek cloud. Logs land in
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

## Ethernet (CH395)

The Control board has a WCH CH395 Ethernet chip on SPI2. By default it sits
idle and the firmware only uses Wi-Fi. `--ethernet` (Control images only)
connects it:

```bash
python3 tools/firmware_emulator/run_firmware.py --firmware VNSE3-0:150 \
    --ip 172.17.0.3 --gateway 172.17.0.1 \
    --ethernet --eth-ip 172.28.0.2 --eth-gateway 172.28.0.1
```

`ch395.py` plays the chip. `renode/ch395_spi.py` forwards each SPI byte to it
over TCP (`--eth-port`, default 3458), with two bus hooks for chip select
(PB12) and INT# (PD8). The chip hands out `--eth-ip`, `--eth-gateway`,
`--eth-netmask` and `--eth-mac` as its DHCP lease the moment the firmware
enables DHCP. Each UDP socket the firmware opens is a host socket bound to
`--eth-ip`, plus listeners on the subnet and limited broadcast addresses.
`--eth-ip auto` (the default) picks the host's address on the route to
`--eth-gateway`. The log is `WORK/ch395.log`.

Ethernet needs an address of its own. In Docker, attach the container to a
second network and give the Wi-Fi side the other one:

```bash
docker create --name fw-eth --network bridge marstek-fw-emulator \
    --firmware VNSE3-0:150 --gateway 172.17.0.1 \
    --ethernet --eth-gateway 172.28.0.1
docker network connect devcontainer_marstek_net fw-eth
docker start fw-eth
```

Connect the second network before the start: `--eth-ip auto` is resolved when
the launcher starts.

Once the chip has its lease, the firmware moves its uplink to Ethernet
(`0x20001f55` = 2, set by `0x08027c08`). The modem stays associated, but the
firmware drops its Wi-Fi sockets, so the Local API, the CT003 poll, DNS and
the cloud all go over the CH395. The Wi-Fi address then stops answering.

The stand-in cloud only answers on Wi-Fi. Over Ethernet the firmware does its
own DNS, HTTPS and MQTT through CH395 sockets, and `ch395.py` answers no DNS
query and no TCP connect. So an Ethernet unit behaves like one whose internet
access is blocked: `[CH395] MQTT init err`, `[HTTP] upload data error`, and
the upload queue (`0x20001004`) grows by one record about every 5 minutes.
Control 150 then resets the CH395 every 1800 s of virtual time, as on a real
unit. In one run (VNSE3-0:150, booted 10:03:43) the first reset came at
11:05:39, 62 wall-clock minutes later, because virtual time runs at about half
speed. The firmware logged `CH395 reset!!!` and `[HTTP]ch395 reset!!!!`,
re-sent `INIT_CH395`, enabled DHCP again and reopened the Local API socket
0.4 s later. A request every 5 s never went unanswered, because the model
hands out its lease at once; a real unit takes 5 to 9 s for DHCP.

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
| `fw-venus-a-150` | 172.28.0.51 | VNSA-0 150, PV 420/360 W, Open API port 30004 | default |
| `fw-venus-d-150` | 172.28.0.52 | VNSD-0 150, PV 400/350/300/250 W | default |
| `fw-venus-e-144`, `-147`, `-1476`, `-148`, `-149` | .53-.57 | VNSE3-0 | `firmware-all` |
| `fw-venus-a-148`, `-1487`, `-149`, `-1508`, `-1509` | .58-.62 | VNSA-0, PV 420/360 W; 148 on port 30001, 149 on 30003 | `firmware-all` |
| `fw-venus-d-147`, `-149`, `-1492` | .63-.65 | VNSD-0, four PV channels | `firmware-all` |
| `fw-venus-e-151`, `fw-venus-d-151` | .70-.71 | VNSE3-0 151, VNSD-0 151 (four PV channels) | `firmware-all` |
| `fw-venus-a-1491`, `fw-venus-a-151` | .73-.74 | VNSA-0 1491 and 151, PV 420/360 W | `firmware-all` |
| `fw-venus-e-mini-301` | .75 | VNSEMINI-0 PMU 301 (32-byte header, load `0x08008000`). Modem is USART2. Settings in SPI1 NOR (`renode/spiflash.py`). `mini_peers.py` simulates the pack, DCDC and inverter (`--soc`, `--capacity`); `fc41d.py --autojoin --netmask 255.255.255.0 --lan-broadcast`. Opens UDP and answers the Open API itself; replies go to `<a.b.c>.255`. See `tools/firmware/ANALYSIS.md` | `firmware-all` |

```bash
cd .devcontainer
docker compose up -d --build                  # HA, the mocks and the four defaults
# One named batch; naming the -ct sidecar also starts its emulator
docker compose up -d homeassistant fw-venus-e-148-ct fw-venus-d-149-ct
docker compose rm -sf fw-venus-e-148-ct fw-venus-e-148 fw-venus-d-149-ct fw-venus-d-149
```

The four defaults are `fw-venus-e-150`, `-a-150`, `-d-150` and the HMG-50
`fw-venus-c-156`. `docker compose --profile firmware-all up -d` starts all
twenty-five emulators. That needs a large host: each takes 0.3-0.6 core and
about 550 MB, and on a 4-CPU sandbox the load reaches about 60 and the firmware
answers `Parse error` or times out. On a small machine, run at most `nproc`
emulators at a time in named batches; see AGENTS.md, *Sandbox resource budget*.

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
- **Venus E mini PMU 301 link.** The mini board runs `fc41d.py --autojoin
  --netmask 255.255.255.0 --lan-broadcast`. `--autojoin` reports `GOT_IP`
  right after `AT+QWLMAC`, like a provisioned module after power-up; the
  image only asks for an address in its first 750 control ticks. The PMU
  replies to `<a.b.c>.255` whatever mask it is given, so the module reports
  a /24 and `--lan-broadcast` sends that address as `255.255.255.255`, which
  reaches Home Assistant on the /16 Docker network. On a real LAN that is not
  a /24 the replies are lost. The image also hears its own broadcast replies
  back on port 30000 and drops them (they carry no `method`).
  `mini_peers.py` plays the inverter (USART4) and DCDC (USART5) around an
  energy-counted 2S LFP pack and only drains the UART7 Modbus link. Its pack
  energy is kept in `mini_pack.json` next to the EEPROM. Every reply echoes
  the polled command, and register writes are echoed back, because the PMU
  retries a poll or a write until the reply matches. Manual mode drives the
  inverter through `cmd 0x72` (direction and watts); Passive is not
  implemented in this image. The PMU computes SoC and the energy totals
  itself from the DCDC current and the inverter's AC power. Its counter is
  saved to flash only on its power-down path, so after every boot the
  launcher asks it to restart SoC from the cell voltage once readings arrive
  (`0x2001a020` = 1). That lookup is in whole percent and the LFP plateau is
  flat, so the PMU can start a percent or two away from the pack. Field map
  and evidence: the `mini_peers.py` docstring. Renode timing is patched for
  this board only: the link UARTs are clocked at 36 MHz, TIMER6 at 72 MHz
  (the PMU's millisecond clock; at 10 MHz its SoC and energy ran at 0.14x),
  SysTick at 144 MHz (its task timers; at 72 MHz the energy totals ran at
  half rate), and a bus hook keeps UART7 TC set (Renode raises TC once per
  DR write). TIMER6's DMA line is left off because it shares DMA2 request 3
  with USART4 RX.
- **CH395 (`--ethernet`).** The DHCP lease arrives at once, with the PHY
  already linked at 100 Mbit/s full duplex. Only UDP is bridged: a TCP connect
  times out after `--tcp-timeout` (3 s), and TCP listen sockets (Modbus TCP on
  502) never see a client, so the firmware's cloud and debug-log TCP paths
  just fail. `GET_RECV_LEN` reports one datagram at a time. The PD9 hardware
  reset is not modelled; `INIT_CH395` resets the socket state instead. The
  Ethernet address in `Marstek.GetDevice` comes out zero-padded
  (`172.28.00.02`): the firmware formats it with `%02d`, as a real unit would.
- **CAN pacing.** Inbound CAN frames are spread 1 ms apart in virtual time.
  With a 10 ms quantum, a burst injected at once overflowed the 3-deep RX FIFO,
  and the lost BMS frames showed up as SoC 0 for minutes at a time.
- **Parse errors under load.** On a busy host the firmware answers a share of
  intact requests with `Parse error` (`data` 402: no payload buffer, 403:
  cJSON rejected the bytes it read), always with `id` 0. It retries fine, but
  config flows can need a second attempt, and long requests (a manual schedule)
  can fail every retry. Treat a burst of these as an emulator artefact, not
  firmware behaviour, until a real device shows it.
- **Clock.** A real unit gets its time from the cloud, and so does the
  emulator (the stand-in answers `getDateInfo` with host time). The emulated RTC always
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
  Venus E mini PMU 301 opens it as `AT+QIOPEN=3,"UDP SERVICE","",<port>,<port>,1`,
  then sends `AT+QGETIP=station` and `AT+QICFG="udp/broadcast",1`.
- EEPROM byte 0 marks an initialised store. The Local API enable flag is at 0x371
  and the port at 0x372 (uint16 LE).
- Control resets the module itself through `0x0800b224`: it takes the modem
  mutex, drives PE5 and PE4 low (GPIOE BRR), waits 1 s, sets them high (BSRR)
  and holds the mutex 2 s more. It then re-runs `AT+QSSLCERT?` and the BLE
  setup but sends no `AT+QSTAAPINFO` and no `AT+QIOPEN` of its own: the module
  rejoins from its saved network, and the `AT+QISTATE` checks reopen the Local
  API (connection 3) within seconds and the CT socket (connection 1) about
  75 s after the pulse.
- Cloud (Control 150 and HMG-50 156 both match these byte for byte):
  `+QMTOPEN: 0,0`, `+QMTCONN: 0,0,0`, `+QMTSUB: 0,1,0,0`, `+QMTPUB: 0,0,0`,
  and `+QMTCONN: 0,3` for `AT+QMTCONN?` (Control 150's handler around
  `0x08010bbc`). HTTP is `AT+QHTTPCFG="url",...`, then `AT+QHTTPGET=60` or
  `AT+QHTTPPOST=<len>,60,60` (body after `CONNECT`), then `AT+QHTTPREAD=60`
  until `+QHTTPREAD: 0`. Control uploads about every 5 minutes to
  `data-upload/v1/venus/<uid>`; with a reply its queue (`upload_cnt`) stays at
  1. HMG-50 156 also sends `setAcCoupleReporting` 12 times after boot and then
  stops when the reply is `{}`.
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
  VNSA-0 1487/149/1491/150/1508/1509/151 and VNSD-0 149/1492/150; Venus A 148 hard-codes
  `pv_power` and `total_pv_energy` to 0. `PV.GetStatus` reports PV1 power raw
  (0.1 W) and every other channel field ×0.1, truncated. Venus A 1491 and 151
  do the same: `--pv 420,360` came back as `pv1_power` 4200 and `pv2_power` 360,
  and Home Assistant showed 420.0 W and 360 W.
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
MQTT "Set local time", BLE). The stand-in cloud answers `getDateInfo` with the
host's local time as `_YYYY_MM_DD_HH_MM_SS`: the parser (`0x08015b74`) reads
the digits after the first `_`, validates them and sets the RTC. At boot the firmware
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
  goes to 0 and `ES.GetMode` keeps reporting `Passive`. It can also end far
  sooner: in the Docker image (`--quantum 0.01`) on a lightly loaded host, a
  `cd_time` of 7200 dropped the setpoint after about two minutes. A long
  charge or discharge test re-sends `ES.SetMode` every minute.
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
- The cloud is a stand-in that accepts everything and sends nothing down.
- There is no power stage (the ADC reads 0). Battery current stays 0 and
  self-consumption (Auto) moves no power.

So the emulator shows the CT being lost, but it cannot show charging stalling.
