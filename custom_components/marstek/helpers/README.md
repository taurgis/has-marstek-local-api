# Helpers

Small, focused modules used by the Marstek integration. Keep entity and flow files compact by putting reusable descriptions, schemas, and utility functions here.

## Modules

- binary_sensor_descriptions.py: Binary sensor EntityDescription definitions.
- coordinator_helpers.py: Coordinator validation helpers.
- flow_helpers.py: Config flow data helpers.
- flow_schemas.py: Config flow and options schemas.
- number_descriptions.py: Number EntityDescription definitions for SYS DOD.
- select_descriptions.py: Select EntityDescription definitions.
- select_helpers.py: Select mode change retry helpers.
- sensor_descriptions.py: Sensor EntityDescription definitions.
- sensor_stats.py: API stats helpers for sensors.
- service_helpers.py: Service schemas and schedule helpers.
- service_retry.py: Service mode command retry helpers.
- switch_descriptions.py: Switch EntityDescription definitions for SYS BLE/LED.
- sys_write.py: Pause/resume SYS writes and set_result acknowledgement.
