# HMG-50 shares one Wi-Fi receive channel with its own meter (issue #82)

Issue [#82](https://github.com/taurgis/has-marstek-local-api/issues/82) reports a
**Venus C 2.0** on firmware **153** and **156** that stops charging from excess
solar while Home Assistant polls the Open API. Self-consumption resumes when
Open API is toggled in the app, when Home Assistant loses the connection, or
when the integration is disabled. Open API itself stays enabled throughout, so
this is not the reset/wipe defect tracked in
[#15](https://github.com/taurgis/has-marstek-local-api/issues/15).

Marstek's own integration carries the matching warning for the other model
built on the same Control firmware: "This integration is currently not
compatible with Venus E2.0 devices. Using this integration with Venus E2.0 may
cause disconnection between the device and CT003"
([MarstekEnergy/ha_marstek README](https://github.com/MarstekEnergy/ha_marstek)).
A community report on a different client sees the same thing from the CT side:
three Venus E 2.0 units flip "CT connected" off several times an hour with the
local API active, and stay stable with it off
([jaapp/ha-marstek-local-api#133](https://github.com/jaapp/ha-marstek-local-api/issues/133)).

Venus C 2.0 and Venus E 2.0 ship the **same Control binaries**. The vendor OTA
tree files both under `HMG-50`, and `Marstek.GetDevice` on those images answers
`VenusC` or `VenusE` from one build (`catalog.json`, and
`firmware_profile.py`). So a Venus C report and a Venus E 2.0 report are reports
about one firmware.

## What the images show

Blobs are not stored in git. `fetch_firmware.py` downloads them; every hash
below verified against `catalog.json`.

The Control firmware talks to the Wi-Fi module (Quectel FC41D) over AT
commands. Inbound socket data always raises `+QIURC: "recv",<connect_id>`; in
buffer access mode (`AT+QIOPEN` access_mode 0) the URC carries no payload and
the application drains it with `AT+QIRD`, while in direct push mode
(access_mode 1) the data is in the URC itself
([FC41D AT Commands Manual](https://quectel.com/content/uploads/2024/02/Quectel_FC41D_AT_Commands_Manual_V1.0-1.pdf),
sections 2.3.2, 2.3.5 and 3.3.2). Counting which parsers and read sites each
image contains separates HMG-50 from every other line:

| device | ver | `"recv",%d,` | `"recv",3` | `"recv",11` | `AT+QIRD` sites | `accept/mode` |
|---|---|---|---|---|---|---|
| HMG-50 | 153 | no | yes | no | 4 | 2 |
| HMG-50 | 155 | no | yes | no | 4 | 2 |
| HMG-50 | 156 | no | yes | no | 4 | 2 |
| VNSA-0 | 148 | yes | yes | no | 0 | 0 |
| VNSA-0 | 1487 | yes | yes | yes | 0 | 0 |
| VNSA-0 | 149 | yes | yes | yes | 0 | 0 |
| VNSA-0 | 150 | yes | yes | yes | 0 | 0 |
| VNSA-0 | 1509 | yes | yes | yes | 0 | 0 |
| VNSD-0 | 149 | yes | yes | yes | 0 | 0 |
| VNSD-0 | 1492 | yes | yes | yes | 0 | 0 |
| VNSD-0 | 150 | yes | yes | yes | 0 | 0 |
| VNSE3-0 | 144 | yes | yes | no | 0 | 0 |
| VNSE3-0 | 148 | yes | yes | no | 0 | 0 |
| VNSE3-0 | 149 | yes | yes | yes | 0 | 0 |
| VNSE3-0 | 150 | yes | yes | yes | 0 | 0 |

The last column counts `AT+QICFG="accept/mode"` sites. That setting governs
whether inbound **TCP** connections on a listener socket are accepted
automatically or with `AT+QIACCEPT` (FC41D manual, section 2.3.1); it is listed
here only because HMG-50 is the only line that configures it, not because it
touches the receive path.

Reproduce with:

```bash
python3 tools/firmware/fetch_firmware.py
for f in tools/firmware/blobs/*.bin; do
  echo "== $f"
  strings -a "$f" | grep -aE '\+QIURC: "recv"|AT\+QIRD|accept/mode'
  strings -a "$f" | grep -aE 'AT\+QIOPEN=%d,"(UDP SERVICE|TCP)"|AT\+QHTTPCFG="url","http://%s/(api/v1/data|v1/json)"'
done
```

Three things follow.

**HMG-50 has no generic receive-channel parser and one hard-coded connect id.**
The URC dispatcher does tokenize — `+QIURC:` (156: `0x128d3`) is followed by
`{"id"` for an inbound Open API request, and `recv` (`0x12f9c`) sits beside
`tmp_ip` / `g_szMonitorIP` where the sender address is extracted so the server
can reply. What HMG-50 does *not* have is the `+QIURC: "recv",%d,` format
string every other line carries, and the only hard-coded connect id in the
image is `+QIURC: "recv",3` (156: `0x316e1`), sitting in the meter/CT task
region. HMG-50 is also the only line that drains sockets with buffered
`AT+QIRD` reads (`0x23bf4`, `0x23c04`); VNSE3-0 150 has no `AT+QIRD` site at
all.

**Connect id 3 is the Local API server's socket.** The server open sits together
in one region (156: `0x0800cc54`):

```
cc54 AT+QIOPEN=%d,"UDP SERVICE","%s",%d,%d,%d
cc84 +QIOPEN: 3,0
cc97  UDP server open!
ccac UDP server open err!
```

The expected reply `+QIOPEN: 3,0` is a literal: the Local API listener is
connect id 3 on this firmware.

**The meter side is not one client, and not all of it is UDP.** Four transports
exist, and only two of them touch the socket path the Local API server uses.
Offsets are 156; 153 and 155 carry all four at different offsets.

| Meter | Transport | Evidence (156) |
|---|---|---|
| Marstek's own CT / meter family and Shelly | **UDP socket**, access mode 1 (direct push) | `0x217b4` |
| `hame` LAN discovery / device link | **UDP socket**, access mode 0 (buffered `AT+QIRD`) plus a TCP socket | `0x23b6b` |
| HomeWizard P1 | **HTTP** (`AT+QHTTPGET` / `AT+QHTTPREAD`) | `0x9d4c` |
| Eco-Tracker | **HTTP**, same client | `0x9d78` |

The UDP meter client opens its socket and polls the meter itself (156:
`0x080217b4`):

```
217b4 AT+QICFG="accept/mode",0
217d3  AT+QIOPEN=%d,"UDP SERVICE","%s",%d,%d,1
2180f  {"id":%d,"method":"EM.GetStatus","params":{"id":0}}
21844 {"id":%d,"method":"EM1.GetStatus","params":{"id":0}}
2187c AT+QISEND=%d,%d,"%s","%s",%d
2189c a_act_power   218a8 b_act_power   218b4 c_act_power
218c0 total_act_power                   21904 act_power
218d0 HME-   218d8 SMR-   218e0 TPM2-
218e8 shellyemg3   218f4 shellyproem50
```

`EM.GetStatus` / `EM1.GetStatus` is Shelly's own RPC, and the response fields
match Shelly's two energy-meter components: `a_act_power` / `b_act_power` /
`c_act_power` / `total_act_power` belong to the three-phase `EM` component (as
on a Pro 3EM), and `act_power` to `EM1`, which is what the two model
identifiers present here actually expose — both Shelly EM Gen3 (`shellyemg3`)
and Shelly Pro EM (`shellyproem50`) carry two `em1:` instances and no `EM`
([EM](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EM/),
[EM1](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EM1/)).
So the client covers both single-channel and three-phase Shellys. Shelly's RPC
over UDP is disabled by default and has no documented default port — it is
turned on per device by setting a `listen_port`
([RPC channels](https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels/),
[Sys component](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys/)),
which is what the firmware's configurable `shelly_port=` (`0xff08`) pairs with.
The
`HME-` / `SMR-` / `TPM2-` prefixes beside them match Marstek's own meter
identifier table (`HME-4`, `HME-3`, `SMR-0`, `SMR-1`, `SMR-2`, `TPM2-0`, at
`0x1e9cb` and `0x24407`). So one UDP client serves both Marstek's CT hardware
and a Shelly. Its task functions sit next to the one hard-coded connect id
(156: `0x31649`): `OKTCP_Analy`, `Udp_Tcp_Init_Read`, `CT_Loop_Read`,
`Get_dev_ip`, `Check_ct_status`, `Get_meter_IP`, `Parse_shelly_udp_data`,
`Check_ct_ip`, `Parse_new_ct_data`, `Shelly_Task_loop`, then
`+QIURC: "recv",3`. The CT payload it decodes is `ct002_get_info.*`
(`0x23f3c` onward: `meter_dev_type`, `meter_mac_code`, `hhm_dev_type`,
per-phase power, `wifi_rssi`).

A HomeWizard P1 or an Eco-Tracker is read differently — over the module's own
HTTP client, whose responses come back through `AT+QHTTPREAD` rather than a
`recv` URC:

```
9d4c AT+QHTTPCFG="url","http://%s/api/v1/data"      (HomeWizard P1)
9d78 AT+QHTTPCFG="url","http://%s/v1/json"          (Eco-Tracker)
8354 AT+QHTTPGET=%d
84ac AT+QHTTPREAD=60
```

Their failure strings are tagged accordingly:
`!!! [HTTP] Warning : P1 METER DISCONNECT!!!` (`0x8a73`),
`!!! [HTTP] Warning : ECO-TRACKER DISCONNECT!!!` (`0x8aa4`) and
`!!! [HTTP] Warning : GET METER ERR!!! meter_disconnect_cnt = %d!` (`0x32704`).

So a Venus C on a CT003 or a Shelly has **two UDP consumers on one hard-coded
channel** — the Local API server and the meter client — while a Venus C on a
HomeWizard P1 or an Eco-Tracker reads its meter over a separate AT service.
Home Assistant's Open API datagrams land where the *UDP* meter's replies do.
Lose enough meter samples and `Check_ct_status` declares the meter gone; Auto
mode then has no grid reading to regulate on and stops charging. That is the
reported symptom, and it is the same mechanism as the CT003 disconnection
Marstek warns about on Venus E2.0.

The HTTP client is genuinely a separate service: its responses are retrieved
with `AT+QHTTPREAD` and it has its own URCs, never `+QIURC: "recv"` or
`AT+QIRD` (FC41D manual, sections 2.6.1-2.6.5 and 3.6.5). But choosing an HTTP
meter is not a clean escape. The FC41D exposes one main UART for AT commands
and data, with the second serial port reserved for debug logging
([FC41D Hardware Design](https://www.sigmaelectronica.net/wp-content/uploads/2022/08/Quectel_FC41D_Hardware_Design_V1.0.1_Preliminary_20210623.pdf),
section 3.6.1), so `QHTTPGET` / `QHTTPREAD` and the Local API's socket traffic
still serialize on one interface and one URC stream. It moves the meter off the
contested socket, not off the contested link.

**The meter architecture is the same on the lines that do not stall.** VNSE3-0
150 polls Shelly with the identical `EM.GetStatus` / `EM1.GetStatus` RPC, carries
the same `HME-` / `SMR-` / `TPM2-0` identifiers, and reads a HomeWizard P1 and an
Eco-Tracker over the same HTTP client. What it has and HMG-50 does not is the
generic `+QIURC: "recv",%d,` parser and a second hard-coded channel. The
difference is the receive path, not the meter support.

**Generation 149 is where the other lines got a second channel.** VNSE3-0 144
and 148 and VNSA-0 148 have one hard-coded id; 149 and everything after it
(including VNSA-0 148.7) add `+QIURC: "recv",11`. That lines up with the
vendor's Local API fixes in the same window — VNSE3-0 150 is "Optimized Local
API send anomaly on Ethernet", confirmed on issue #15, and VNSA-0/VNSD-0 150
are "Fixed faulty Local API transmission in Ethernet mode"
(`catalog.json`, `ANALYSIS.md`).

**No HMG-50 build has it.** Not 153, not 155, not 156. HMG-50 156's OTA note is
"Optimized OpenApi interface stability", which this repo already uses for
`_HMG50_OPENAPI_STABLE_GENERATION`; it covers the API surface, not the shared
receive channel. There is no HMG-50 image in either community archive that adds
the second channel, so a firmware update does not currently clear this.

## What the integration does about it

Nothing on the client repairs a single-channel receive path — the only lever is
how many datagrams reach it. `FirmwareProfile.shared_meter_udp_channel` is true
for every HMG-50 Control build and:

- forces `parallel_requests_safe` off, so concurrent Open API reads stay off
  even on 156 where the reset-prone gate has cleared
- forces `openapi_wifi_retransmit_safe` off, so a Wi-Fi timeout never puts a
  second copy of a read on the meter's channel
- raises a non-fixable repair warning on the config entry

Slower polling intervals reduce the exposure further but cannot remove it. Two
different things both get called "use a separate meter", and only one of them
changes what the battery does:

- **Reading grid power in Home Assistant from the meter directly** (a
  HomeWizard P1 or a Shelly integration, instead of the battery's
  `EM.GetStatus`) lets you drop the Marstek entry to slow intervals for battery
  state only. It reduces Open API traffic. This is the workaround community
  reports find stable.
- **Changing which meter the battery itself regulates on** is the one that can
  move the meter off the contested socket, and only in one direction: a
  HomeWizard P1 or an Eco-Tracker is read over the module's HTTP client, a
  CT003 or a Shelly over the UDP client. Switching *to* a Shelly puts the meter
  on the same socket path as the Local API server, so it makes contention worse,
  not better. The AT-link caveat above still applies either way, so treat an
  HTTP meter as worth testing rather than as a fix.

## What is not established

- No vendor note, on any HMG-50 version, mentions CT/meter connection,
  self-consumption, or anti-backflow. The 153/155/156 OTA remarks cover Wi-Fi
  provisioning, cumulative charge/discharge time, a grid-connection standard,
  BLE advertising, third-party servers, and Open API stability.
- The mechanism above is read from strings and their layout, not from a full
  decompile of the receive path. It explains the reported behaviour and matches
  the vendor's Venus E2.0 warning; it is not a vendor statement.
- Issue #82 is one reporter. No second independent Venus C report existed when
  this was written, and no capture of `ct_state` during a stall.
- Which identifier the CT003 itself reports is not proven. The firmware knows
  `HME-4`, `HME-3`, `SMR-0`, `SMR-1`, `SMR-2` and `TPM2-0` alongside the two
  Shelly model names, and the `ct002_get_info` payload sits in the same region,
  but nothing in the strings ties a given identifier to a given product name.
- The meter client's connect id is not proven either. Only the Local API
  server's id is hard-coded in a string (`+QIOPEN: 3,0`); the meter socket's id
  is computed at runtime, so "both sit on one channel" rests on HMG-50 having
  exactly one `"recv",<id>` handler, not on reading the meter's id directly.
- The HTTP meters use a separate AT service, not a separate link. Everything
  still serialises on the module's single main UART, so an HTTP meter reduces
  contention on the receive channel without removing it from the path.
