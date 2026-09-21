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
commands. Inbound UDP arrives either as a `+QIURC: "recv",<connect_id>`
unsolicited result code or by polling a buffered socket with `AT+QIRD`. Counting
which of those each image contains separates HMG-50 from every other line:

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

Reproduce with:

```bash
python3 tools/firmware/fetch_firmware.py
for f in tools/firmware/blobs/*.bin; do
  echo "== $f"; strings -a "$f" | grep -aE '\+QIURC: "recv"|AT\+QIRD|accept/mode'
done
```

Three things follow.

**HMG-50 has exactly one hard-coded inbound channel, connect id 3.** There is no
generic `+QIURC: "recv",%d,` parser on those images at all — only
`+QIURC: "recv",3` (156: `0x316e1`). Every other line carries the generic parser
*and* a hard-coded id.

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

**The CT / P1 meter reader is a second UDP client with no channel of its own.**
It opens a socket in direct-push mode and polls the meter itself (156:
`0x080217b4`):

```
217b4 AT+QICFG="accept/mode",0
217d3  AT+QIOPEN=%d,"UDP SERVICE","%s",%d,%d,1
2180f  {"id":%d,"method":"EM.GetStatus","params":{"id":0}}
21844 {"id":%d,"method":"EM1.GetStatus","params":{"id":0}}
2187c AT+QISEND=%d,%d,"%s","%s",%d
...
218d0 HME-   218d8 SMR-   218e0 TPM2-
218e8 shellyemg3   218f4 shellyproem50
```

Its task functions sit next to the one URC filter (156: `0x31649`):
`OKTCP_Analy`, `Udp_Tcp_Init_Read`, `CT_Loop_Read`, `Get_dev_ip`,
`Check_ct_status`, `Get_meter_IP`, `Parse_shelly_udp_data`, `Check_ct_ip`,
`Parse_new_ct_data`, `Shelly_Task_loop`, then `+QIURC: "recv",3`. The failure
message for losing it is already in the image:
`!!! [HTTP] Warning : P1 METER DISCONNECT!!!`.

So HMG-50 runs two UDP consumers — the Local API server and the meter reader —
over a single hard-coded channel, drained with buffered `AT+QIRD` reads and an
`accept/mode` reconfiguration. Home Assistant's Open API datagrams land in the
same place the meter replies do. Lose enough meter samples and
`Check_ct_status` declares the meter gone; Auto mode then has no grid reading
to regulate on and stops charging. That is the reported symptom, and it is the
same mechanism as the CT003 disconnection Marstek warns about on Venus E2.0.

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

Slower polling intervals reduce the exposure further but cannot remove it. A
separate meter integration for grid power (a HomeWizard P1 or a Shelly read
directly, rather than through the battery) is the workaround that community
reports find stable.

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
