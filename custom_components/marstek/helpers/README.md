# Helpers

Small, focused modules used by the Marstek integration. Keep entity and flow files compact by putting reusable descriptions, schemas, and utility functions here.

## Modules

- binary_sensor_descriptions.py: Binary sensor EntityDescription definitions.
- command_retry.py: Shared retry loop for device writes over UDP.
- coordinator_helpers.py: Coordinator validation helpers.
- device_lookup.py: Device registry lookups and loaded-entry resolution.
- flow_helpers.py: Config flow data helpers.
- flow_schemas.py: Config flow and options schemas.
- number_descriptions.py: Number EntityDescription definitions for SYS DOD.
- polling.py: `polling_paused()` context manager held around device writes.
- ports.py: Open API bind port helpers shared by setup and the flows.
- select_descriptions.py: Select EntityDescription definitions.
- sensor_descriptions.py: Sensor EntityDescription definitions.
- sensor_stats.py: API stats helpers for sensors.
- service_helpers.py: Service schemas and schedule helpers.
- service_retry.py: Service mode command retries with pause and error mapping.
- switch_descriptions.py: Switch EntityDescription definitions for SYS BLE/LED.
- sys_write.py: SYS write transport plus set_result acknowledgement.
- udp_clients.py: Per-bind-port `MarstekUDPClient` pool stored on `hass.data`.
