# Energy dashboard

The integration gives you lifetime energy counters for the battery, and for solar on Venus A and Venus D. This page shows where each one belongs on Home Assistant's [Energy dashboard](https://www.home-assistant.io/docs/energy/).

## Add Marstek to the Energy dashboard

Open **Settings** > **Dashboards** > **Energy**, then:

1. **Home battery storage:** add **Total grid input energy** as *energy going in to the battery* and **Total grid output energy** as *energy coming out of the battery*. Despite their names, these count energy through the battery's own AC connection, not your house's grid connection.
2. **Electricity grid:** use your house meter, for example a P1 or utility-meter integration. If your Marstek CT meter reports them, **Meter input energy** and **Meter output energy** work too. Compare them with your utility meter once to confirm which is import and which is export.
3. **Solar panels (Venus A and Venus D):** see [Venus A and Venus D: solar and the battery row](#venus-a-and-venus-d-solar-and-the-battery-row) first.
4. Leave **Total load energy** off this dashboard. What it measures differs between models and firmware.

> [!WARNING] Earlier versions of this page were wrong
> They said to add Total grid input and output energy as grid consumption and return to the grid. That makes every charge count as grid import and every discharge as grid export. If you set it up that way, move the two sensors to **Home battery storage** and pick a house meter for the grid.

Screenshots of the battery's own **Energy** tab, which is a different view of the same battery counters, are in the [visual tour](screenshots.md#energy-dashboard-and-repairs).

### Venus A and Venus D: solar and the battery row

On Venus A and Venus D, solar power goes in through the battery and out through the same AC connection. So **Total grid output energy** includes solar that went straight to the house. If you add both **Total solar energy** under **Solar panels** and the battery counters under **Home battery storage**, that solar is counted twice. Pick one:

- **Solar matters most:** add **Total solar energy** under **Solar panels** and leave the battery row out.
- **The battery matters most:** add the battery counters and leave solar out.

## Why the names say "grid"

Marstek's Open API calls the battery's AC connection its grid side, so its counters are named `total_grid_input_energy` and `total_grid_output_energy`. Tests against Marstek's firmware in an emulator, with no CT meter attached, show them counting the battery's own charge and discharge ([firmware analysis](../tools/firmware/ANALYSIS.md)). The [Battery health](usable-capacity.md) card labels the same counters **Charged from AC** and **Delivered to AC**. They include inverter losses, so output is lower than input.

## Technical details

To be accepted by the Energy dashboard, a sensor must have `device_class: energy`, `state_class: total` or `total_increasing`, and a unit such as Wh or kWh. All the energy sensors below meet that.

## Why Wh instead of kWh

The integration stores energy totals in Wh, a Home Assistant-supported energy unit. Open API energy fields are **not** all Wh on the wire. Each field is normalized individually:

| Field | Typical wire unit | Home Assistant | When the scale applies |
| --- | --- | --- | --- |
| `total_pv_energy` | 0.01 kWh on Venus A firmware 1487 (app 148.7) and 149, Venus D 149, and known families at 150+; Wh on older firmware | Wh (`raw × 10` when 0.01 kWh) | Venus A at 1487+ ([#35](https://github.com/taurgis/has-marstek-local-api/issues/35)); Venus D at 149+; known families at 150+. Venus A 148 / `148.3` stays Wh. |
| `total_grid_input_energy` / `total_grid_output_energy` | Wh | Wh (never scaled with PV) | Always |
| `total_load_energy` | Wh | Wh | Always |
| `EM.GetStatus` `input_energy` / `output_energy` | 0.1 Wh on Rev 3.1 | Wh (`raw × 0.1`) | Known family at firmware 150+; sensors omitted when the fields are absent |

`0.01 kWh` is a wire encoding, not a custom Home Assistant unit. Energy dashboard eligibility still requires `device_class: energy`, `state_class: total` or `total_increasing`, and a supported unit such as Wh or kWh ([Energy FAQ](https://www.home-assistant.io/docs/energy/faq/)).

## Energy totals mapping

| Dashboard input | Entity name | Entity key | Unit |
| --- | --- | --- | --- |
| Home battery storage: energy in | Total grid input energy | total_grid_input_energy | Wh |
| Home battery storage: energy out | Total grid output energy | total_grid_output_energy | Wh |
| Solar production (Venus A, D) | Total solar energy | total_pv_energy | Wh |
| Grid import or export (CT meter, if reported) | Meter input energy | em_input_energy | Wh |
| Grid import or export (CT meter, if reported) | Meter output energy | em_output_energy | Wh |

> [!NOTE]
> Some Marstek firmware versions can keep the raw grid input/output energy
> counters fixed even while the battery's AC power is still changing. When that happens, the
> integration keeps these totals monotonic by deriving the missing growth from
> `on_grid_power` between updates and restoring the corrected total after Home
> Assistant restarts. Impossible spikes (above 1 GWh, or more than 5 MWh in one
> poll) are ignored on every lifetime energy total so they cannot freeze the
> Energy dashboard at a bogus value.

> [!NOTE]
> `total_load_energy` is reported by the Marstek Open API as load or
> off-grid energy, and its semantics vary by device and firmware. The integration
> does not recommend it as the Energy dashboard's home consumption source.

Meter input/output energy (`em_input_energy` / `em_output_energy`) come from
the existing fast-tier `EM.GetStatus` payload when the device reports those
lifetime totals (Open API Rev 3.1). The wire unit is 0.1 Wh; Home Assistant
exposes watt-hours. They are measured at the CT meter, so they describe your
house's grid connection, unlike the ES counters above, which describe the
battery. The sensors are omitted when the fields are absent.

## Solar energy unit correction

On Venus A firmware 149, `ES.GetStatus.total_pv_energy` is encoded as **0.01 kWh**
on the wire (issue [#35](https://github.com/taurgis/has-marstek-local-api/issues/35)).
That is the solar *energy* counter, not PV1 *power* ([#57](https://github.com/taurgis/has-marstek-local-api/issues/57)).
Known families at firmware 150+ use that solar unit too. Firmware **148 or older**
keeps the value in Wh. PV1 stays deciwatts through 150.9 and is not used here.

The integration converts the 0.01 kWh wire unit to **Wh** (`raw × 10`) so a
device value of `25742` becomes `257420 Wh` (`257.42 kWh`). Grid and load
totals in the same payload stay in Wh and are not scaled.

The Total solar energy entity keeps its existing BLE-MAC unique ID and native
unit `Wh`. After this correction, previously recorded solar states remain about
10× too low. The first corrected state is a large **upward** jump, not a meter
reset: Home Assistant treats a **decrease** in a `total_increasing` sensor as a
new meter cycle, while an increase is added to long-term statistics and can
look like a one-time production spike.

Do not treat that jump as a reset. Review and adjust the transition in
**Developer tools** > **Statistics** if the Energy dashboard shows inflated
production for that interval. See Home Assistant's
[Statistics tab](https://www.home-assistant.io/docs/tools/dev-tools/#statistics-tab)
and the [`total_increasing` state class](https://contributors.home-assistant.io/core/entities/sensor/#state-classes).

## Optional power sensors
These are not required for the energy dashboard. Use them for real time cards and sanity checks. These sensors use `state_class: measurement`.

| Sensor use | Entity name | Entity key | Unit |
| --- | --- | --- | --- |
| Solar power | PV power | pv_power | W |
| Battery AC power | On-grid power | ongrid_power | W |
| Grid power at the CT meter | Total power | em_total_power | W |
| Battery power | Battery power | battery_power | W |

## Unit conversion
Home Assistant accepts both Wh and kWh for energy sensors, so conversion is optional. If you prefer kWh, convert by dividing by 1000.

### Template examples (Wh to kWh)
Replace `<battery>` with your battery's entity ID prefix, which comes from its device name (for example `venus_e_3_0`). These examples create kWh sensors that can be selected in the Energy dashboard.

```yaml
template:
  - sensor:
      - name: "Marstek total solar energy kwh"
        unique_id: marstek_total_pv_energy_kwh
        state: "{{ (states('sensor.<battery>_total_solar_energy') | float(0)) / 1000 }}"
        availability: "{{ is_number(states('sensor.<battery>_total_solar_energy')) }}"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
      - name: "Marstek total grid input energy kwh"
        unique_id: marstek_total_grid_input_energy_kwh
        state: "{{ (states('sensor.<battery>_total_grid_input_energy') | float(0)) / 1000 }}"
        availability: "{{ is_number(states('sensor.<battery>_total_grid_input_energy')) }}"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
      - name: "Marstek total grid output energy kwh"
        unique_id: marstek_total_grid_output_energy_kwh
        state: "{{ (states('sensor.<battery>_total_grid_output_energy') | float(0)) / 1000 }}"
        availability: "{{ is_number(states('sensor.<battery>_total_grid_output_energy')) }}"
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
```

## Entity ID examples
Home Assistant builds entity IDs from the device name and the sensor name. Replace `<battery>` with your battery's prefix, and check the exact ID on the entity's settings.

### Energy totals
- `sensor.<battery>_total_solar_energy`
- `sensor.<battery>_total_grid_input_energy`
- `sensor.<battery>_total_grid_output_energy`
- `sensor.<battery>_meter_input_energy` (when reported)
- `sensor.<battery>_meter_output_energy` (when reported)

### Power sensors
- `sensor.<battery>_pv_power`
- `sensor.<battery>_on_grid_power`
- `sensor.<battery>_total_power`
- `sensor.<battery>_battery_power`

### Other device-reported totals
- `sensor.<battery>_total_load_energy`

## References
- [Home Assistant Energy](https://www.home-assistant.io/docs/energy/)
- [Energy FAQ](https://www.home-assistant.io/docs/energy/faq/)
- [Marstek Open API](marstek_device_openapi.MD)
- [Entities](entities.md)
