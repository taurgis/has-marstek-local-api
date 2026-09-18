---
name: homeassistant-chrome-ui-testing
description: Drive Docker Home Assistant in Chrome via CDP (not screenshot pixels). Restores Chrome DevTools when /json/version dies (Chrome 136+ default profile, ProcessSingleton). Use for Marstek config-flow UI tests (discovery, Confirm device, manual IP/port) and walkthroughs.
---

# Home Assistant Chrome UI testing

Use this when exercising the Marstek custom integration in Chrome against `.devcontainer/docker-compose.yml`.

**Drive the UI with CDP. Do not click screenshot coordinates.** Pixel clicks miss Ignore vs Add, Submit, IP focus, and the wrong Chrome tab. `xdotool` guessed `x,y` and computerUse screenshot clicks are forbidden for HA dialogs.

## Restore DevTools first

Chrome 148 in this environment is past **Chrome 136**. `--remote-debugging-port` is **ignored** on the default profile (`~/.config/google-chrome`). A second `google-chrome … --remote-debugging-port=9222` does **not** enable CDP either: Chromium `ProcessSingleton` attaches to the existing process and swallows the new flags.

Always verify, then fix with the helper (do not relaunch by hand unless the helper fails):

```bash
python3 .agents/skills/homeassistant-chrome-ui-testing/scripts/ha_cdp.py status
# If ok=false:
python3 .agents/skills/homeassistant-chrome-ui-testing/scripts/ha_cdp.py ensure
```

`ensure` quits **specific Chrome PIDs** (never `pkill -f`), then launches:

```text
--remote-debugging-port=9222
--remote-allow-origins=*
--user-data-dir=/tmp/chrome-ha-debug
```

Success is `GET http://127.0.0.1:9222/json/version` returning `webSocketDebuggerUrl`. If that URL 404s/refuses, CDP is down — do not fall back to pixels.

| Symptom | Cause | Fix |
|---------|--------|-----|
| `/json/version` connection refused | Flag ignored (default profile) or Chrome started without it | `ha_cdp.py ensure` |
| Second Chrome command exits immediately; 9222 still closed | `ProcessSingleton` reused the live instance | Quit those PIDs, then `ensure` |
| HTTP 9222 works, WebSocket 403 | Client sent `Origin`; flag missing | Relaunch with `--remote-allow-origins=*` |
| `ensure` still fails | Stale singleton files in the debug profile | Helper deletes `SingletonLock/Cookie/Socket` |

