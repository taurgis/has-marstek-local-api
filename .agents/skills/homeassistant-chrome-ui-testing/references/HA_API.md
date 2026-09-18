# Home Assistant REST / WebSocket used by `ha_cdp.py`

These go through the logged-in page (`hass.callApi` / `hass.callWS`), not a long-lived token file.

Official REST reference: [developers.home-assistant.io/docs/api/rest](https://developers.home-assistant.io/docs/api/rest)

| Command | HA API | Notes |
|---------|--------|--------|
| `ha_cdp.py api GET config/config_entries/entry` | GET `/api/config/config_entries/entry` | List entries. This HA version omits `unique_id` / `data` on the list payload. |
| `ha_cdp.py delete-entry ENTRY_ID` | DELETE `/api/config/config_entries/entry/{entry_id}` | Only delete mechanism ([config entries](https://developers.home-assistant.io/docs/config_entries_index)). No WebSocket delete. Keep the HTTP call open until it returns. |
| `ha_cdp.py api POST config/config_entries/entry/{id}/reload` | POST reload | Unload + setup without wiping the entity registry. |
| `ha_cdp.py service select select_option --data '{...}'` | POST `/api/services/<domain>/<service>` | Same path the UI uses. |
| `ha_cdp.py states --entity ID` | `hass.states` | Live entity object. |
| `ha_cdp.py devices` | WS `config/device_registry/list` | MAC is `identifiers[0][1]` (`marstek`, BLE-MAC). |
| `ha_cdp.py entries` | entries + devices joined | Adds `device_id` / `mac` / `model`. |

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

Marstek **select** accepts `auto` / `ai` / `ups` (profile-gated). `manual` and `passive` raise from the select entity; use services or device actions for those.

## Device actions

[Device automation actions](https://developers.home-assistant.io/docs/device_automation_action): Charge battery / Discharge battery / Stop charging/discharging (`charge` / `discharge` / `stop`). Official note: new device automations are not accepted for new integrations; this repo already has them.

Build them in the automation editor (Then do → Device) or store them in an automation `action` dict with `domain: marstek`, `device_id`, `type`.

`POST /api/config/automation/config/{id}` is **not** in the official REST docs. Prefer the UI at `/config/automation/dashboard` for creating automations during Chrome tests.

## Unique IDs after delete / re-add

[Entity registry unique_id](https://developers.home-assistant.io/docs/entity_registry_index#unique-id-requirements): BLE-MAC based IDs stay stable. Re-adding the same device restores `sensor.venus_d_*` instead of creating a second set. From HA 2026.8 a device belongs to one config entry; deleting the entry removes the device ([blog](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)).
