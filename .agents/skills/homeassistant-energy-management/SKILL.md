---
name: homeassistant-energy-management
description: Guidance for using correct energy and power metrics in Home Assistant energy features
---

# Home Assistant Energy Management

This skill helps you choose and expose the correct metrics for Home Assistant energy features (Energy Dashboard, grid, and individual devices) to avoid incorrect graphs or totals.

## When to Use

- Adding or auditing energy-related sensors in an integration.
- Mapping device data to Home Assistant energy concepts (grid import/export, production, consumption).
- Fixing Energy Dashboard configuration or incorrect totals.

## Core Rules

- **Power vs Energy**: Power is instantaneous (W), energy is accumulated (kWh).
- **Energy sensors** must be monotonically increasing totals (never reset except on rollover).
- Use **state_class** `total_increasing` and **device_class** `energy` for kWh totals.
- Use **state_class** `measurement` and **device_class** `power` for W values.
- Avoid mixing sign conventions for energy; separate import and export sensors.

## Metric Mapping

| Concept | Correct Metric | Unit | Notes |
| --- | --- | --- | --- |
| Grid import | Total energy imported | kWh | Separate from export; total_increasing |
| Grid export | Total energy exported | kWh | Separate from import; total_increasing |
| Solar/production | Total energy produced | kWh | Must be total_increasing |
| Home consumption | Total energy consumed | kWh | Prefer derived if not provided |
| Instant grid power | Power at grid | W | Positive/negative must be consistent |

## Grid Data Guidance

- Provide **two energy totals** for grid: import and export.
- If the device reports net energy, split into import/export totals before exposing sensors.
- Keep grid power sign conventions consistent with your integration (document it).

## Individual Devices

- Each device should expose **energy consumed** (kWh) as a total_increasing sensor.
- If only power is available, use a platform-level integration sensor to integrate power into energy.
- Avoid using daily reset counters as energy totals unless clearly marked and not used in Energy Dashboard totals.

## Implementation Checklist

- Use `device_class=energy`, `state_class=total_increasing`, `native_unit_of_measurement=kWh`.
- For power, use `device_class=power`, `state_class=measurement`, `native_unit_of_measurement=W`.
- Ensure totals never decrease; filter resets or rollovers.
- Add documentation to explain data sources and sign conventions.
- Validate sensor types in the Energy Dashboard configuration.

## Reference

- https://www.home-assistant.io/docs/energy/faq/
- https://www.home-assistant.io/docs/energy/electricity-grid/
- https://www.home-assistant.io/docs/energy/individual-devices/
