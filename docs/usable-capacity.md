# Usable capacity

The **Usable capacity** sensor estimates how much energy the pack can actually
store, in watt-hours. **Battery state of health** is that estimate divided by
the nameplate (`ES.GetStatus` `bat_cap`), in percent. Both stay `unknown`
until there is enough evidence. Neither one adds a UDP request.

Home Assistant records them as long-term statistics because usable capacity
uses `device_class: energy_storage` and `state_class: measurement`. That
combination stores a mean, minimum, and maximum, not a sum. The unit is Wh,
which the entity settings can display as kWh. State of health is a percentage
with the same `measurement` statistics. `last_reset` is not set: Core rejects
it for any state class other than `total`.

Sources, Home Assistant Core 2026.9.3 (the devcontainer image):

- [`SensorDeviceClass.ENERGY_STORAGE`](https://github.com/home-assistant/core/blob/2026.9.3/homeassistant/components/sensor/const.py)
  is "stored energy" and explicitly includes "the capacity of a battery".
  Its only allowed state class is `measurement`, and its unit converter is the
  energy converter (`Wh`, `kWh`, and the other `UnitOfEnergy` members).
- [`DEFAULT_STATISTICS`](https://github.com/home-assistant/core/blob/2026.9.3/homeassistant/components/sensor/recorder.py)
  maps `measurement` to mean, min, and max.
- [`SensorStateClass.MEASUREMENT`](https://developers.home-assistant.io/docs/core/entity/sensor/#available-state-classes)
  is "a measurement in present time".
- Setting `last_reset` outside `state_class: total` raises
  (`sensor/__init__.py` in that same Core tag).
- Accepted windows are kept in a [`Store`](https://github.com/home-assistant/core/blob/2026.9.3/homeassistant/helpers/storage.py)
  keyed by config entry (`marstek.usable_capacity.<entry_id>`, version 1).

## What the device actually reports

| Signal | Where | Used? |
| --- | --- | --- |
| `bat_soc` | `ES.GetStatus`, fast poll | Yes. State of charge, usually a whole percent. |
| Battery power | `bat_power` when the firmware sends it, otherwise `pv_power - ongrid_power - offgrid_power` | Yes. This is the AC-side wattage the integration already publishes (positive = discharging). |
| Operating mode | `ES.GetMode` | Yes, only to discard a window that changes mode. |
| `bat_cap` | `ES.GetStatus` | Nameplate for state of health, on families where it is the pack size. Not used as the estimate. |
| `bat_capacity`, `rated_capacity`, temperature, charge flags | `Bat.GetStatus` | No. The call stays off unless a diagnostic entity is enabled, and on some firmwares it is suspected of resetting the device. Those watt-hours are the BMS's own remaining and rated figures, not a measured fade. |
| Lifetime grid, PV, and load energy | `ES.GetStatus` | No. They count the house and the inverter, not coulombs into the pack. |
| Cycle count, cell voltage, BMS state of health | — | Not in the Open API. Not invented. |

Venus E mini reports remaining energy in `bat_cap`, so the integration does not
treat it as a nameplate. Usable capacity is still estimated. State of health
is not created.

## Formula

A window is one direction (charge or discharge), one mode, and no gap longer
than 180 seconds. Power below 150 W ends the window. When state of charge has
moved at least 20 points and the window has at least four samples:

```text
DC watt-hours = AC watt-hours / 0.93     while discharging
DC watt-hours = AC watt-hours * 0.93     while charging
capacity = DC watt-hours / (state-of-charge change / 100)
```

AC watt-hours are the trapezoidal integral of the reported battery power
between polls. The 0.93 one-way factor is the same model the mock pack uses.
It is not a measurement from Marstek hardware. It exists so a charge window
and a discharge window describe the same DC pack: without it they disagree by
about the square of the efficiency, and a healthy nameplate would look faded.

The sensor publishes the median of the last 21 accepted windows, and only
after three have been accepted. A window outside 300–30,000 Wh, or outside
15–135% of the nameplate when one is known, is dropped. The median is what
ignores a single bad stretch; there is no second outlier rule that would
freeze the value if the pack really did fade.

State of health is `estimate / bat_cap * 100`, rounded to 0.1. It can sit a
little above 100% when the efficiency assumption is low.

Attributes (not stored in long-term statistics, only the state is): method,
windows accepted, confidence (`collecting`, `low`, `medium`, `high`), the
20-point minimum, the 0.93 factor, nameplate, and the last window's direction,
capacity, and time. Confidence counts windows. It is not a calibrated error bar.

## Limits

- The estimate is only as good as the device's state of charge. If the BMS
  coulomb-counts that percentage against a fixed nameplate, this calculation
  recovers that scale. Chemical fade shows up when the reported percentage
  moves differently from the energy that actually left the pack, which is
  what a voltage recalibration or a drifted coulomb counter does. It will not
  reveal fade the BMS is hiding by stretching its own percentage.
- State of charge is coarse. A 20-point window closed on whole percents can
  be about one point early or late, which is about 5% on that window. The
  median of several windows is tighter. This is not a lab measurement.
- The absolute watt-hours move if the real one-way efficiency is not 0.93.
  The long-term slope is the degradation signal either way. Charge windows
  and discharge windows drifting apart is the sign that the factor is off.
- Idle, short cycles, mode changes, and gaps longer than about three minutes
  are ignored. A battery that only trickles may take days to publish a number.
- No extra polling is added. Disabling the two entities in the registry hides
  them; the arithmetic still runs on data the coordinator already fetched,
  which is a few numbers per poll and no UDP.

The mock can run a faded pack with `--capacity-wh` (physics) while `bat_cap`
stays at the family nameplate. `--soc-cycle` holds Passive mode between 30%
and 92%. `--time-scale` speeds the physics and the reported inverter watts by
the same factor so a wall-clock integrator still sees the true capacity. The
default scale is 1.
