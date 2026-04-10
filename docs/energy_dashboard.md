# Energy Dashboard

## Overview
This page maps Marstek entities to Home Assistant energy dashboard inputs and provides copy-paste examples. Eligibility note: energy sensors must report `device_class: energy`, use `state_class: total` or `state_class: total_increasing`, and report energy units Wh or kWh to be accepted by the energy dashboard.

Note: Battery energy totals are not provided by this integration.

## Why Wh instead of kWh
The Marstek Open API reports total energy values in Wh. The integration keeps these values 1:1 with the device to avoid rounding and preserve the raw totals.

## Energy totals mapping
| Dashboard input | Entity name | Entity key | Unit |
| --- | --- | --- | --- |
| Solar production (total) | Total solar energy | total_pv_energy | Wh |
| Grid consumption (total) | Total grid input energy | total_grid_input_energy | Wh |
| Grid export (total) | Total grid output energy | total_grid_output_energy | Wh |

Note: Some Marstek firmware versions can keep the raw grid input/output energy
counters fixed even while grid power is still changing. When that happens, the
integration keeps the grid totals monotonic by deriving the missing growth from
`on_grid_power` between updates and restoring the corrected total after Home
Assistant restarts.

Note: `total_load_energy` is reported by the Marstek Open API as load or
off-grid energy, and its semantics vary by device and firmware. The integration
does not recommend it as the Energy Dashboard's Home consumption source.

## Optional power sensors
These are not required for the energy dashboard. Use them for real time cards and sanity checks. These sensors use `state_class: measurement`.

| Sensor use | Entity name | Entity key | Unit |
| --- | --- | --- | --- |
| Solar power | PV power | pv_power | W |
| Grid power | On-grid power | ongrid_power | W |
| Total load power | Total power | em_total_power | W |
| Battery power | Battery power | battery_power | W |

## Unit conversion
Home Assistant accepts both Wh and kWh for energy sensors, so conversion is optional. If you prefer kWh, convert by dividing by 1000.

### Template examples (Wh to kWh)
Replace the source entity IDs to match your setup. These examples create kWh sensors that can be selected in the Energy Dashboard.

```yaml
template:
  - sensor:
      - name: "Marstek total solar energy kwh"
        unique_id: marstek_total_pv_energy_kwh
        state: "{{ (states('sensor.total_pv_energy') | float(0)) / 1000 }}"
        availability: "{{ is_number(states('sensor.total_pv_energy')) }}"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
      - name: "Marstek total grid input energy kwh"
        unique_id: marstek_total_grid_input_energy_kwh
        state: "{{ (states('sensor.total_grid_input_energy') | float(0)) / 1000 }}"
        availability: "{{ is_number(states('sensor.total_grid_input_energy')) }}"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
      - name: "Marstek total grid output energy kwh"
        unique_id: marstek_total_grid_output_energy_kwh
        state: "{{ (states('sensor.total_grid_output_energy') | float(0)) / 1000 }}"
        availability: "{{ is_number(states('sensor.total_grid_output_energy')) }}"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
```

## Entity ID examples
Use these as copy-paste starting points, then replace the entity IDs with the actual ones from your system.

### Energy totals
- `sensor.total_pv_energy`
- `sensor.total_grid_input_energy`
- `sensor.total_grid_output_energy`

### Power sensors
- `sensor.pv_power`
- `sensor.ongrid_power`
- `sensor.em_total_power`
- `sensor.battery_power`

### Other device-reported totals
- `sensor.total_load_energy`

## References
- https://www.home-assistant.io/docs/energy/
- https://www.home-assistant.io/docs/energy/faq/
- [Marstek Open API](marstek_device_openapi.MD)
- [Entities](entities.md)