Official notes: [Chrome 136 remote-debugging-port](https://developer.chrome.com/blog/remote-debugging-port), [CDP HTTP endpoints](https://chromedevtools.github.io/devtools-protocol/). Details in [references/CHROME_DEVTOOLS.md](references/CHROME_DEVTOOLS.md).

## Drive HA with `ha_cdp.py`

Script: `.agents/skills/homeassistant-chrome-ui-testing/scripts/ha_cdp.py` (needs `aiohttp`).

```bash
python3 …/scripts/ha_cdp.py dump
python3 …/scripts/ha_cdp.py click Add --near '00:9b:08:a5:aa:39'
python3 …/scripts/ha_cdp.py fill 'IP address' 172.28.0.20
python3 …/scripts/ha_cdp.py fill Port 30000
python3 …/scripts/ha_cdp.py click Submit
python3 …/scripts/ha_cdp.py wait 'already_in_progress' --timeout 8
python3 …/scripts/ha_cdp.py press Escape
python3 …/scripts/ha_cdp.py navigate 'http://127.0.0.1:8123/config/integrations/dashboard'
python3 …/scripts/ha_cdp.py screenshot /tmp/ha.png
python3 …/scripts/ha_cdp.py token
```

Rules:

1. `dump` before every click. Match **visible text / aria-label**, not pixels.
2. Several **Add** buttons exist. Pass `--near` (MAC / unique_id / dialog heading) or `--nth`. If the result is `ambiguous`, dump and retry — do not guess.
3. Fill IP/port by **field label** (`IP address`, `Port`). The helper targets `ha-form-string` / `ha-form-integer` (HA 2025 `wa-input`) and types into the focused native input. Do not click an empty host field “somewhere in the dialog”.
4. `computerUse` may **look** at the screen. It must not click HA. `xdotool` may focus the Chrome window only.
5. Type HA URLs into `navigate` (or Chrome’s address bar). Do not walk Overview → Settings → Devices & services unless recording a user-facing demo.

## Bring-up

From `.devcontainer/`:

```bash
sudo docker compose up -d --build
```

Wait until `http://127.0.0.1:8123/api/onboarding` responds. On this Cloud VM, `iptables-legacy` FORWARD may drop Docker ICC; if HA cannot ping `172.28.0.26`:

```bash
sudo iptables-legacy -P FORWARD ACCEPT
sudo iptables-legacy -I FORWARD -i br-+ -j ACCEPT
sudo iptables-legacy -I FORWARD -o br-+ -j ACCEPT
```

Mock image must COPY `custom_components/marstek/firmware_profile.py` and `custom_components/marstek/pymarstek/const.py` or every mock exits on import.

The integration is bind-mounted at `/config/custom_components`. Restart `marstek-ha-dev` after Python edits so HA reimports it.

## Login (once)

Fresh `marstek-ha-config` volume needs onboarding. After that:

| Field | Value |
|-------|--------|
| URL | `http://127.0.0.1:8123` |
| Username | `admin` |
| Password | `marstek-dev` |

Dismiss the browser “save password” bubble immediately. Skip area assignment.

Reuse a refresh token instead of typing the password. After login, `ha_cdp.py token` writes `/tmp/ha_access_token.txt` from the live page (REST tokens expire).

Onboard via REST when the UI wizard would waste recording time (`POST /api/onboarding/users`, `core_config`, `analytics`, then `integration` with `redirect_uri`).

## Fast routes

Base: `http://127.0.0.1:8123`

| Path | Use for |
|------|---------|
| `/config/integrations/dashboard` | **Start here.** Discovered cards + Configured list + “+ Add integration” |
| `/config/integrations/integration/marstek` | Only Marstek entries |
| `/config/devices/dashboard` | Device tiles (SoC/mode live check) |
| `/config/entities?domain=marstek` | Entity states |
| `/config/logs` | UI log (also `sudo docker logs marstek-ha-dev`) |
| `/developer-tools/yaml` | Prefer `docker restart marstek-ha-dev` after Python edits |

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
3. `dump` and `click Add --near '<unique_id>'` (MAC on the flow, not the first Add on the page).
4. Confirm device has editable **IP address** and **Port**. Submit as-is, **or** `fill` then Submit.

This is `async_step_confirm` from `SOURCE_INTEGRATION_DISCOVERY`. It always unicast-probes with `get_device_info`. That probe must reuse the pooled UDP client for the target port. Pausing the coordinator listener is **not** enough (see Same-port GetDevice).

`already_in_progress` means that MAC already has an open confirm flow — `press Escape` / click Close, do not start a second manual add for the same device.

### 2) Manual add, no auto-detect

1. `click 'Add integration'` → wait → search `Marstek`.
2. Wait for the picker (broadcast can take ~10s).
3. `click` **Enter IP/port manually** (a `ha-radio-option` on the device picker; or land here when discovery is empty).
4. Submit the picker, then `fill 'IP address'` / `fill Port` (labels, not pixels) → Submit with `press Enter`.

Same unicast probe as Confirm device. Manual does not reuse the cached broadcast result.

### 3) `+ Add integration` picker

Selecting a listed device creates the entry from discovery data (no extra GetDevice). Fastest happy path when the IP/port in the label is already correct. Broadcast discovery **does** pause pooled listeners, because it binds its own sockets.

## Same-port GetDevice (do not “fix” with pause)

Firmware replies to the device listen port, not the ephemeral source. Per-port sockets are correct for mixed 30000/30001/… installs. Linux `SO_REUSEPORT` load-balances datagrams across every socket still **bound** to that port. Pause stops the listener task; it does **not** unbind.

Wrong path (second bind after pause) live signature:

- `Querying device info from 172.28.0.20:30000`
- `UDP socket bound to 0.0.0.0:30000`
- `Invalid device response from 172.28.0.26` carrying `EM.GetStatus`
- `No valid response from device at 172.28.0.20:30000`
- Coordinator `Recv` of `Marstek.GetDevice` a few ms later

Right path: `get_device_info(..., udp_client=pooled_client)`. Log: `Querying device info from HOST:PORT via pooled UDP client`, with **no** second `UDP socket bound` line.

Use this when Venus C (`172.28.0.26:30000`) is already configured and you add Venus E (`172.28.0.20:30000`) via manual or Confirm device.

## Recording

1. `ha_cdp.py ensure` and land on `/config/integrations/dashboard` **before** `START_RECORDING`.
2. Record only the add/confirm/entity proof. Drive clicks with `ha_cdp.py` while the screen records.
3. After Submit, wait for the device page: Battery SoC, mode, and (Venus A/D) PV must not stay `unavailable`.
4. If Confirm device or manual fails, **discard** the recording, grab `docker logs`, fix, then re-record a passing run. Do not cite 0-byte `mp4` files.
5. computerUse auto-resume hits a **100-image cap**. Prefer CDP + `ffmpeg` / `RecordScreen` over screenshot loops.

Enable debug for a failing run (token from `ha_cdp.py token`):

```bash
curl -sS -X POST http://127.0.0.1:8123/api/services/logger/set_level \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"custom_components.marstek":"debug"}'
```

Look for `UDP socket bound to 0.0.0.0:30000` during manual/confirm (second bind — bug), `via pooled UDP client` (expected), `No valid response from device`, and `cannot_connect`.

## When NOT to use

- Pytest / config-flow unit tests: `homeassistant-testing-playbook` and `tests/test_config_flow.py`.
- Protocol / bind-port behavior: `marstek-open-api-udp`.
- Mock firmware encodings: `mock-device-development`.
