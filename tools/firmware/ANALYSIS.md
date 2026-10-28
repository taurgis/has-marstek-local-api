# VNSE3-0 Control firmware analysis (issue #15)

This note records what the Venus E 3.0 Control images actually contain.
Blobs are **not** stored in git (Marstek/Hamedata copyright). Hashes and OTA
URLs live in `catalog.json`; `fetch_firmware.py` downloads them locally.

## Community confirmation (firmware 150)

On 19 Sep 2026, [issue #15](https://github.com/taurgis/has-marstek-local-api/issues/15)
received:

> this bug is fixed with update v150 from Marstek venus 3.0 side. So just
> update via App to 150 and you are ready to go. I my case update via LAN was
> impossible so wifi or Bluetooth.

That matches the official OTA remark for VNSE3-0 Control **150**
(`VNSEE3-0_app_0150_0804_151249.bin`, built `Aug 4 2026 15:11:03`):

1. Optimized Local API send anomaly on Ethernet (`Loacl API` in the vendor text)
2. Optimized HTTP upgrade failure on Ethernet
3. Peak-shaving
4. HTTP payload too long causing data loss
5. Meter connection via `CT_TYPE`

Earlier 147.x OTA pushes did **not** fix it. Archive **1476** is app **147.6**
(12 Mar 2026, “Optimized MQTT connection stability”) and ships **before** 148
and 150. Treating `ver=1476` as newer than 150 is wrong.

## What the MCU is

All four Control images are bare STM32 Cortex-M firmware (vector table at
offset 0, Thumb reset in `0x0800xxxx`, SRAM stack in `0x2001xxxx`):

| ver | size | initial SP | reset | banner |
|-----|------|------------|-------|--------|
| 144 | 354304 | `0x2001f0d8` | `0x08004a6d` | `VNSE3-0 v144` |
| 1476 | 364544 | `0x2001f318` | `0x08004a71` | `VNSE3-0 v1476` |
| 148 | 366592 | `0x2001f328` | `0x08004a71` | `VNSE3-0 v148` |
| 150 | 374784 | `0x2001f5e0` | `0x08004a71` | `VNSE3-0 v150` |

FreeRTOS `heap_4` plus a malloc wrapper that logs `malloc buf fail` and
enforces a 64-byte minimum. JSON lives in `..\APP\json\json_data.c` (cJSON-like,
heap allocated). JSON-RPC `id` is stored with `ldrh` (uint16). Parse failures
reply with `id: 0` / `-32700`.

Transport is dual-stack:

- WiFi: Quectel FC41D `AT+QIOPEN=…,"UDP SERVICE"` / `AT+QISEND`
- Ethernet: WCH CH395 (`CH395SendData`, `CH395GetRecvData`, `CH395 reset!!!`)

Local API enable/disable strings exist on BLE and MQTT paths
(`[BLE] Set local api enable.`, `[MQTT] Local API enable.`). Factory-reset
copy includes `Reset, clear all/part/cert…`. That matches reports that Open API
**and** user settings wipe together.

## What 150 changed versus 148

String and size diffs, not a full decompile of every Thumb function:

- **+8.2 KiB** image, initial SP moved `0x2001f328` → `0x2001f5e0` (more
  `.data`/`.bss`, typical of extra HTTPS/HTTP buffers).
- New Open API methods **`Set.Ver`** and **`Reset.Factory`** (Rev 3.1). This
  integration still does not expose them.
- New Peak-shaving BLE/MQTT setters.
- New CH395 HTTPS client (`Init_ch395_https`, `Pack_https_post_req`,
  `Parse_https_data`) replacing some HTTP upload strings that existed in 148
  (`[CH395] HTTP upload data: %s.`).
- New explicit chip reset logs: `[HTTP]ch395 reset!!!!`, `[HTTP]fc41d reset!!!!`.
  148 already had `CH395 reset!!!`; 150 adds HTTP-path resets instead of
  letting a stuck Local API send share the same sockets.
- `FreeRtos_mem` debug string.
- JSON / malloc / `Send Json:{%s} len:(%d)` / `AT+QISEND` / `CH395SendData`
  strings are **still present**. The parser and uint16 id width were not
  replaced.

The OTA wording “Local API **send** anomaly on Ethernet” plus duplicate
WiFi+Ethernet send helpers in 148 is the best explanation for the factory
reset: a CH395 send of a UDP reply (or a second copy of it) corrupts the
Ethernet/HTTP path, the MCU resets the chip or the settings partition, and
Open API comes back disabled.

150 does **not** prove the JSON heap is now burst-safe. Parallel Open API
calls stay disabled on generation &lt; 150 and remain optional on 150+.

## Wi-Fi Open API timeouts (FC41D, firmware 150 included)

Control 150's published fix is **Ethernet only** (`CH395SendData` / "Local API
send anomaly on Ethernet"). The Wi-Fi path is unchanged across 148/149/150:

- Incoming Local API on Wi-Fi: `Extract_udp_data` plus Quectel
  `AT+QIOPEN=…,"UDP SERVICE"` / `+QIURC: "recv"`
- Outgoing replies on Wi-Fi: `AT+QISEND`
- Incoming Local API on Ethernet: `Extract_udp_data_ch395` with
  `[CH395] UDP data remote port: %d, api local port %d`

The FC41D UART is shared with MQTT and HTTP. Ethernet CH395 is not subject
to 802.11 station idle behavior. That matches "timeouts on Wi-Fi, fine on
LAN" on 150. Limited live probes recovered more Wi-Fi reads with a silent
wait plus one copy; the public Quectel manual does not establish a
deterministic first-datagram drop after idle.

Plugin-side mitigations for **known-safe** read-only unicasts
(`FirmwareProfile.openapi_wifi_retransmit_safe`: known family, known
Control generation, not reset-prone):

1. Send copies at 0.5 s, 2.5 s and 5 s after the first datagram, each only
   if nothing has answered yet (RFC 1122: UDP retransmission is the
   application's job). Losses are bursty: the 150 reply sender gives up
   only after the modem stays busy for about 1.8 s, and the firmware then
   ignores requests for a second or two, so the later copies keep at least
   ``POST_REPLY_QUIET_INTERVAL`` between them. Ethernet replies that succeed
   do so in well under 200 ms, so an awake LAN radio does not get a second
   datagram. See `WIFI_UDP_RELIABILITY.md` for the trace and measurements.
2. Offsets at or past the configured request timeout are skipped, and the
   rest of that timeout is used for a matching reply. Cap: four datagrams
   inside one timeout. ``asyncio.wait`` does **not** cancel the pending
   future, so a late reply to any copy still counts.
3. Writes, unknown models, missing ``ver``, and reset-prone IPs stay at
   one datagram and one wait.

These do not make a Wi-Fi-only Open API as reliable as a cable. Prefer
Ethernet for polling. If the AP isolates STA clients or NATs Wi-Fi, unicast
replies never arrive and retries cannot help.

## Network chip resets

Control 150 (`VNSEE3-0_app_0150_0804_151249.bin`, base `0x08000000`)
resets its own network chip while cloud uploads keep failing. A user's LAN
log on Ethernet showed a DHCP Discover and Request, then 5 to 9 s without
ping, about every 35 minutes; a linked plug showed the start-up check right
after each.

- **Ethernet.** The HTTP task at `0x08014ecc` reads the unsent-record count
  (halfword `0x20001004`; records of 0x227 bytes at `0x60000040`). With more
  than one queued and the 1800 s timer at `0x20001fce` expired
  (`0x08014f9e`-`0x08014fb8`), and the halfword at `0x2000014c` at 0 (it stayed
  0 in the emulator; its meaning is not known), it calls `0x08027b04`, which logs
  `[HTTP]ch395 reset!!!!`, pulses the CH395 reset pin (PD9, `0x08003be4`),
  re-sends its init commands and logs `CH395 reset!!!`. The CH395 restarts
  DHCP from Discover.
- **Wi-Fi.** The same path resets the FC41D every 900 s through `0x0800b224`
  (PE4/PE5 low for 1 s; logs `[HTTP]fc41d reset!!!!`).
- **Timer helper `0x0802bce0(stamp, seconds)`.** A zero stamp is set to now
  and returns 0; once `now - stamp >= seconds` it clears the stamp and returns
  1. The period is the limit plus the time to the next call, so 1800 s shows
  up as about 35 minutes.
- **Neither path resets the MCU.** The software reboot (`0x08027e18`, "system
  will reboot!") is called from OTA, develop-mode, MQTT `Reset` and two
  battery-state checks (`0x0802b394` after 3600 s, `0x0802b444`), not from
  the HTTP task.
- **Control loop.** The self-use loop (`0x08029c88`) checks the network flag
  (`0x2001a634`, `g_dev_net_info`) first; with it clear it zeroes the target
  power and commands 0 W (`0x08005c8c(1, 0)`). While the chip is down the
  CT meter's UDP packets do not arrive, and the loop then restarts its ramp
  from 0 W (`0x08029ecc`).
- **In the emulator.** `run_firmware.py` now feeds PE4/PE5 to `fc41d.py`, so
  the vendor image resets its modem by itself when no cloud answers. Venus E
  150 on Wi-Fi, booted at 07:42:47, pulsed the line at +31:05, +34:14,
  +39:33 and +41:16. Each time the Open API was gone about 10 s (pulse to
  the Local API socket reopening) and the CT socket came back about 75 s
  after the pulse. `EM.GetStatus` kept `ct_state: 1` with the last
  `total_power`, and in Auto the battery held its last output (-200 W)
  until CT data returned, then regulated again. The 0 W branch above did
  not show at this outage length: the network flag stays set while the
  module rejoins. The unsent-record count at `0x20001004` grew by one about
  every 5 minutes.
- **With a cloud.** `fc41d.py` now answers MQTT and HTTP through a stand-in
  cloud (`cloud.py`; `--no-cloud` restores the run above). The same image
  set its RTC from `getDateInfo`, connected MQTT, and posted an upload about
  every 5 minutes, each answered `{"code":0}`. `upload_cnt` stayed at 1 and
  the module was never reset in 46 minutes.
- **Ethernet in the emulator.** With `run_firmware.py --ethernet` the image
  moves to the CH395, where `ch395.py` answers no DNS and no TCP, so the
  uploads fail as above. `upload_cnt` reached 13, the 1800 s stamp at
  `0x20001fce` was 58, and the reset came when the clock reached 1858:
  `CH395 reset!!!`, `[HTTP]ch395 reset!!!!`, `INIT_CH395`, DHCP and the
  Local API socket open again within 0.4 s. That took 62 wall-clock minutes
  at the emulator's half speed. The stamp was then re-armed at 1858 for the
  next period.

Open: why the plug sees the start-up check after a chip reset. Nothing in the
paths above touches the inverter (Micro 116/119, BMS 115 run their own
`self_check` behind CAN/RS-485). The hardware watchdog at `0x08016f48`
(IWDG) could turn a stalled reset into a full reboot, but the code does not
show that. The integration lists a self-test with no other trace and at most
one missed poll as a **brief network reset** instead of a restart
(`helpers/restart_detector.py`).

Control 151's changelog lists "Fixed the pre-release server URL error,
causing data upload failure" and "Optimize the stability of Https upload
data", which address the trigger.

## Plugin-side mitigations (cannot patch the MCU)

1. JSON-RPC ids cycle `1..65535` and never emit `0` from the command
   builder. Discovery still sends top-level `"id": 0` on purpose
   (`discovery._build_discovery_message`); the official PDF requires
   `params.ble_mac="0"`, not a JSON-RPC id of 0. Do not change discovery
   without a firmware matrix test.
2. Outbound ids above 65535 are rejected when validation is on. The UDP
   client also rewrites `validate=False` payloads to the uint16 wire id
   (non-discovery methods never send 0). Inbound ids are matched as uint16.
3. Empty UDP datagrams are never sent and inbound empties are ignored.
4. `Bat.GetStatus` is not sent on reset-prone firmware, even if battery-detail
   entities were previously enabled. On 150+ it stays entity-registry gated
   (issue #14).
5. Parallel polling is ignored when `openapi_reset_prone` is true
   (known family, Control generation &lt; 150, including `ver=1476`; unknown
   model names with a Control-like generation 100–149). Unicast requests to
   those IPs are serialized on a per-IP lock. `pause_polling` waits for the
   current coordinator cycle to finish and rolls back if the wait is
   cancelled. Discovery pauses the UDP listener only after in-flight unicasts
   drain; nested pauses wait until the listener is actually stopped.
   Reset-prone IPs keep a 1s UDP floor even when a caller asks to bypass
   rate limiting. Marks are owned by config-entry id so an IP change cannot
   leave a stale lock on an address a later 150+ device reuses.
6. A non-fixable Home Assistant warning is created from config-entry metadata
   **before** the first UDP probe and points at issue #15. The pooled client
   is created and marked before the scanner starts so the first scan can
   pause an existing listener; the scanner still starts before the probe.
7. Known-safe read-only unicasts send copies at 0.5 s, 2.5 s and 5 s only
   while silent, then finish the remaining request timeout (cap: four
   datagrams).
   ``asyncio.wait`` keeps the pending future alive. Writes, unknown
   firmware, and reset-prone IPs stay at one datagram.

## CH395 Ethernet path (VNSE3-0 150)

Read from the 150 image and confirmed by booting it in Renode against
`tools/firmware_emulator/ch395.py` (`run_firmware.py --ethernet`). Addresses are
file offsets + `0x08000000`; the image loads at `0x08004800`, so pointers in
literal pools are `0x4800` higher.

- **Bus.** SPI2 master, mode 0, 8-bit (`0x08017070`). PB12 is chip select,
  PD9 reset (low 100 ms, then 50 ms, `0x08003be4`), PD8 INT# (`0x08017334`).
  One byte goes through `0x0802b564`. A command raises then lowers CS, sends
  the command byte (`0x08053150`) and its parameters; reads clock out `0xFF`
  (`0x0805255c`). CS high ends it.
- **Bring-up.** `Task_ch395` (`0x0802bb50`) calls `0x08027b04`: reset,
  `INIT_CH395` and poll `GET_CMD_STATUS` while it reads busy (`0x08027bc4`),
  receive/send buffers for sockets 0-2 and 4-7 (`0x08027d28`), `DHCP_ENABLE`
  (`0x08027ab8`), then log `CH395 reset!!!` and open the sockets.
- **Interrupts.** The task polls PD8 (`0x080309e0`). When low, `0x080301c8`
  reads `GET_GLOB_INT_STATUS_ALL`: bit 2 reads the PHY state, bit 3 the DHCP
  result and `GET_IP_INF` (into `0x2001abbd`; `0x2001abb8[1]` = 1 marks the IP
  ready), bits 4-11 go to the socket handler `0x08030534`.
- **Sockets.** 0: Modbus TCP server on 502. 1: TCP client on 8091 for the debug
  log. 2: UDP server on 8090 (vendor discovery). 4: CT003 poll, UDP from 22222
  to broadcast 12345. 5: Local API, UDP server on the Open API port, opened by
  `0x0802c1d8` once the IP is ready (`UDP Server Start, adv ip(%d)`). 6 and 7:
  DNS clients on source port 2000 (cloud host lookups). UDP server mode is
  destination `255.255.255.255`; each received datagram then starts with
  length (2, LE), port (2, LE) and IP (4), which `0x0802c04c` unpacks before
  the shared JSON handler.
- **Uplink choice.** `0x08027c08` sets `0x20001f55` to 2 when the CH395 has an
  IP, and clears the Wi-Fi socket state. From then on the Local API, CT poll,
  DNS and cloud traffic use Ethernet only, and the Wi-Fi address stops
  answering although the module stays associated.
- **`Marstek.GetDevice` `ip`.** Formatted with `%02d.%02d.%02d.%02d`, so an
  Ethernet unit reports e.g. `172.28.00.02`. `discovery.py` normalizes that.

## Mock device

`tools/mock_device` now reproduces:

- uint16 JSON-RPC id truncation
- parse-error reply `id=0`, code `-32700`
- Open API freeze after a 0-byte datagram
- duplicate UDP replies on reset-prone firmware; a single reply on 150+
  (HMG-50 / Venus C uses 156 for that single-reply gate)
- JSON-RPC `-32601 Method not found` for unknown methods
- HMG-50 / Venus C 153: no `EM.GetStatus` server method; 155+ serves it
- HMG-50 153 `ES.GetStatus` includes `bat_power`; 155/156 omit it
- HMG-50 `Wifi.SetConfig`; VNSE3-0 `PV.GetStatus` `-32601` with `data: 424` on generation ≥150
- HMG-50 153 `ES.GetStatus` includes `bat_power`; 155/156 omit it
- HMG-50 `Wifi.SetConfig`; VNSE3-0 `PV.GetStatus` `-32601` with `data: 424` on generation ≥150

## Related vendor notes (other SKUs)

These are **not** Venus E 3.0 Control, but they show Marstek has been chasing
the same class of bug on other products:

- HME-3 v122 / HME-4 v124: `udp协议v4 修复udp复位bug` (UDP protocol v4, fix UDP reset bug)
- HMG-50 Control v156: `优化OpenApi接口稳定性` (optimized Open API interface stability)

## Venus E 2.x / HMG-50 identity

Venus E 2.0 is **HMG-50**, not VNSE3-0. Control **153** is the first archived
image with `Marstek.GetDevice`. Strings in that binary (and 154–156):

| Role | HMG-50 153+ | VNSE3-0 |
|------|-------------|---------|
| GetDevice `device` | `VenusE` | `VenusE 3.0` |
| JSON-RPC `src` | `VenusE-%s` | `VenusE 3.0-%s` |
| SKU | `HMG-50` / `HMG-25` / `HMG-1` | `VNSE3-0` |

Matching only `Venus E2.0` / `VNSE2-0` would accept a real E2 as Venus E 3.x.
The integration treats bare `VenusE`, `HMG-50`, and `VNSE2` as unsupported.
Venus E 3.x requires `VenusE 3.0` / `VNSE3`.

HMG-50 Control **153** Open API methods in the binary recv list:
`Marstek.GetDevice`, `ES.GetMode`, `ES.SetMode`, `Wifi.SetConfig`,
`BLE.GetStatus`, `ES.GetStatus`, `PV.GetStatus`, `Wifi.GetStatus`,
`Bat.GetStatus`. `EM.GetStatus` appears only as a meter *client* request
on 153 (`{"id":%d,"method":"EM.GetStatus"...}`). Control **155** adds the
Open API **server** method `EM.GetStatus` to that recv list. Control **156**
OTA is “Improved OpenAPI interface stability”. None of 153/155/156 contain
`DOD.SET` / `Ble.Adv` / `Led.Ctrl`. GetDevice identity strings include both
`VenusE` / `VenusE-%s` and `VenusC` / `VenusC-%s`.

Issue [#60](https://github.com/taurgis/has-marstek-local-api/issues/60) is
HMG-50 reporting `device: "VenusC"` and omitting GetDevice result MACs. The
firmware profile therefore treats Venus C **153/155/156** as HMG-50 Control:
no SYS/UPS, EM server from **155**, reset-prone until **156**. Bare
`VenusE` at those generations stays an unsupported E 2.x identity.

Docker mocks:

- `172.28.0.26` Venus C 153 (issue #60)
- `172.28.0.42` / `.43` Venus C 155 / 156
- `172.28.0.29` / `.44` / `.45` unsupported `VenusE` 153 / 155 / 156

## What 151 changed versus 150

VNSE3-0 151 (built 28 September 2026) and VNSD-0 151 (29 September 2026), from
the official EU OTA CDN. Both match the community archive's SHA-256 and the
cloud's CRC16. The vendor changelog lists Modbus TCP stability, a fix for the
DOD state that blocked discharge, an upgrade-type check (`Bin type
mismatch!`), AI-mode validation and an AI-strategy query, the pre-release
server URL, HTTPS upload stability, and CT polling every 1.1 s.

Checked by string diff and by running both images in the emulator next to
150, with Home Assistant set up against them:

- **The Open API is unchanged.** The same dotted method names are there,
  including `Set.Ver`, `Reset.Factory` and `Marstek.GetData`. There are no new
  reply fields, and the values and encodings match 150 (Venus D still reports
  PV1 in deciwatts). `DOD.SET`, `Ble.Adv`, `Led.Ctrl` and `ES.SetMode`
  answer as on 150. The AI-strategy query, the MQTT `Get INV DEBUG info`
  command (`debug=`), and the `read_inv*` strings belong to the cloud side;
  no Open API method was added for them.
- **`src` names the SKU.** Replies carry `"VNSE3-0-<ble_mac>"` /
  `"VNSD-0-<ble_mac>"`, where 150 sent `"VenusE 3.0-<ble_mac>"`. The format
  string `` VenusE 3.0-%s`` became `%s-%s` next to the SKU literal.
  `Marstek.GetDevice` `device` still reads `VenusE 3.0` / `VenusD`, so family
  detection is unaffected, and the MAC fallback from `src` (issue #60) still
  finds the BLE MAC. Venus A **1509** already made the same switch
  (`VenusA-%s` → `%s-%s` next to `VNSA-0`, so `src` is `VNSA-0-<ble_mac>`),
  even though `ver` 1509 still folds to generation 150. VNSA-0 **150** still
  has `VenusA-%s`. VNSA-0 **1508** is VEPRO-0 / VenusE Pro, not Venus A: its
  src site is `%s-%s` next to the product name `VenusE Pro`, not `VNSA-0`.
- **`[HTTP]ch395 reset!!!!` is gone.** 150 added that log on the HTTP path
  next to `[HTTP]fc41d reset!!!!`. 151 still has the Wi-Fi
  `[HTTP]fc41d reset!!!!` and the general `CH395 reset!!!` strings; only the
  HTTP CH395 log string is confirmed removed. The reset behaviour itself is
  unverified. Venus A 1509 also dropped it (VNSA-0 150 still has it; VEPRO-0
  1508 already lacks it).
- The vendor changelog's `Bin type mismatch!` OTA guard is in the 151 blobs
  (`Bin type mismatch! Upgrade aborted` and
  `Bin type mismatch! file=%s expect=%d got=%d`) and in Venus A 1509; 150
  has neither.
- Cloud-side only: the getDateInfo HTTP host moved from `%s.hamedata.com` to
  `api-%s.marstekcloud.com`, `AT+QHTTPPOST` timeouts went from 60 to 120 s
  (`AT+QHTTPPOST=%d,120,120`; Venus A 1509 still posts `,60,60`), a
  `cd=19,ct_st=…` CT statistics line and a `df=` status field were added, and
  the BLE name is built from `MST_VNSE3_` / `MST_VNSD_`.
- Initial SP moved to `0x20021df8` (VNSE3-0) and `0x20022010` (VNSD-0).

The firmware profile resolves 151 exactly like 150
(`tests/test_firmware_profile.py::test_control_151_resolves_like_150`),
including Venus A. Venus A 1509 was the first archived VNSA-0 image whose
Open API `src` names the SKU; VNSA-0 151 does the same because its generation
is 151.

## What Venus A 151 changed versus 150 and 1509

VNSA-0 151 (`VNSA-0_ems_app_0151_0928_095826.bin`, 391168 bytes, built
28 September 2026 09:54:57) is the official EU OTA image. SHA-256
`5cceb230f28c9c3a67be5e897afd5722f795b3e8f6c39d58d5a2c2873d994346` matches
the download and the community archive
([sphings79 metadata](https://github.com/sphings79/marstek-firmware-archiv/blob/main/firmwares/VNSA-0/Control/151/metadata.json)).
The vendor changelog is the same text as VNSE3-0 / VNSD-0 151: Modbus TCP
stability, the DOD state that blocked discharge, a Ymodel upgrade-type
check, AI-mode validation and an AI-strategy query, the pre-release server
URL, HTTPS upload stability, and CT polling every 1.1 s. Initial SP is
`0x20022010` and reset is `0x08004a71`, the same layout as VNSD-0 151 and
the Venus A 1509 ems build.

String diff against VNSA-0 150 and 1509 (dotted method names, JSON field
names, and the format strings next to them):

- **The Open API method list is unchanged.** 149, 1491, 150, 1509 and 151
  carry the same dotted names: `Marstek.GetDevice`, `Marstek.GetData`,
  `ES.GetMode`, `ES.SetMode`, `ES.GetStatus`, `EM.GetStatus`, `PV.GetStatus`,
  `Wifi.GetStatus`, `Bat.GetStatus`, `BLE.GetStatus`, `DOD.SET`, `Ble.Adv`,
  `Led.Ctrl`, `Set.Ver`, `Reset.Factory`, `Ota.Set`. No new reply field name
  appears (`bat_power` is still absent; `total_pv_energy`, `pv2_power` through
  `pv4_power`, `ongrid_power`, `offgrid_power`, `ct_state` stay). `Wifi.SetConfig`
  is still absent. GetDevice `device` is still `Venus A`.
- **`src` names the SKU**, as on 1509 and on E3/D 151. 150's `VenusA-%s` is
  gone; `%s-%s` sits next to the `VNSA-0` literal, so replies are
  `VNSA-0-<ble_mac>`. Generation 151 already selects that form in the mock.
- **Versus 150, 151 picked up the 1509 cloud/OTA strings** and then the
  September server-URL change. New relative to 150: `Bin type mismatch!`
  (the Ymodel upgrade-type check; also `Bin type unrecognized!`),
  `cd=19,ct_st=…`, `[MQTT] Type: %d, Get INV DEBUG info...` / `debug=`,
  and the loss of `[HTTP]ch395 reset!!!!`. New relative to **both** 150 and
  1509: the getDateInfo host moved from `%s.hamedata.com` to
  `api-%s.marstekcloud.com`, and `AT+QHTTPPOST` timeouts went from 60 s to
  120 s (`AT+QHTTPPOST=%d,120,120`). 1509 still posts `,60,60` and still
  uses hamedata. A status fragment `` `AI%u=%d|%d|%d|%d|%d. `` is also only
  in 151. `[MQTT] Type: %d, GET_AI_STRATEGY...` is already in 150.
- **Not an Open API change, and not measured here.** `Task_Modbus_tcp`,
  `modbus_addr` and `dod_value` / `pgn_1802_info.dischrg_dod` are already in
  150. The DOD discharge fix and the Modbus TCP stability note are behavior,
  not new methods or fields; the binaries do not show a new `DOD.SET`
  parameter. The 1.1 s CT period is the vendor changelog plus the `ct_st`
  diagnostic line (present since 1509). It is not an Open API field, and a
  bare count of the uint16 1100 does not isolate the timer. The integration
  does not poll the CT meter; it only reads `EM.GetStatus`.

The profile therefore keeps Venus A 151 identical to Venus A 150 apart from
`firmware_version` (SYS/UPS, solar 0.01 kWh, PV1 deciwatts, not reset-prone).

Ran in the emulator (`fw-venus-a-151`, `172.28.0.74`). `Marstek.GetDevice`
returned `device: "Venus A"`, `ver: 151`, and `src: "VNSA-0-02e000000074"`.
`PV.GetStatus` with the configured 420 W / 360 W strings was `pv1_power` 4200
and `pv2_power` 360. Home Assistant set the device up with firmware **151**,
SYS entities (depth of discharge, Bluetooth advertising, panel LED), and
`last_exception` null. A few early reads timed out or came back `Parse error`
data 403 while the CT task held the modem; that is the same drop already
documented for these images, and the following read succeeded. The 1.1 s CT
period was not measured from the running emulator.

## What Venus A 1491 changed versus 149

VNSA-0 1491 (`VNSA-0_app_1491_0528_145327.bin`, 382976 bytes, built
28 May 2026 14:52:08, four hours after 149's 10:23:00 stamp) is the official
EU OTA image. SHA-256
`271c60265478cf92eba21824814632c185d9344244112a842b547fd1e65b6a16` matches
the download and the community archive
([sphings79 metadata](https://github.com/sphings79/marstek-firmware-archiv/blob/main/firmwares/VNSA-0/Control/1491/metadata.json)).
The vendor remark is only “V149替代版本” (“V149 Alternate”). Initial SP is
`0x2001f778` and reset is `0x08004a71`, the same vector table as 149.

The two images are the same length. Almost every byte differs, which fits a
rebuild rather than a patched 149, but the printable strings do not: the
only real string changes are the banner `VNSA-0 v1491` and the build stamp.
Dotted methods, JSON field names, `VenusA-%s`, `Venus A`, the hamedata
getDateInfo URL and `AT+QHTTPPOST=%d,60,60` all match 149. There is no
`%s-%s` SKU `src`, no `Bin type mismatch!`, no `ct_st` line, and no
marstekcloud host. `DOD.SET` is present as a string, as it already is on
149, and Home Assistant still does not unlock SYS below generation 150
(Rev 3.1 PDF, SYS from firmware 150:
[Marstek Device Open API Rev 3.1](https://static-eu.marstekenergy.com/ems/resource/agreement/MarstekDeviceOpenApi.pdf)).

Open API `ver` **1491** is the same dotted encoding as 1476 and 1509
(149.1 → 1491). The profile folds 1000–1999 with `ver // 10`, so 1491 is
generation 149: solar energy in 0.01 kWh, PV1 deciwatts, no SYS/UPS,
reset-prone, `src` still `VenusA-<ble_mac>`. A JSON float `149.1` keeps
meaning generation 149 via the leading integer; the wire value this image
banners is the integer 1491. Nothing in the binary says which internal bug
the alternate build fixed.

Ran in the emulator (`fw-venus-a-1491`, `172.28.0.73`). `Marstek.GetDevice`
returned `device: "Venus A"`, `ver: 1491`, and `src: "VenusA-02e000000073"`.
PV1 was again deciwatts (`pv1_power` 4200 for 420 W). After several minutes at
780 W, Home Assistant showed about 130 Wh of solar energy, which matches
0.01 kWh scaled by 10 rather than a raw Wh counter left unscaled. The device
page has no depth-of-discharge or SYS switches. Diagnostics report generation
149, `openapi_reset_prone: true`, and the reset-prone repair for that entry
only. One `ES.GetStatus` / `Bat.GetStatus` pair came back all zeros while
`ES.GetMode` still had a real SoC; the integration dropped those placeholders
and the next successful poll showed SoC again. `last_exception` was null.
About one in three fast-tier requests timed out, consistent with the CT-mutex
drops, not a new method.

## Archived Control matrix (community OTA)

`catalog.json` now lists every Control image hashed from
[rweijnen/marstek-firmware-archive](https://github.com/rweijnen/marstek-firmware-archive)
and [sphings79/marstek-firmware-archiv](https://github.com/sphings79/marstek-firmware-archiv).
Blobs stay local-only.

| SKU | Open API `ver` | Generation | Notes |
|-----|----------------|------------|-------|
| VNSE3-0 | 144, 147, 1476, 148, 149, 150, 151 | 144–151 | 1476 is app 147.6. SYS/UPS at 150. Live 150 `PV.GetStatus` is `-32601`. |
| VNSA-0 | 148, 1487, 149, 1491, 150, 1508, 1509, 151 | 148–151 | 1487→148, 1491→149, 1508/1509→150. 1491 is the V149 Alternate rebuild. 1508 banners `VEPRO-0` / `VenusE Pro` (unknown family). |
| VNSD-0 | 147, 149, 1492, 150, 151 | 147–151 | 1492→149. Venus D 149 and 1492 report solar energy in 0.01 kWh (`×10`), like Venus A 149. |
| HMG-50 | 153, 155, 156 | 153–156 | No SYS. EM server from 155. Open API stable at 156. |

String presence of `DOD.SET` / `Ble.Adv` / `Led.Ctrl` on VNSE3-0 **147–149**
and VNSA-0 **148** does **not** unlock Home Assistant SYS entities. The
Rev 3.1 PDF plus issue #15 keep the generation ≥ 150 gate.

`tools/mock_device` now reproduces, per firmware profile:

- uint16 JSON-RPC id truncation
- parse-error reply `id=0`, code `-32700`
- Open API freeze after a 0-byte datagram
- duplicate UDP replies on reset-prone firmware (VNSE3-0 &lt; 150, HMG-50 &lt; 156)
- JSON-RPC `-32601` for unknown methods (Control `unknow method` path)
- HMG-50 153: no `EM.GetStatus` server method; 155+ serves it
- HMG-50 153 `ES.GetStatus` includes `bat_power`; 155/156 omit it
- HMG-50 `Wifi.SetConfig` (ssid required); VNSE3-0 / VNSA-0 / VNSD-0 `-32601`
- `Set.Ver` / `Reset.Factory` acknowledged from VNSA-0 1487 and generation 149+
  (not HMG-50). Home Assistant still does not expose those writes.
- Venus C GetDevice omits result MACs (issue #60)
- Venus E 3.0 generation ≥150 `PV.GetStatus` `-32601` with `data: 424`;
  `ver=1476` (app 147.6) does not copy that 150-only payload

Venus A/D reports on issue #15 used the same Local API stack symptoms. This
integration treats every **known family** below Control generation 150 as
reset-prone, except HMG-50 Venus C which uses the 156 Open API stability
gate. Venus E 3.0 **150** is the VNSE3-0 build with a published Local API
Ethernet fix plus a user confirmation.

## Venus E mini PMU 301 (VNSEMINI-0)

Posted by ViktorGozhiy on
[issue #86](https://github.com/taurgis/has-marstek-local-api/issues/86#issuecomment-6273580192)
from Marstek's EU OTA CDN (`getCommonOtaUpdate` / `checkSmallBalconyOTA` for
device type `VNSEMINI-0`). Hashes match the comment. The PMU is the Open API
image. INV 270 and DCDC 268 are catalogued with the same 32-byte header so
their hashes stay checkable; they have no Open API strings and are not booted
(`runnable: false`). Their load address `0x08000000` is inferred from a reset
vector in the `0x0800xxxx` range, not from a successful boot.

### Image layout (observed)

Each file starts with exactly 32 ASCII bytes and no trailing NUL. The number
in the header is the payload size.

| Part | Header | Payload | SP | Reset | Load |
|------|--------|---------|----|-------|------|
| PMU 301 | `VNSEMINI-0_PMU_V12D_182500_35_FF` | 182500 | `0x200205f8` | `0x080081ad` | `0x08008000` (vector sits there) |
| INV 270 | `VNSEMINI-0_INV_V10E_100904_AA_FF` | 100904 | `0x20003160` | `0x080082d5` | inferred `0x08000000` |
| DCDC 268 | `VNSEMINI-0_DCDC_V10C_50152_2F_FF` | 50152 | `0x200087a0` | `0x080061ed` | inferred `0x08000000` |

PMU build stamp strings `Sep 11 2026` and `11:31:28` are at payload offsets
`0x22ad4` and `0x22afc`. Banner strings include `VNSEM-0`, `VNSEMINI-0`,
`MARS_VNSEM`, `VE-MINI`, and `HMC-1` inside an OTA URL.

### MCU (observed peripheral bases, inferred exact part)

No vendor string names the chip. Literal pools match the GD32F30x / STM32F1
map already modelled by `control.repl.in`, so that board is reused rather than
a new one:

- RCC `0x40021000`, PWR `0x40007000`, FLASH `0x40022000`, DBGMCU `0xE0042000`
- GPIOA–C at `0x40010800` stepping `0x400` (not the F4 GPIOA at `0x40020000`;
  that address is DMA1 here and is referenced)
- TIM1 `0x40012C00` through TIM7, USART1 `0x40013800`, USART2 `0x40004400`,
  USART3 `0x40004800`, UART4 `0x40004C00`, UART5 `0x40005000`
- Extra UARTs `0x40015000` (twice) and `0x40015400` (six times)

The vector table is Cortex-M3/M4: MemManage, BusFault and UsageFault are
present, and indices 7–10 and 13 are zero. It runs through index 101 (IRQ 85)
with default handler `0x080081c7`. Indices 102–106 are code immediately before
`Reset_Handler` at payload offset `0x1AC`. There is no F1 UID (`0x1FFFF7E8`)
or flash-size (`0x1FFFF7E0`) read. Initial SP `0x200205f8` sits past the 64 KiB
STM32F103 SRAM, which fits the oversized SRAM the Control board already maps.
RTOS strings are uC/OS-III, not FreeRTOS. No CH395 strings. No I2C1/I2C2 or
CAN1/CAN2 base hits, so the Control EEPROM “force Local API on” path and the
CAN BMS peers are not what this image talks to. FC41D AT commands are present
(`AT+QIOPEN` UDP SERVICE access modes 0 and 1, `AT+QISEND`, `+QIURC:`,
`AT+QIRD`, CT port `12345`, and `60606`).

IRQ 74, which the other Control images use for the FC41D UART at `0x40015000`,
is the default handler in this image. USART2/3 and UART4/5 (IRQs 38, 39, 52,
53) have real handlers, and so does IRQ 75 (`0x08010cdd`), whose neighbouring
register writes use `0x40015400`. The first boot that reached the AT sender
wrote `AT+QVERSION`, `AT+QBLEADDR?`, `AT+QBLENAME=MARS_VNSEM_` and the GATT
setup to USART2 (`0x40004400`), not to `0x40015000` or `0x40015400`. The
emulator therefore connects `fc41d.py` to USART2 for `VNSEMINI-0` only. That
is from the boot log, not from guessing which extra UART the ISR belongs to.

### Open API method table (observed)

Eleven entries at `0x0802f2ac`, each a string pointer plus a Thumb handler,
stride 8:

`Marstek.GetDevice`, `Wifi.GetStatus`, `BLE.GetStatus`, `Bat.GetStatus`,
`ES.GetStatus`, `ES.SetMode` (`0x08026bfc`), `ES.GetMode`, `EM.GetStatus`,
`DOD.SET`, `Ble.Adv`, `Led.Ctrl`.

The next four words are mode-name pointers, not code: `Auto`, `UPS`, `Manual`,
`AI` (`0x0802f304`–`0x0802f310`). There is no `PV.GetStatus` string anywhere
and no `Passive` string anywhere. `staging` sits in rodata between `ES.SetMode`
and `Led.Ctrl` and is not in the dispatch table.

### Dispatcher and errors (observed)

`error_reply` at `0x0801b17c(id, code, data)` formats
`{"id":%d,"src":"%s-%s","error":{"code":%d,"message":"%s","data":%d}}`.
Relative to base `-32602` the switch selects `Parse error` (`-32700`),
`Invalid Request` (`-32600`), `Method not found` (`-32601`), `Invalid params`
(`-32602`) and `Internal error` (`-32603`). The success `src` literal is
`VNSEM-0`, so every reply is `VNSEM-0-%s` (the second `%s` is the BLE MAC).
That matches the issue #86 `GetDevice` `src` of `VNSEM-0-<mac>`.

The dispatcher at `0x0801b03c` first `strstr`s the raw request for the decimal
form of a halfword at global `0x200115a0+4`. If that decimal is absent it
returns without a reply. The halfword is RAM, not a flash constant, so the
value it filters on is not known from the image. It then extracts JSON with a
substring search, not a tree parser, which is why a key inside `manual_cfg`
is still found. Missing numeric keys become `atoi("")` = 0.

Only three call sites pass arguments to `error_reply`, and each passes id 0:

1. JSON parse failure (`0x0801b08a`): data `401` (`0x191`), code `-32700`.
   Reachable.
2. After the 11-name loop, if every name missed (`0x0801b0e8`): data `408`
   (`0x198`), code `-32700+0x64` = `-32600` (`Invalid Request`). **Unreachable.**
   The bytes at `0x0801b0da` are `add r0, sp, #0x140` then `cbnz r0`, and
   `sp+0x140` is never null, so the error call is dead. A complete miss frees
   the scratch buffer and returns. `Foo.Get` and `PV.GetStatus` therefore get
   silence, which is what the issue #86 capture recorded (nothing back, not
   even an id-0 error). The `Method not found` string has no caller that
   passes `-32601`. The mock must not answer those methods, and must not
   invent a data-408 reply.
3. Unknown `ES.SetMode` mode (`0x08026d36`): see below.

### ES.SetMode (observed in the image, compared with #104)

`ES.SetMode` compares the mode with a substring match (`0x080082fe`, non-zero
means match) in order `Auto`, `AI`, `Manual`, `UPS` (literals at `0x08026d78`,
`0x08026d88`, `0x08026d8c`, `0x08026d9c`). The fall-through is
`movw r2, #0x191` (data 401), `ldr r1` = `-32700`, `movs r0, #0` (id),
`bl error_reply`. Passive is not a string in the image, so it takes this
path: Parse error `-32700`, data `401`, JSON-RPC id `0`. That is the reply
issue #104 recorded. Auto, AI, Manual and UPS are recognized. Accepting AI
and UPS is binary-proven; the integration already exposes AI for the mini
and UPS when `ver >= 150` (301 qualifies). Passive stays selectable in Home
Assistant so the parse error can be shown.

The Manual arm stores mode `2`, `manual_set`, `power`, `enable`, `week_set`,
and `%d:%d` times, indexed by `time_num`, with no compare against 5. The
slot range 0–5 is the official app and issue #104, not a bounds check in this
function. The integration keeps 0–5. A missing `manual_set` is stored as 0
next to `power`. Issue #104 observed the app showing slot power as `---`
without `manual_set`. The mock follows that display and leaves power unset
unless `manual_set` is 0–2 with a non-negative `power`. The 0–2 meaning is
Rev 3.1 documentation, not a capture of a stored number. `manual_set` 3 and
a negative power stay unknown.

### Result templates (observed strings)

- `Marstek.GetDevice`: `device`, `ver`, `ble_mac`, `wifi_mac`, `wifi_name`, `ip`.
- `ES.GetStatus`: `bat_soc`, `bat_cap`, `pv_power`, `ongrid_power`,
  `offgrid_power`, `total_pv_energy`, `total_grid_output_energy`,
  `total_grid_input_energy`, `total_load_energy`. No `bat_power`.
- `ES.GetMode`: `mode`, `ongrid_power`, `offgrid_power`, `bat_soc`, `ct_state`,
  `a_power`, `b_power`, `c_power`, `total_power`, `input_energy`, `output_energy`.
- `EM.GetStatus`: `ct_state`, phase powers, `total_power`, `input_energy`,
  `output_energy`. The image also contains a meter-client request
  `{"id":0,"method":"EM.GetStatus"...}` and `EM1.GetStatus`.
- `Bat.GetStatus`: `soc`, `charg_flag`, `dischrg_flag`, `bat_temp`,
  `bat_capacity`, `rated_capacity`.
- `BLE.GetStatus`: `state`, `ble_mac`. `Wifi.GetStatus`: `wifi_mac`, `ssid`,
  `rssi`, `sta_ip`, `sta_gate`, `sta_mask`, `sta_dns`.
- `set_result` templates are present for the writes (`DOD.SET`, `Ble.Adv`,
  `Led.Ctrl`, and a successful `ES.SetMode`).

`bat_cap` meaning remaining energy rather than nameplate is the issue #86
capture (`1916` at 95% SoC equals `bat_capacity`, `rated_capacity` `2009`),
not something the format string proves. `firmware_profile.py` already treats
`es_bat_cap_is_remaining` as true for the whole Venus E mini family. No
integration change was required for this image: no PV, manual slots 0–5,
`manual_set` required, SYS when `ver` is known, UPS at generation ≥ 150, and
EM status for a known family were already in the profile.

### What the mock does with this

Observed, and applied only at firmware version 301 (other mini versions have
no image, so they keep the generic replies):

- Unknown methods are silent. Previously the whole mini family was silent;
  that was an inference this image does not support.
- `src` is `VNSEM-0-<ble>` even when the configured device name is
  `Venus E mini`.
- Passive (and any mode other than Auto, AI, Manual, UPS) is the id-0 parse
  error with data 401.

Inferred, and labelled as such in `firmware_quirks.py`: the three UDP rules
captured on 301 (answer only the API source port, reply to the subnet
broadcast, ignore broadcast `GetDevice`) are still applied to every Venus E
mini version, because no other mini capture or image exists. The Manual
`manual_set` 0–2 unsigned-power display is documentation plus the #104
`---` observation, not a stored number from a capture.

### Boot (Renode 1.17, `fw-venus-e-mini-301`)

The PMU is loaded by stripping the 32-byte header and `LoadBinary` at
`0x08008000`, then setting SP/PC from the vector table. It starts, talks to
the modem, opens its UDP service and answers the Open API itself. Nothing
outside the PMU binary answers a datagram.

What had to change before the image would run at all, each one observed:

- Renode's `STM32G0DMA.ReadDoubleWord` treats channel index `==
  numberOfChannels` as in range and then indexes off the array. The first
  boot died there during USART init, on a DMA1 read one past the seven
  STM32F103 channels (the log stops on `dma1` offset `0x4` and the exception
  has no address). `numberOfChannels: 8` on both DMA controllers makes that
  read land on a modelled register. The IRQ wiring is unchanged.
- Bring-up then spun on SPI1 `SR` at `0x40013008` bit 1 (TXE), PC
  `0x08023886`. SPI2 already has an always-ready stub; SPI1 uses the same
  stub. The other Control images do not touch this address.
- AT-command timeouts read `DWT_CYCCNT` (`0xE0001004`). Unmapped, the counter
  stays 0 and the wait never ends. The mini board inserts a peripheral that
  steps the counter on each read. The other Control boards do not.
- With those in place the AT stream (`AT+QVERSION`, `AT+QBLEADDR?`,
  `AT+QBLENAME=MARS_VNSEM_0075`, the GATT setup, `AT+QBLEADVSTART`,
  `AT+QWLMAC`) came out of USART2. IRQ 74 and `0x40015400` stayed quiet.
- `AT+QWLMAC` with only `OK` does not advance. A `+QWLMAC:<mac>` line in the
  same colon style as `+QBLEADDR` is stored: the bytes at `0x2000e70c` were
  the ASCII hex of that MAC with the colons removed (`02ee00000001` on the
  run that still used the modem's default Wi-Fi MAC), and `0x2000e719` was
  set to 1. `fc41d.py` now answers `AT+QWLMAC` from `--wifi-mac`.

What it took past that point, in boot order. Each step is something the
binary waits for, found by disassembly and confirmed on the running image:

1. **Settings flash.** The PMU keeps its settings in SPI1 NOR (chip select
   PA4), not in an I2C EEPROM. An 18-byte record at `0x14000` holds the Open
   API enable flag (byte `0x0A`), the port twice (`0x0B`, `0x0D`) and a
   CRC-16/XMODEM at `0x10`. `renode/spiflash.py` models the part (opcodes
   03/02/20/D8/06/05) and, like the EEPROM model of the other boards, keeps
   the record set to "Open API on" with the configured port. A GPIOA write
   hook hands it the PA4 level.
2. **Pack readings.** `AT+QGETIP` is queued only by the sequencer at
   `0x0801dfd0`, and only while the struct at `0x20014618+0x18` is set. The
   control task sets it when the inverter's command `0x02` reply puts a pack
   voltage above `0xFA0` at `0x2000b780+0x56` and a second reading above
   `0x320` at `+0x58`. `mini_peers.py` answers USART4 (inverter, address
   `0x81`) and USART5 (DCDC, `0x82`) with the image's own AA frames and a
   simulated pack behind them (see **Battery** below).
3. **UART timing.** Renode clocks `STM32_UART` at 8 MHz. The image sets BRR
   for a 36 MHz PCLK1, so at 8 MHz an inverter reply outlasted the next poll
   and the frames merged; the USART4 parser (`0x080259c0`) wants one frame
   per IDLE and dropped them. The link UARTs now get `frequency: 36000000`.
   TIMER6 shared DMA2 request line 3 with USART4 RX and, since Renode raises
   a timer's DMA line on every update, read an empty DR into every frame;
   the mini board leaves that line off.
4. **Modbus task.** The control task sends on UART7 by DMA, and the DMA
   TX-complete IRQ clears TC and waits for a fresh TC before posting the task
   semaphore. Renode raises TC once, when DR is written, so the task hung in
   its pend. A bus hook keeps TC set through SR writes on UART7.
5. **Idle window.** The sequencer also wants `0x2000b780+0x4b6 == 0`. That
   flag stays 0 for the first 750 control ticks (`0x20014618+0x90`). After
   that it becomes 1 in Manual mode (`0x20002cb4 == 2`) while the byte at
   `0x2001472c+2` is 0, which a blank settings flash gives. A module that only joins on a
   command arrives too late, so `fc41d.py --autojoin` reports
   `WLAN_CONNECTED` and `GOT_IP` right after `AT+QWLMAC`, the way a
   provisioned module reconnects at power-up. The image's factory-join path
   (a Modbus write that makes it send `AT+QSTAAPINFODEF` with its built-in
   factory AP credentials) is not used.

With those, the image sends `AT+QIOPEN=3,"UDP SERVICE","",30000,30000,1`,
then `AT+QGETIP=station` and `AT+QICFG="udp/broadcast",1`, and answers the
Open API itself. Every reply below is the firmware's.

| Request | Reply from PMU 301 |
|---------|-------------------|
| `Marstek.GetDevice` | `device` `VNSEM-0`, `ver` 301, BLE/Wi-Fi MAC, SSID, `ip` |
| `ES.GetStatus` | no `bat_power`; `bat_cap` field present |
| `ES.GetMode` | `mode` (Manual on a blank settings flash) plus the CT fields |
| `Bat.GetStatus`, `EM.GetStatus`, `Wifi.GetStatus`, `BLE.GetStatus` | answered |
| `ES.SetMode` Auto, AI, Manual, UPS | `set_result: true`; `ES.GetMode` follows |
| `ES.SetMode` Passive | `id` 0, `-32700 Parse error`, `data` 401 (issue #104) |
| `ES.SetMode` Manual, any `time_num` (6, 10, -1, missing) | `set_result: true` |
| `DOD.SET`, `Ble.Adv`, `Led.Ctrl` | `set_result: true` |
| `PV.GetStatus`, unknown methods | no reply |
| any request from a source port other than 30000 | no reply |

**Reply address.** Before `+QGETIP` the image sends replies to
`255.255.255.255:<port>`. The `+QGETIP` handler then keeps the first three
octets of the station address and sends every reply to `<a.b.c>.255:<port>`,
whatever netmask the module reports. On a LAN that is not a /24 that address
is not the LAN broadcast, so replies never reach Home Assistant. The Docker
network is a /16, so the mini board runs `fc41d.py --netmask 255.255.255.0
--lan-broadcast`: the module reports a /24 and puts `<a.b.c>.255` on the
wire as `255.255.255.255`. That is the emulator's link layer, not a change to
what the firmware sends.

**Discovery.** The `+QIURC: "recv"` line carries only the sender's address,
so the PMU cannot tell a broadcast request from a unicast one. In the
emulator it answers a broadcast `Marstek.GetDevice` (Home Assistant's
discovery list showed it). The issue #86 capture saw no answer on a real
device; if that holds, the filter is in the Wi-Fi module or the network, not
in this image. The integration keeps the add-by-IP advice.

Home Assistant 2026.9.3 against `fw-venus-e-mini-301`: added by IP, every
coordinator read populated, the mode select offered Auto/AI/Manual/UPS and
each write was acknowledged, DoD, LED and BLE writes were acknowledged, and a
Manual slot 5 write carried `manual_set`. Passive and slot 6 were refused by
the integration before any datagram was sent.

**Battery.** The PMU keeps SoC and the energy totals itself; the peers only
report what a pack, DCDC and inverter would. Confirmed on the running image
(RAM reads through the Renode monitor and the frames the PMU sends):

- Replies must carry the polled command. The inverter parser (`0x080259c0`)
  marks a poll answered only on a matching command, and the inverter
  sequence (`0x08025b78`: `0x40 0x41 0x43 0x60 0x44 0x45 0x46 0x01 0x02`,
  then `0x21`) retries and stalls otherwise. A fixed `0x02` reply never got
  past the register reads.
- Register writes must be echoed. `0x08025748` compares six register groups
  and the `cmd 0x72` block (`0x2000b780+0x4b6`, 16 bytes) with the
  inverter's last echo (`+0x4d6`, written by the `0x72` reply handler
  `0x080196ec`) and rewrites while they differ. With zero replies the PMU
  looped on `0x60`/`0x72` and stopped polling `0x02`, so `ongrid_power`
  froze at the last value.
- `cmd 0x72` bytes 0-1 are the direction (1 charge, 2 discharge, 0 none)
  and bytes 2-3 the power in W. Manual `manual_set` 2 at 800 W sent
  `0002 0320`, `manual_set` 1 at 600 W sent `0001 0258`, Auto sent
  `0000 0258`.
- `cmd 0x02` byte 6 (s16) lands at `0x2000b780+0x5c` and is reported as
  `ongrid_power`. `cmd 0x80` carries the pack voltage, the current (mA,
  + = charge; the PMU's `0x2001d55c+8` read 86188 mA while the pack
  delivered 86.2 A), the two cell voltages and the cell temperatures
  (`bat_temp` 25 from 250).
- `cmd 0x21` bytes 0-1 come from `0x2000b780+0xaa`, the CT meter power
  (`0x0801e560`). They stayed 0 with no CT, so the sign under a real meter is
  not confirmed.

Two clocks had to be fixed for the counters to run at real speed. The PMU's
millisecond tick (`0x20002e58`, TIMER6 IRQ) assumes a 72 MHz timer clock
(PSC 1, ARR 35999); at Renode's 10 MHz it ran at 0.139x, and so did SoC and
the energy totals. SysTick is loaded with 143999 for 1 ms at 144 MHz; at
72 MHz the 20 ms control task ran every 40 ms and the energy task (one sum
per 50 runs) counted half. After both: 22016 ticks in 22.05 s, the control
task every 20.4 ms, 800 W for about 193 s gave `total_grid_output_energy`
42 (43 Wh expected), 600 W charge for about 162 s gave
`total_grid_input_energy` 26-27 (27 Wh), and over 120 s of 800 W discharge
the PMU's SoC fell 1.39 % against the pack's 1.40 %.

On a blank SoC record the PMU starts from the open-circuit voltage about a
second after boot, before its first DCDC `0x80` poll. It looked up 0 mV and
started at 43 % whatever the pack held. Its counter is saved to flash
(`0xF000`) only on the power-down path (`0x0802904c`) or a reboot command,
so a container restart loses it the same way. `run_firmware.py` therefore
writes 1 to `0x2001a020` once `0x2001d55c` holds a voltage; the 1 s task
(`0x0802b2d8`) then restarts SoC from the OCV with real readings. A fresh
`--soc 60` image read 60.00 %; after a restart at 59.17 % it read 57.99 %,
since the lookup is in whole percent on a flat LFP plateau.
