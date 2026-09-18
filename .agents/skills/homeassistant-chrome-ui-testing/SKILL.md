---
name: homeassistant-chrome-ui-testing
description: Fast Chrome / computerUse navigation for testing the Marstek integration against Docker Home Assistant and mock devices. Use when adding devices in the HA UI, verifying config flow (discovery, Confirm device, manual IP/port), or recording a walkthrough.
---

# Home Assistant Chrome UI testing

Use this when exercising the Marstek custom integration in a browser against `.devcontainer/docker-compose.yml`. Type HA URLs in Chrome’s address bar. Do not click through Overview → Settings → Devices & services unless you are recording a user-facing demo.

## Bring-up

From `.devcontainer/`:

```bash
sudo docker compose up -d --build
```

Wait until `http://127.0.0.1:8123/api/onboarding` responds. On this Cloud VM, `iptables-legacy` FORWARD may drop Docker ICC; if HA cannot ping `172.28.0.26`, allow the compose bridge:

```bash
sudo iptables-legacy -P FORWARD ACCEPT
sudo iptables-legacy -I FORWARD -i br-+ -j ACCEPT
sudo iptables-legacy -I FORWARD -o br-+ -j ACCEPT
```

Mock image must COPY `custom_components/marstek/firmware_profile.py` and `custom_components/marstek/pymarstek/const.py` or every mock exits on import.

The integration is bind-mounted at `/config/custom_components`. Restart `marstek-ha-dev` after Python edits so HA reimports it.

## Login (once)

Fresh `marstek-ha-config` volume needs onboarding. After that, Chrome login:

| Field | Value used in compose HA |
|-------|--------------------------|
| URL | `http://127.0.0.1:8123` |
| Username | `admin` |
| Password | `marstek-dev` |

Dismiss the browser “save password” bubble immediately. Skip area assignment on new devices.

Onboard via REST when the UI wizard would waste recording time (`POST /api/onboarding/users`, `core_config`, `analytics`, then `integration` with `redirect_uri`). A refresh token from a prior login still works; reuse it instead of typing the password.

## Fast routes (paste these)

Base: `http://127.0.0.1:8123`

| Path | Use for |
|------|---------|
| `/config/integrations/dashboard` | **Start here.** Discovered cards + Configured list + “+ Add integration” |
| `/config/integrations/integration/marstek` | Only Marstek entries |
| `/config/devices/dashboard` | Device tiles (SoC/mode live check) |
| `/config/entities?domain=marstek` | Entity states |
| `/config/logs` | UI log (also `sudo docker logs marstek-ha-dev`) |
| `/developer-tools/yaml` | Reload only if you must; prefer `docker restart marstek-ha-dev` after Python edits |

## Mock devices

| IP | Port | Model | What it proves |
|----|------|-------|----------------|
| `172.28.0.20` | 30000 | Venus E 145 | Same-port share with C |
| `172.28.0.25` | 30000 | Venus E 150 | Same-port, SYS/UPS |
| `172.28.0.22` | 30001 | Venus A 148 | Custom port + PV |
| `172.28.0.23` | 30002 | Venus D 145 | Custom port + PV |
| `172.28.0.24` | 30003 | Venus A 149 | Custom port + scaled solar |
| `172.28.0.26` | 30000 | Venus C 153 | Issue #60 same-port reply |

Unicast check from the HA container (not the VM host):

```bash
sudo docker exec -i marstek-ha-dev python3 - <<'PY'
import json, socket
cmd = json.dumps({"id": 1, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}).encode()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.settimeout(3)
sock.sendto(cmd, ("172.28.0.26", 30000))
print(sock.recvfrom(4096)[0])
PY
```

## Config-flow paths (test all three)

### 1) Discovered card → Confirm device (scanner)

1. Open `/config/integrations/dashboard`.
2. Under **Discovered**, a Marstek card has **Add**.
3. Add opens **Confirm device** with editable **IP address** and **Port**.
4. Submit as-is, **or change IP/port** then Submit.

This is `async_step_confirm` from `SOURCE_INTEGRATION_DISCOVERY`. It always unicast-probes with `get_device_info`. That probe must reuse the pooled UDP client for the target port. Pausing the coordinator listener is **not** enough (see Same-port GetDevice below).

### 2) Manual add, no auto-detect

1. `+ Add integration` → search `Marstek`.
2. Wait for the picker (broadcast can take ~10s, retry ~3s).
3. Choose **Enter IP/port manually** (or land here when discovery is empty).
4. Click the **IP address** field so it has focus, type the IP, then Port → Submit with **Enter**.

Same unicast probe as Confirm device. Do not assume “discovery found it, so manual will work” — manual does not reuse the cached broadcast result.

### 3) `+ Add integration` picker

Selecting a listed device creates the entry from discovery data (no extra GetDevice). Fastest happy path when the IP/port in the label is already correct. Broadcast discovery **does** pause pooled listeners, because it binds its own sockets.

## Same-port GetDevice (do not “fix” with pause)

Firmware replies to the device listen port, not the ephemeral source. Per-port sockets are correct for mixed 30000/30001/… installs. Linux `SO_REUSEPORT` load-balances datagrams across every socket still **bound** to that port. Pause stops the listener task; it does **not** unbind.

Wrong path (second bind after pause) live signature:

- `Querying device info from 172.28.0.20:30000`
- `UDP socket bound to 0.0.0.0:30000`
- `Invalid device response from 172.28.0.26` carrying `EM.GetStatus` (probe ate coordinator traffic)
- `No valid response from device at 172.28.0.20:30000`
- Coordinator `Recv` of `Marstek.GetDevice` a few ms later

Right path: `get_device_info(..., udp_client=pooled_client)` so the existing listener matches a unique request id. Log should say `Querying device info from HOST:PORT via pooled UDP client`, with **no** second `UDP socket bound` line.

Use this when Venus C (`172.28.0.26:30000`) is already configured and you add Venus E (`172.28.0.20:30000`) via manual or Confirm device.

## computerUse / recording

1. Finish onboarding, login, and land on `/config/integrations/dashboard` **before** `START_RECORDING`.
2. Record only the add/confirm/entity proof. Do not record compose pull, onboarding, or password-save clicks.
3. After Submit, wait for the device page: Battery SoC, mode, and (Venus A/D) PV must not stay `unavailable`.
4. If Confirm device or manual fails, **discard** the recording, grab `docker logs`, fix, then re-record a passing run.
5. computerUse auto-resume hits a **100-image cap**. For long sessions, drive Chrome with `xdotool` and record with `ffmpeg` (or `RecordScreen`) instead of relying on screenshot loops.

Dialog input:

- Click the IP/port field, then type. Missed focus leaves host empty → `cannot_connect`.
- Prefer **Enter/Return** to submit HA dialogs. Clicks on the **Submit** button often miss.
- The search box in “Add integration” is a separate field from the manual IP form.

Enable debug for a failing run:

```bash
# after login, with a Bearer token
curl -sS -X POST http://127.0.0.1:8123/api/services/logger/set_level \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"custom_components.marstek":"debug"}'
```

Look for `UDP socket bound to 0.0.0.0:30000` during manual/confirm (second bind — bug), `via pooled UDP client` (expected), `No valid response from device`, and `cannot_connect`.

## When NOT to use

- Pytest / config-flow unit tests: `homeassistant-testing-playbook` and `tests/test_config_flow.py`.
- Protocol / bind-port behavior: `marstek-open-api-udp`.
- Mock firmware encodings: `mock-device-development`.
