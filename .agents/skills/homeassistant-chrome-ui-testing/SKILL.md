---
name: homeassistant-chrome-ui-testing
description: Drive Docker Home Assistant in Chrome via CDP (not screenshot pixels). Restores Chrome DevTools when /json/version dies (Chrome 136+ default profile, ProcessSingleton). Use for Marstek config-flow UI tests (discovery, Confirm device, manual IP/port, delete/re-add, actions, automations) and walkthroughs.
---

# Home Assistant Chrome UI testing

Use this when exercising the Marstek custom integration in Chrome against `.devcontainer/docker-compose.yml`.

**Drive the UI with CDP. Do not click screenshot coordinates.** Pixel clicks miss Ignore vs Add, Submit, IP focus, overflow Menu, and the wrong Chrome tab. `xdotool` guessed `x,y` and computerUse screenshot clicks are forbidden for HA dialogs.

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
H=.agents/skills/homeassistant-chrome-ui-testing/scripts/ha_cdp.py
python3 $H dump
python3 $H click Add --near '00:9b:08:a5:aa:39'
python3 $H fill 'IP address' 172.28.0.20
python3 $H fill Port 30000
python3 $H click Submit
python3 $H wait 'already_in_progress' --timeout 8
python3 $H press Escape
python3 $H navigate 'http://127.0.0.1:8123/config/integrations/dashboard'
python3 $H screenshot /tmp/ha.png
python3 $H token
python3 $H entries
python3 $H devices
python3 $H flows
python3 $H wait-flow --unique-id '02:de:ad:be:ef:04' --timeout 700
python3 $H states --prefix venus_c
python3 $H wait-state sensor.venus_c_battery_power --changed --timeout 90
python3 $H service marstek request_data_sync --data '{"device_id":"<id>"}'
python3 $H delete-entry '<entry_id>'
python3 $H click Menu --near 'Marstek VenusD 1 device' --nth 0
python3 $H click Delete
python3 $H click Delete --near 'permanently deleted'
python3 $H device-actions '<device_id>'
python3 $H run-script '{"domain":"marstek","type":"discharge","device_id":"<id>","metadata":{}}'
python3 $H click 'Overflow menu' --near 'Marstek CDP discharge test' --nth 0
python3 $H click 'Run actions'
python3 $H fire-event marstek_cdp_test
python3 $H entities --prefix venus_d
python3 $H device-triggers '<device_id>'
python3 $H diagnostics '<entry_id>'
python3 $H start-reconfigure '<entry_id>'
python3 $H flow-next '<flow_id>' '{"host":"172.28.0.22","port":30001}'
python3 $H start-options '<entry_id>'
python3 $H enable-entity binary_sensor.venus_d_ct_connection
python3 $H upsert-automation marstek_gap_state '{"alias":"...","triggers":[...],"actions":[...]}'
```

Rules:

1. `dump` before every click. Match **visible text / aria-label**, not pixels.
2. Several **Add** / **Menu** / **Delete** buttons exist. Pass `--near` (MAC, unique_id, `Marstek VenusD 1 device`, dialog heading) or `--nth`. If the result is `ambiguous`, dump and retry — do not guess.
3. Overflow **Menu** context is the `list-item` / config-entry row. Use `--near '<title> 1 device'` and `--nth 0` if the device registry row also matches.
4. Fill IP/port by **field label** (`IP address`, `Port`). The helper targets `ha-form-string` / `ha-form-integer` (HA 2025 `wa-input`) and types into the focused native input. It must **never** assign `ha-form.value` (that clobbers the whole form to a scalar, e.g. Port `30000` wiping the host).
5. Menu items are `ha-dropdown-item`. Radio rows on the picker are `ha-radio-option` (`Enter IP/port manually`).
6. `computerUse` may **look** at the screen. It must not click HA. `xdotool` may focus the Chrome window only.
7. Type HA URLs into `navigate` (or Chrome’s address bar). Do not walk Overview → Settings → Devices & services unless recording a user-facing demo.

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
| `/config/integrations/integration/marstek` | Only Marstek entries (delete Menu lives here) |
| `/config/devices/dashboard` | Device tiles (SoC/mode live check) |
| `/config/entities?domain=marstek` | Entity states |
| `/config/logs` | UI log (also `sudo docker logs marstek-ha-dev`) |
| `/config/automation/dashboard` | Automations (singular `automation`, not `automations`) |
| `/config/devices/device/<id>` | Device page: mode select, SoC, power, SYS number/switch |

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

`already_in_progress` means that MAC already has an open confirm flow — `press Escape` / click Close, do not start a second manual add for the same device. `flows` lists those (`step_id=confirm`).

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

If the last entry on that port was deleted, there is **no** pooled client. Then the log is `Querying device info from HOST:PORT` without `via pooled UDP client`. That is expected (unique-port Venus D on 30002 after delete; Venus A on 30001). Same-port re-add while C or another E is still loaded **must** show `via pooled`.

## Delete, re-add, live updates, actions

Use `entries` / `devices` / `states` / `entities` before mutating anything. Join key is BLE-MAC on `devices[].identifiers`. HA 2026.8+ also exposes `config_entry_id` (one entry per device).

### Delete

Prefer REST so the HTTP call stays open until it finishes ([config entries](https://developers.home-assistant.io/docs/config_entries_index); no WebSocket delete):

```bash
python3 $H delete-entry '<entry_id>'
```

UI recipe (when recording): `/config/integrations/integration/marstek` → overflow **Menu** `--near 'Marstek VenusD 1 device' --nth 0` → **Delete** (`ha-dropdown-item`) → confirm **Delete** `--near 'permanently deleted'`.

After delete, the scanner should rediscover the MAC. Periodic scan is **10 minutes** (`SCAN_INTERVAL`). Unconfigured discovery is debounced **1 hour** only while the MAC stays in `_unconfigured_seen`; configuring the device prunes that cache, so a **delete then next scan** can re-create the flow immediately. Poll:

```bash
python3 $H wait-flow --unique-id '<ble-mac>' --timeout 700
```

Then `click Add --near '<mac>'` on the dashboard. Do not assume 60s.

### Re-add

1. Discovery Confirm (`async_step_confirm`) — same as path 1 above. Proves pooled GetDevice when another device still owns that UDP port.
2. Manual IP/port — path 2. Use this after delete when you want to type host/port again without waiting for the scanner.

Entity IDs must come back the same (`sensor.venus_d_battery_level`, not `_2`) because unique IDs are BLE-MAC. Check with `entities --prefix venus_d`. See [references/HA_API.md](references/HA_API.md).

### Live updates

Mock power/SoC move every coordinator cycle (fast tier default 30s). Do **not** add per-entity polling.

```bash
python3 $H service marstek request_data_sync --data '{"device_id":"<device_id>"}'
python3 $H wait-state sensor.venus_c_battery_power --changed --timeout 90
```

`last_updated` changing counts as a change even if the watt value is identical.

### Device page + services

On `/config/devices/device/<id>`: Battery SoC, power, status, operating mode. Venus C / Rev 3.1 Venus E also have Depth of discharge, Bluetooth, Panel LED.

Select entity options: `auto`, `ai`, `ups` (profile-gated). **Do not** pick `manual`/`passive` on the select — those need `marstek.set_passive_mode` or device actions.

```bash
python3 $H service select select_option --data '{"entity_id":"select.venus_c_operating_mode","option":"ai"}'
python3 $H service number set_value --data '{"entity_id":"number.venus_c_depth_of_discharge","value":80}'
python3 $H service switch turn_off --data '{"entity_id":"switch.venus_c_panel_led"}'
python3 $H service marstek set_passive_mode --data '{"device_id":"<id>","power":-400,"duration":90}'
```

Device actions (Then do → Device): **Charge battery**, **Discharge battery**, **Stop charging/discharging**. List them with `device-actions`. Firing via `run-script` uses WS `execute_script` ([device automation actions](https://developers.home-assistant.io/docs/device_automation_action)).

**Do not block a recording on `run-script` for Marstek charge/discharge/stop.** Those actions verify for up to 8 × ~60s (`poll_cycle`). Mode often flips to `manual` within seconds; verification then retries because mock power does not match the requested watts. Start the script, then `wait-state sensor.*_device_mode --equals manual`. Prefer `set_passive_mode` or `select.select_option` when you only need a mode change. New device automations are not accepted for new integrations; this repo already has them.

```bash
python3 $H device-actions '<device_id>'
python3 $H run-script '{"domain":"marstek","type":"discharge","device_id":"<id>","metadata":{}}'
python3 $H wait-state sensor.venus_c_device_mode --equals manual --timeout 90
```

### Automations

UI: **`/config/automation/dashboard`** (singular). `/config/automations/dashboard` is the wrong path.

Overflow **Overflow menu** on that page lives in a data-table row. After extending `contextOf` with `data-table` / `tr`, `--near 'Marstek CDP discharge test'` matches. Then click **Run actions** (`ha-dropdown-item`). Last triggered should change to **now**.

Prefer building in the UI when recording. `POST /api/config/automation/config/{id}` is **not** in the official REST docs; do not treat it as the supported API.

To exercise an existing event automation:

```bash
python3 $H click 'Overflow menu' --near 'Marstek CDP discharge test' --nth 0
python3 $H click 'Run actions'
python3 $H fire-event marstek_cdp_test
python3 $H wait-state sensor.venus_c_device_mode --equals manual --timeout 90
```

`POST /api/events/{event_type}` is the official fire-event path.

Marstek does **not** ship `device_trigger.py` / `device_condition.py`. HA still lists generic device triggers/conditions from entities (`current_option_changed`, `battery_level`, `power`). `device-triggers` / `device-conditions` return those. Official note: new device automations are not accepted ([device automation index](https://developers.home-assistant.io/docs/device_automation_index/)).

Official automation triggers: [state](https://www.home-assistant.io/docs/automation/trigger/), [numeric_state](https://www.home-assistant.io/triggers/numeric_state/), [event](https://www.home-assistant.io/triggers/event/), template, time, time_pattern, webhook, device, persistent_notification. Skip sun/MQTT/zone/calendar/sentence unless the install has those integrations.

`numeric_state` **only fires when the value crosses the threshold**. Mocks already sit at SoC ~5–10%, so `below: 15` on battery level will **not** fire until SoC rises above 15 then drops. Use a writable number (`number.*_depth_of_discharge`) for a live crossing, and put SoC `below: 15` on a **condition** instead.

`enable-entity` returns `{entity_entry: {entity_id, disabled_by}, reload_delay: 30}`. Wait 30s; the coordinator reloads that entry. `Wifi.GetStatus` entities (`sensor.*_wifi_signal_strength`) are safe to enable. Do **not** enable `bat_*` (issue #14).

`upsert-script` body must **omit** `id` (HA 2026.9: `Message malformed: not a valid option at 'id'`). Automations may include `id`.

## Remaining surfaces (gap campaign)

Already covered: discovery Confirm, manual IP/port, delete/re-add, same-port pooled GetDevice, live power, select ai/auto, discharge action, `set_passive_mode`, event automation + Run actions.

Still exercise these (use a **different device** than one with an in-flight `execute_script` verification):

| Surface | How |
|---------|-----|
| Charge / stop device actions | `upsert-automation` with `type: charge` then `fire-event`; wait-state `device_mode=manual`. Do not await `run-script`. Stop: `select.select_option auto` is faster than `type: stop`. |
| UPS | `select.select_option` `ups` on Venus C / Rev 3.1 Venus E |
| SYS BLE + DOD + LED | `switch.turn_on` / `number.set_value` on firmware that supports SYS |
| Manual schedules | `marstek.set_manual_schedule`, `set_manual_schedules`, `clear_manual_schedules` (3 retries, seconds not minutes) |
| CT binary sensor | Disabled by default. `enable-entity binary_sensor.venus_d_ct_connection` then wait 30s + `on`. Do **not** enable `bat_*`. |
| WiFi RSSI | `enable-entity sensor.venus_d_wifi_signal_strength` (Wifi.GetStatus). Expect a negative dBm after reload. |
| PV | Venus A/D `sensor.venus_d_pv1_power` `wait-state --changed` |
| Reconfigure | `start-reconfigure ENTRY` then `flow-next`. Prove `cannot_connect` (bad IP), `unique_id_mismatch` (another mock's IP), then same host/port `reconfigure_successful`. |
| Options | `start-options ENTRY`; submit `polling_settings` / `network_settings` / `power_settings` sections. Reloads the entry. |
| Reload / diagnostics | `reload-entry`; `diagnostics ENTRY` (`GET /api/diagnostics/config_entry/{id}`). |
| already_configured | User flow while all MACs are loaded, then manual `host` of an existing device → abort `already_configured`. |
| State / numeric_state | State: `select` → `ai`. Numeric: DOD `below` after `number.set_value`. SoC `below: 15` as a **condition** on an event automation. |
| Template / webhook / time_pattern | Template on select `ai`; POST `/api/webhook/<id>`; `seconds: "/15"`. |
| Device trigger | Generic `current_option_changed` from `device-triggers`. |
| Persistent notification trigger | Create `notification_id` then listen for `update_type: added`. |
| Conditions + choose | Event + numeric_state/state conditions; `choose` on `battery_status`. |
| Script | `upsert-script` (no `id` in body) + `script.turn_on` calling `marstek.request_data_sync`. |

`upsert-automation` / `upsert-script` hit `POST /api/config/automation/config/{id}` and `/api/config/script/config/{id}` — **not** in official REST docs. Prefer the UI when recording.

## Extensive live campaign

Run this against Docker mocks after a UDP/config-flow change. Keep compose up.

1. `entries` / `flows` / `states --prefix venus` — inventory.
2. Discovery Confirm: Add a leftover Discovered MAC (`click Add --near '<mac>'` → Submit). Watch logs for `via pooled UDP client` when the port is shared.
3. Delete one configured device (`delete-entry` or UI Menu). `wait-flow --unique-id '<mac>' --timeout 700`. Confirm re-add. Entity IDs must not grow `_2`.
4. Delete a **same-port** device while another still uses 30000. Manual re-add IP/port. Must log pooled GetDevice.
5. Delete a **unique-port** device (Venus A `:30001` / Venus D `:30002`). Manual re-add is allowed immediately; discovery Confirm may wait for the 10 min scanner. GetDevice without `via pooled` is expected if no other client remains on that port.
6. `request_data_sync` + `wait-state --changed` on battery power for each re-added device.
7. Device page / services: select `ai` then `auto`; `set_passive_mode`; SYS number/switch if the profile allows it.
8. `device-actions` to confirm charge/discharge/stop exist. Use `set_passive_mode` or `select.select_option` for a fast mode change. If you `run-script` a device action, do not wait for `execute_script` to return (verification can take many minutes).
9. Open `/config/automation/dashboard`, overflow **Run actions** on a Marstek discharge automation, and/or `fire-event`. Last triggered must update.

Do not cite 0-byte `mp4` files. Discard failed recordings.

## Recording

1. `ha_cdp.py ensure` and land on `/config/integrations/dashboard` **before** `START_RECORDING`.
2. Record only the add/confirm/entity proof. Drive clicks with `ha_cdp.py` while the screen records.
3. After Submit, wait for the device page: Battery SoC, mode, and (Venus A/D) PV must not stay `unavailable`.
4. If Confirm device or manual fails, **discard** the recording, grab `docker logs`, fix, then re-record a passing run.
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
