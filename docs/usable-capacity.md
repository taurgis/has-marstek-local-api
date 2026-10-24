# Usable capacity

The **Usable capacity** sensor estimates how much energy the pack can store
per 100 points of reported state of charge, in watt-hours. It is not the
energy inside the SYS depth-of-discharge setting, and it is not the figure
[Marstek Capacity Monitor](https://github.com/satscan1/marstek-capacity-monitor)
publishes after scaling a full discharge to that band. **Battery state of
health** is the estimate divided by the nameplate (`ES.GetStatus` `bat_cap`),
in percent. Both stay `unknown` until there is enough evidence. Neither one
adds a UDP request.

`battery_power` is an AC-side wattage. On Venus E 3.0, Venus A, and Venus D
the Open API usually omits `bat_power`, and the entity is
`ongrid_power + offgrid_power - pv_power` (positive discharges). Do not feed
that entity to a tool that wants DC battery power. Marstek Capacity Monitor
refuses AC power for this reason; using this entity there counts inverter
losses as pack energy.

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

A window is one direction (charge or discharge), one mode, and no long gap.
At the default 30 second poll the gap limit is 180 seconds. The options flow
allows a fast poll up to 300 seconds, and Home Assistant starts that wait
after the poll has finished, so a slow setting would step past 180 seconds
on every successful poll. The limit then becomes the configured interval
plus 150 seconds, which is how long one poll can spend on its own requests
(six serial reads at the maximum 30 second delay). A missed poll is another
full interval and still ends the window. Raising the constant to 300 seconds
instead would let a 30 second poll stitch across a five-minute hole.
Power below 150 W ends the window. The window starts on the first poll
after the reported state of charge changes, not on whatever poll opened it.
It is accepted on the poll that completes a 20-point move, which is also the
first poll after a change, so both ends sit the same distance past a step.
When state of charge has moved at least 20 points and the window has at
least four samples:

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

On Venus A and Venus D that reported battery power is an energy balance,
`pv_power - ongrid_power - offgrid_power` in the API sign. `pv_power` is PV
charging power from the MPPT, on the battery side of the inverter
([Open API note](marstek_device_openapi.MD), sections 3.5 and 3.6.1). It does
not take the 0.93 factor. Only the AC port does:

```text
ac = battery_power + pv_power          # derived reading, HA sign
dc = ac / 0.93 - pv_power              when the AC port is exporting or idle
dc = ac * 0.93 - pv_power              when the AC port is importing
```

With `pv_power` at 0 this is the formula above. Venus E 3.0 omits `bat_power`
and reports `pv_power` 0, so its windows do not move. A device that actually
sends `bat_power` stays on the whole-reading formula; that field is not
split, because the Open API does not say whether it is DC. A whole window of
PV-only charging used to be about 7% low (`× 0.93`). The share of the
battery-power integral that PV covered is stored as `pv_share` (0 to 1).

A state-of-charge step of two points or more ends the window when the watts
of that poll, the nameplate, and one extra point of slack cannot explain it.
A single whole-percent step never does. The usual cause is a BMS
recalibration, often near 100%. Without a nameplate the check uses a 300 Wh
pack, so only a jump that small pack could not make is dropped. Venus E mini
has no nameplate in this field.

The sensor publishes a number only when one direction has three windows. It
keeps the newest 21 in each direction. A long run of discharge windows cannot
evict the charge windows. Two charge windows plus two discharge windows stay
unknown: the directions are not pooled into one median. With three windows in
only one direction, the number is that direction's median and it still
depends on the 0.93 factor. Once charge and discharge each have three
windows, the number is the geometric mean of the two medians. If the real
charge and discharge efficiencies are equal, that product cancels the factor,
including the case where 0.93 is wrong. What remains is the square root of
`η_discharge / η_charge`, plus any steady auxiliary draw the AC port has in
both directions. A window outside 300–30,000 Wh, or outside 15–135% of the
nameplate when one is known, is dropped. The median is what ignores a single
bad stretch; there is no second outlier rule that would freeze the value if
the pack really did fade.

`spread_wh` is the range of the windows that entered the published number.
Charge windows are not part of it while only the discharge median is
published. It is an observed spread, not a confidence interval. The choice of
method, and the error margin that goes with it, is written up in
[Capacity estimation methods](capacity-estimation-methods.md).

State of health is `estimate / bat_cap * 100`, rounded to 0.1. It can sit a
little above 100% when the efficiency assumption is low.

Attributes (not stored in long-term statistics, only the state is): method,
windows accepted, confidence (`collecting`, `low`, `medium`, `high`), the
20-point minimum, the 0.93 factor, which treatment produced the number
(`discharge_median`, `charge_median`, `charge_discharge_geometric_mean`, or
`assumed_one_way` while still collecting), nameplate, and the last window's
direction, capacity, and time. Once a direction has a window, its median is
`discharge_median_wh` or `charge_median_wh`. When both exist,
`implied_round_trip_efficiency` is `0.93 × 0.93 × discharge_median /
charge_median`. That is the round-trip efficiency the two medians imply when
the readings are AC and the assumed factor is the only thing that differs
between them. It is a diagnostic, not a measured efficiency, and a PV-heavy
window moves it. Confidence counts windows. It is not a calibrated error bar.

Home Assistant writes a state row when attributes change, so attributes that
move on every poll do not belong on a sensor
([sensor attributes](https://developers.home-assistant.io/docs/core/entity/sensor/#attributes)).
These change when a window is accepted.

Each accepted window is stored with its start and end state of charge,
average reported power, duration, mode, and `pv_share`. A store written
before those fields existed still loads; the missing fields stay absent.
The storage version is unchanged.

## Limits

- The estimate is only as good as the device's state of charge. If the BMS
  coulomb-counts that percentage against a fixed nameplate, this calculation
  recovers that scale. Chemical fade shows up when the reported percentage
  moves differently from the energy that actually left the pack, which is
  what a voltage recalibration or a drifted coulomb counter does. It will not
  reveal fade the BMS is hiding by stretching its own percentage.
- State of charge is coarse: whole percents. Starting on a step keeps a
  20-point window from being a point off, which was about 5% on that window.
  What remains is how far state of charge moves in one poll: a quarter point
  at 1 kW on a 5 kWh pack and a 30 second poll, about 1% on one window. The
  cost is that a run must move 21 points to yield a 20-point window. This is
  not a lab measurement.
- With only discharge windows, or only charge windows, the absolute
  watt-hours move if the real one-way efficiency is not 0.93. The long-term
  slope is still the degradation signal. Once both directions have three
  windows, the geometric mean removes a factor that is shared by both
  directions. A remaining gap between the two medians means the two
  efficiencies are not equal, and that gap is the residual
  `sqrt(η_discharge / η_charge)`. A constant self-consumption on the AC port
  biases both directions the same way and does not cancel.
- The number is watt-hours per 100 state-of-charge points. SYS depth of
  discharge (30–88, default 88) is not applied. Rev 3.1 has `DOD.SET` and no
  read ([Open API note](marstek_device_openapi.MD), section 3.8), and the
  number entity is the last successful write, not a measurement. A
  within-DoD watt-hour attribute would publish that guess, so it is not
  there. Marstek Capacity Monitor's figure is the energy of a full discharge
  scaled to its depth-of-discharge band, which is a smaller number than
  watt-hours per 100 points.
- A poll whose `ES.GetStatus` did not answer is not a sample. `ES.GetMode`
  can still bring a new state of charge while battery power is the previous
  poll's watts. That pair is a hole: the open window is dropped, and the
  next fresh poll does not integrate across it. The power sensor still
  shows the carried watts.
- Idle, short cycles, mode changes, and gaps longer than the limit above
  are ignored. A battery that only trickles may take days to publish a number.
- No extra polling is added. Disabling the two entities in the registry hides
  them; the arithmetic still runs on data the coordinator already fetched,
  which is a few numbers per poll and no UDP.

## Lovelace card

The integration serves `marstek-capacity-card` itself. After upgrading, reload
the frontend (or restart Home Assistant) and use **Add card → Marstek
capacity**. No Lovelace resource is required. The card reads the usable
capacity entity: state of health, the two medians, round-trip efficiency,
stored windows, and the open window while state of charge is still moving
toward 20 points.

**Marstek capacity overview** compares every battery. Options → **Capacity
dashboard** / **Capaciteitsdashboard** adds a sidebar dashboard
(`marstek-capacity`, strategy `custom:marstek-capacity`) titled **Battery
Capacity** or **Batterijcapaciteit**. It is off until you enable it. Turning
it off hides the sidebar entry and leaves the dashboard in place.

The mock can run a faded pack with `--capacity-wh` (physics) while `bat_cap`
stays at the family nameplate. `--soc-cycle` holds Passive mode between 30%
and 92%. `--time-scale` speeds the physics and the reported inverter watts by
the same factor so a wall-clock integrator still sees the true capacity. The
default scale is 1. On Venus A and Venus D the mock credits MPPT watts to the
pack directly and runs only `ongrid_power` through the one-way factor, the
same split as the estimator, so a PV-only window lands on the true pack
watt-hours. Venus E 3.0 and Venus C have no device PV, so that split does
not move them.
