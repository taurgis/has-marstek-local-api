# Home Assistant REST / WebSocket used by `ha_cdp.py`

These go through the logged-in page (`hass.callApi` / `hass.callWS`), not a long-lived token file.

Official REST reference: [developers.home-assistant.io/docs/api/rest](https://developers.home-assistant.io/docs/api/rest)

| Command | HA API | Notes |
|---------|--------|--------|
| `ha_cdp.py api GET config/config_entries/entry` | GET `/api/config/config_entries/entry` | List entries. This HA version omits `unique_id` / `data` on the list payload. Config-entry HTTP routes are implemented in core (`config/config_entries.py`) and are **not** listed on the REST reference page. |
| `ha_cdp.py delete-entry ENTRY_ID` | DELETE `/api/config/config_entries/entry/{entry_id}` | Only delete mechanism ([config entries](https://developers.home-assistant.io/docs/config_entries_index)). No WebSocket delete. Keep the HTTP call open until it returns ([core issue #178092](https://github.com/home-assistant/core/issues/178092)). |
| `ha_cdp.py reload-entry ENTRY_ID` | POST `/api/config/config_entries/entry/{id}/reload` | Unload + setup without wiping the entity registry. Same “don’t cancel the HTTP call” caveat. |
| `ha_cdp.py flows` | WS `config_entries/flow/progress` | Discovery flows waiting for the user. Does **not** list user-initiated flows. |
| `ha_cdp.py wait-flow --unique-id MAC` | polls `flow/progress` | After delete, wait for scanner rediscovery (up to 10 min). |
| `ha_cdp.py abort-flow FLOW_ID` | DELETE `/api/config/config_entries/flow/{flow_id}` | Abort an in-progress confirm/manual flow. |
| `ha_cdp.py service …` | POST `/api/services/<domain>/<service>` | Same path the UI uses. Blocks until the service finishes. |
| `ha_cdp.py fire-event TYPE` | POST `/api/events/{event_type}` | Official fire-event path. |
| `ha_cdp.py states --entity ID` | `hass.states` | Live entity object. |
| `ha_cdp.py devices` | WS `config/device_registry/list` | MAC is `identifiers[0][1]` (`marstek`, BLE-MAC). HA 2026.8 adds `config_entry_id`; `config_entries` is a compatibility shim ([blog](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)). Child devices (2026.9 `parent_device_id`) are skipped. |
| `ha_cdp.py entities` | WS `config/entity_registry/list` | `unique_id` stays BLE-MAC based after delete/re-add. |
| `ha_cdp.py entries` | entries + devices joined | Adds `device_id` / `mac` / `model`. |
| `ha_cdp.py device-actions DEVICE_ID` | WS `device_automation/action/list` | Charge / discharge / stop plus generic entity actions. |
| `ha_cdp.py run-script JSON` | WS `execute_script` | No dedicated “fire device action” command. Marstek charge/discharge/stop **block** until verification finishes (up to 8 × ~60s). |
| `ha_cdp.py start-reconfigure ENTRY_ID` | POST `/api/config/config_entries/flow` with `entry_id` | Starts `async_step_reconfigure`. Not a documented public WS command. |
| `ha_cdp.py start-options ENTRY_ID` | POST `/api/config/config_entries/options/flow` | Options flow ([options flow](https://developers.home-assistant.io/docs/config_entries_options_flow_handler)). |
| `ha_cdp.py diagnostics ENTRY_ID` | GET `/api/diagnostics/config_entry/{id}` | Frontend download path; **not** on the official REST page ([diagnostics](https://developers.home-assistant.io/docs/core/integration/diagnostics)). |
| `ha_cdp.py device-triggers DEVICE_ID` | WS `device_automation/trigger/list` | Generic entity triggers. Not on the public WS reference; frontend uses it. Marstek has no `device_trigger.py`. |
| `ha_cdp.py enable-entity ID` | WS `config/entity_registry/update` `disabled_by: null` | Returns `{entity_entry, reload_delay}`. Wait `reload_delay` (30s) before `wait-state`. Enable CT (EM) or `wifi_rssi` (`Wifi.GetStatus`). Do **not** enable `Bat.GetStatus` entities. |
| `ha_cdp.py upsert-automation ID JSON` | POST `/api/config/automation/config/{id}` | **Not** on the official REST page. Body may include `id`. |
| `ha_cdp.py upsert-script ID JSON` | POST `/api/config/script/config/{id}` | **Not** on the official REST page. Do **not** put `id` in the body — HA 2026.9 returns `400 Message malformed: not a valid option at 'id'`. The id is the URL slug only. |

## Entity services ([select](https://www.home-assistant.io/integrations/select), [number](https://www.home-assistant.io/integrations/number), [switch](https://www.home-assistant.io/integrations/switch))

```bash
python3 scripts/ha_cdp.py service select select_option \
  --data '{"entity_id":"select.venus_c_operating_mode","option":"ai"}'
python3 scripts/ha_cdp.py service number set_value \
  --data '{"entity_id":"number.venus_c_depth_of_discharge","value":80}'
python3 scripts/ha_cdp.py service switch turn_on \
  --data '{"entity_id":"switch.venus_e_3_0_panel_led"}'
python3 scripts/ha_cdp.py service marstek request_data_sync \
  --data '{"device_id":"<device_registry_id>"}'
python3 scripts/ha_cdp.py service marstek set_passive_mode \
  --data '{"device_id":"<device_registry_id>","power":-500,"duration":60}'
```

Official action pages: [select.select_option](https://www.home-assistant.io/actions/select.select_option/), [number.set_value](https://www.home-assistant.io/actions/number.set_value/).

Marstek **select** accepts `auto` / `ai` / `ups` (profile-gated). `manual` and `passive` raise from the select entity; use services or device actions for those.

## Device actions

[Device automation actions](https://developers.home-assistant.io/docs/device_automation_action): Charge battery / Discharge battery / Stop charging/discharging (`charge` / `discharge` / `stop`). Official note: new device automations are not accepted for new integrations; this repo already has them.

```bash
python3 scripts/ha_cdp.py device-actions '<device_id>'
python3 scripts/ha_cdp.py run-script \
  '{"domain":"marstek","type":"discharge","device_id":"<device_id>","metadata":{}}'
```

Build them in the automation editor (Then do → Device) at **`/config/automation/dashboard`** (singular). `POST /api/config/automation/config/{id}` is **not** in the official REST docs.

## Unique IDs after delete / re-add

[Entity registry unique_id](https://developers.home-assistant.io/docs/entity_registry_index#unique-id-requirements): BLE-MAC based IDs stay stable. Re-adding the same device restores `sensor.venus_d_*` instead of creating a second set. From HA 2026.8 a device belongs to one config entry; deleting the entry removes the device ([blog](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)).
