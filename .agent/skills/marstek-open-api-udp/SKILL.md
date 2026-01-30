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
