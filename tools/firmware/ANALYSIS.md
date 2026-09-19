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
   those IPs are serialized on a per-IP lock. `pause_polling` is ref-counted.
6. A non-fixable Home Assistant warning is created from config-entry metadata
   **before** the first UDP probe and points at issue #15.

## Mock device

`tools/mock_device` now reproduces:

- uint16 JSON-RPC id truncation
- parse-error reply `id=0`, code `-32700`
- Open API freeze after a 0-byte datagram
- duplicate UDP replies on reset-prone firmware; a single reply on 150+

## Related vendor notes (other SKUs)

These are **not** Venus E 3.0 Control, but they show Marstek has been chasing
the same class of bug on other products:

- HME-3 v122 / HME-4 v124: `udp协议v4 修复udp复位bug` (UDP protocol v4, fix UDP reset bug)
- HMG-50 Control v156: `优化OpenApi接口稳定性` (optimized Open API interface stability)

Venus A/D reports on issue #15 used the same Local API stack symptoms. This
integration treats every **known family** below Control generation 150 as
reset-prone. Venus E 3.0 **150** is the build with a published Local API
Ethernet fix plus a user confirmation.
