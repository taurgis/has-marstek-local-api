---
name: marstek-open-api-udp
description: How this integration talks to Marstek devices via the local Open API over UDP (JSON messages) and how to handle errors safely
---

# Marstek Device Open API (UDP)

This repository communicates with Marstek devices using the **Open API over UDP** as documented in `docs/marstek_device_openapi.MD`.
In code, this is encapsulated by the `py-marstek` library (`pymarstek`).

## When to Use

- You need to change how device data is fetched (polling)
- You’re debugging connectivity or “no data” scenarios
- You’re adding new data points that might require additional Open API methods

## Transport & Message Shape

- Transport is UDP to the device (default port **30000**).
- Bind the local socket to the **same port the device listens on**. Several firmware builds reply to that listen port instead of the client’s ephemeral source port. The Open API port is **user-configurable**, so the integration keeps **one socket per unique listen port** (devices that share a port share a socket). `SO_REUSEPORT` is set so a second bind can share a port, but Linux then load-balances replies across every still-bound socket. **Pause does not unbind.** Broadcast discovery must pause pooled listeners and bind its own sockets. Unicast `Marstek.GetDevice` (manual add, Confirm device, repairs) must reuse the pooled `MarstekUDPClient.send_request(...)` for that port. A second bind after pause produces `cannot_connect` / `No valid response from device` while the coordinator `Recv`s the GetDevice reply.
- Messages are JSON objects with a `method` and `params`, e.g.:
  - Discovery: `Marstek.GetDevice`
  - Status: `ES.GetStatus`, `ES.GetMode`, `Bat.GetStatus`, `PV.GetStatus`, `EM.GetStatus`
  - Control: `ES.SetMode`

Discovery pattern from the spec:
- A UDP broadcast may first receive a `Parse error` response (devices reacting to a non-JSON broadcast probe).
- Then send a proper JSON request with `method: Marstek.GetDevice` to receive the device’s metadata including `ip`, `ble_mac`, `wifi_mac`, `device`, and `ver`.

## Key Objects & Calls (in this repo)

- Library client: `pymarstek.MarstekUDPClient`
- Used patterns:
  - `await udp_client.discover_devices(...)` (config flow + scanner)
  - `await udp_client.send_request(...)` (setup connectivity check)
  - `await udp_client.get_device_status(...)` (coordinator polling)

Important library behavior (current implementation):
- `get_device_status(...)` can return **default values** on failure instead of raising.
- The coordinator treats `device_mode == "Unknown"` as “no valid data received” and falls back to previous `coordinator.data`.

## Error Handling Contract

The integration aims to be resilient to device/network flakiness:

- **Timeouts / OSError / ValueError**
  - Meaning: UDP request did not complete, network unreachable, or response parse issues.
  - Behavior in polling: log a warning and return previous data (entities keep last-known values instead of flapping).
  - Behavior in setup: raise `ConfigEntryNotReady` to let HA retry while the scanner updates IP if needed.

Local API enablement:
- Devices must have **OPEN API enabled** in the Marstek app or discovery/polling will fail.

## Concurrency Guidance

Marstek devices can be sensitive to request bursts.

- Prefer one request per update interval.
- Avoid parallel requests.
- Keep all I/O in the coordinator (entities read from coordinator data).
- All device I/O must stay async; do not introduce blocking sockets or file access on the event loop.

Control actions (`ES.SetMode`):
- Pause polling for the target host while sending a command + verifying the result (see `custom_components/marstek/device_action.py`).
- Use retries + backoff; UDP packets may be dropped.

## Wi-Fi vs Ethernet

Venus Control images speak Open API on two radios:

- Ethernet: WCH CH395 (`Extract_udp_data_ch395` / `CH395SendData`). Firmware **150** is the vendor fix for Local API **send** failures on this path.
- Wi-Fi: Quectel FC41D `AT+QIOPEN=…,"UDP SERVICE"` / `AT+QISEND`. That path is unchanged in 150. The module UART is shared with MQTT/HTTP. Wi-Fi unicasts after idle can time out even when Ethernet is stable; the public AT manual does not document a deterministic first-packet drop.

For firmware the profile marks **`openapi_wifi_retransmit_safe`** (known family, known Control generation, not reset-prone — Venus 150+ / HMG-50 156+), **read-only** unicasts (`Marstek.GetDevice`, `ES.GetStatus`, `ES.GetMode`, `EM.GetStatus`, `PV.GetStatus`, `Wifi.GetStatus`, `Bat.GetStatus`) may:

1. Send once, wait 500 ms. Retransmit only if that wait is silent (RFC 1122 UDP retransmission is the application's job). Ethernet replies typically land well before this, so LAN and dual-homed Ethernet IPs stay one datagram.
2. Wait the **remaining** configured request timeout for a matching reply **without cancelling** the pending future (`asyncio.wait`, not `wait_for`). Cap is **two datagrams** inside one timeout.

Writes (`ES.SetMode`, `DOD.SET`, `Ble.Adv`, `Led.Ctrl`), unknown models, missing `ver`, and reset-prone IPs stay at one datagram and one wait. Prefer Ethernet for polling; retries cannot repair AP client isolation or Wi-Fi NAT.

## Practical Debugging Steps

- If discovery finds no devices:
  - Confirm device is on the same LAN segment and OPEN API is enabled.
  - Confirm UDP port (default 30000) is not blocked.
- If polling returns stale/default data:
  - Check logs for `device_mode=Unknown` warnings (indicates no valid response).
  - Validate the device IP in the config entry; scanner should update it automatically.

## When NOT to Use

- Don’t implement raw UDP handling in entities; keep it inside `pymarstek` + coordinator.
- Don’t add extra Open API calls per poll cycle unless you can justify the added device load.
