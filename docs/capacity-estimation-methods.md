# How to estimate usable capacity from field data

This note is the evidence behind the usable-capacity sensor. It records what
the published methods need, what other products actually do, and what the
Marstek Open API can support. The sensor itself is described in
[Usable capacity](usable-capacity.md).

Home Assistant's sensor classes are cited there from Core 2026.9.3. There is
no Marstek document online for the local API; the field list is
[the Open API note in this repo](marstek_device_openapi.MD).

## What "usable capacity" means here

Plett defines cell total capacity as the charge that can be extracted from a
full cell, and treats it as the quantity behind state of health. The
estimator in his 2011 paper is

```text
y = Q * x
```

where `x` is the change in state of charge between two times and `y` is the
charge moved in that interval. `Q` is the capacity. See Plett, "Recursive
approximate weighted total least squares estimation of battery cell total
capacity", Journal of Power Sources 196 (2011) 2319–2331
([author PDF](http://mocha-java.uccs.edu/dossier/RESEARCH/2011jps-.pdf),
[ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S037877531001654X)).

That is the same relation this integration uses, in watt-hours instead of
ampere-hours, because the Open API gives power and watt-hour nameplate rather
than a DC current sensor.

## Methods, and whether we can run them

### Coulomb counting between state-of-charge points

Integrate current (or, for us, power) between two state-of-charge readings
and divide by the state-of-charge fraction. This is the field form of
`Q = y / x`. It needs no cell model. It does need the state of charge at the
two ends to mean the same scale, and it needs the integral not to have large
holes.

Plett's warning on this method is the one that matters here. Ordinary least
squares treats only `y` as noisy. State of charge is also noisy, so the fit
is biased, and total least squares is the regression that allows noise on
both axes. He also warns that the state-of-charge input must not itself be a
coulomb count that uses the capacity being estimated: that loop does not
converge. A voltage-based or Kalman state of charge that was computed with a
fixed nameplate is the input he wants. On LFP he reports that even a
sigma-point Kalman filter, which is much more than an open-circuit-voltage
lookup, still has typical state-of-charge errors of about 3–5 percent,
because the open-circuit-voltage curve is flat
([same paper](http://mocha-java.uccs.edu/dossier/RESEARCH/2011jps-.pdf)).

We implement the coulomb-counting ratio, not weighted total least squares.
The Open API does not give the state-of-charge error variance or the power
error variance that the weighting needs, and a recursive update would let one
bad window pull the value. A median of complete windows is the robust version
we can justify from the samples we have. The spread of those windows is
reported as `spread_wh`. That is the range of the accepted samples, not a
confidence interval. A confidence interval would pretend we know the noise
variances Plett's estimator requires.

### Round-trip efficiency

The integral we have is AC power at the inverter port, not DC current at the
cells. Charge and discharge therefore need an efficiency. If the same one-way
efficiency `η` is wrong by the same amount in both directions, the discharge
estimate scales by `η_true / η_assumed` and the charge estimate scales by
`η_assumed / η_true`. The geometric mean of the two is the capacity, and the
assumed factor drops out. That is ordinary algebra on Plett's `y = Q x` once
each `y` has been converted from AC watt-hours with the same factor. It is
why the sensor switches to the geometric mean of the charge median and the
discharge median once each direction has three windows.

Until both directions exist, the number still uses the fixed one-way factor
0.93 from the mock pack model. That number is not a measurement on Marstek
hardware.

Victron's battery monitor is the clearest manufacturer statement of the same
physics, on ampere-hours rather than watt-hours. The SmartShunt manual says
the monitor integrates current, that effective capacity depends on discharge
rate and Peukert's exponent, and that more ampere-hours must be charged than
can later be discharged. For LiFePO4 it tells the installer to set the
Peukert exponent to 1.05 unless the supplier says otherwise, and the charge
efficiency factor to 99 percent. A Peukert exponent of 1.00 turns the
rate correction off. Charge efficiency of 95 percent means 10 Ah must be
supplied to store 9.5 Ah
([SmartShunt manual, PDF](https://www.victronenergy.com/upload/documents/SmartShunt/9172-Manual_BMV_and_SmartShunt-pdf-en.pdf),
[Peukert chapter](https://www.victronenergy.com/media/pg/SmartShunt/en/battery-capacity-and-peukert-exponent.html),
[settings chapter](https://www.victronenergy.com/media/pg/SmartShunt_IP65/en/all-features-and-settings.html)).

Those 99 percent and 1.05 figures are for a DC shunt on lithium cells. They
are not the AC round trip of a Marstek inverter plus pack, so they are not
copied into the estimator. They are the reason a C-rate correction is not
invented here either: at the currents these packs actually use, an exponent
of 1.05 is a small effect, and we do not have the cell's rated-hour current
to apply Peukert's formula honestly.

### Incremental capacity and voltage methods

Incremental capacity analysis plots `dQ/dV` against voltage and reads fade
from the shift of those peaks. It needs a slow, high-resolution voltage sweep
and the charge moved along it. Marstek's fast poll has no cell voltage and no
pack voltage. Open-circuit-voltage state of charge has the same gap, and on
LFP the curve is flat enough that Plett treats voltage-only state of charge
as a poor input. These methods are not implementable on this API.

### Kalman filters and recursive least squares

A Kalman filter of capacity, or recursive least squares on `y = Q x`, is the
online form of the same fit. Plett's preferred state of charge is itself a
sigma-point Kalman filter on a cell model that includes voltage
([2002 EVS paper, author PDF](http://mocha-java.uccs.edu/dossier/RESEARCH/2002evs19a-.pdf)).
A later coulomb-counting paper updates capacity with recursive least squares
between two state-of-charge points, and it resets those points from
open-circuit voltage after the current has been zero for 25 minutes
([Energies 2020, 13(7), 1811](https://www.mdpi.com/1996-1073/13/7/1811)).
Without a voltage measurement there is nothing for either filter to correct
against, so the state of charge we have stays the BMS percentage.
Recursive least squares on our windows would also be less robust than a
median: one glitched window has a permanent vote until it is forgotten.
The sensor keeps a 21-window median instead.

### Temperature

Usable energy falls when the pack is cold. The fast poll does not carry cell
temperature. `Bat.GetStatus` does (`bat_temp`), and this integration leaves
that call off unless a diagnostic entity is enabled, because it is suspected
of resetting some devices
([Open API note](marstek_device_openapi.MD)). Applying a temperature
coefficient we have not measured on these cells would invent a correction.
Cold sessions therefore read low. That shows up as spread between windows,
not as a silent adjustment.

### Cycle counting

A cycle count is not a capacity. Sonnen exposes `cyclecount` from its own
battery endpoint; EVCC and the sonnen JSON API expose user state of charge
and, on `/api/v2/battery`, full charge capacity in ampere-hours as the BMS
reports it
([sonnen status fields as published by community API notes](https://jlunz.github.io/homeassistant/),
[EVCC sonnen template](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/sonnenbatterie.yaml)).
The Marstek Open API has no cycle counter. Counting only the windows that
moved at least 20 state-of-charge points would miss every smaller cycle, so
the sensor does not publish a cycle count.

### Outliers

A window outside 300–30,000 Wh, or outside 15–135 percent of the nameplate
when one is known, is dropped. Inside that band the median is the outlier
rule. A sigma clip around the current estimate would also clip a real step
down in capacity, which is the trend the sensor exists to show. The observed
range of the windows that remain is `spread_wh`.

## What other products do

They almost all display a capacity the battery or the installer already
stated. They do not estimate fade from power and state of charge.

| Product | What it publishes | Source |
| --- | --- | --- |
| Victron SmartShunt | State of charge from a shunt. Battery capacity is a setting in Ah, not a measured fade. | [SmartShunt manual](https://www.victronenergy.com/upload/documents/SmartShunt/9172-Manual_BMV_and_SmartShunt-pdf-en.pdf) |
| Tesla Powerwall | `battery_nominal_full_pack_energy`: available energy when full at 25 °C, "affected by temperature, degradation, etc." Tesla's own BMS figure. The warranty tracks that against 13.5 kWh (70 percent at 10 years for the common US terms). | [Tesla Energy Library, battery signals](https://energylibrary.tesla.com/docs/Public/More/Powerhub/Residential/UserManual/en-us/GUID-9B10A167-8CCA-44FC-9993-572D89191989.html), [Powerwall warranty (US)](https://digitalassets.tesla.com/tesla-contents/image/upload/Powerwall_Warranty_US-EN.pdf) |
| Sonnen | User state of charge (`USOC`) from the local JSON API. Full charge capacity (`fullchargecapacity`, Ah) and `cyclecount` come from the BMS on `/api/v2/battery`. EVCC's template reads `USOC` and takes capacity as a configuration value in kWh. | [sonnen JSON API fields as mapped by the Home Assistant integration](https://github.com/mrpointblue/sonnenBatterie-Integration/blob/main/README.md), [EVCC template](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/sonnenbatterie.yaml), [EVCC capacity parameter](https://github.com/evcc-io/evcc/blob/master/util/templates/defaults.yaml) |
| Home Assistant Powerwall | The core integration's battery capacity sensor reads the gateway's own full-pack energy. That is the same BMS figure as `battery_nominal_full_pack_energy`, not a coulomb count done in Home Assistant. | [Powerwall integration](https://www.home-assistant.io/integrations/powerwall/), [Tesla Energy Library](https://energylibrary.tesla.com/docs/Public/More/Powerhub/Residential/UserManual/en-us/GUID-9B10A167-8CCA-44FC-9993-572D89191989.html) |
| TeslaFi | Battery degradation report is rated range at 100 percent, or range divided by the ending state of charge when the charge stopped short of 100 percent. TeslaFi's own note is that state of charge is a whole number, so an incomplete charge does not know whether it ended at 79.1 or 79.9 percent, and those charges are left out of the report. | [TeslaFi support: how degradation is calculated](https://support.teslafi.com/communities/1/topics/6810-battery-degradation-how-actually-is-it-calculated), [completed charges](https://support.teslafi.com/knowledge-bases/2/articles/10110-what-are-completed-charges) |
| Recurrent | Range Score is expected range today divided by observed range when new for that configuration. Recurrent says a lab capacity in kWh is not what the car reports, and that an exact state of health from outside the vehicle is not available. | [Range Score](https://www.recurrentauto.com/news/recurrent-expands-coverage-used-ev), [why an exact health figure is not available](https://www.recurrentauto.com/questions/why-cant-recurrent-tell-me-exactly-how-healthy-my-battery-is) |
| This integration | No BMS state of health exists on the Open API, so the sensor is the coulomb-counting estimate below. | [Open API note](marstek_device_openapi.MD) |

## What the Marstek signals are actually worth

Fast poll, default every 30 seconds, with a default 5 second gap between the
calls inside the cycle. No extra request is added for this sensor.

| Signal | Granularity and trust | Used? |
| --- | --- | --- |
| `bat_soc` | State of charge. Captures and the mock report whole percents. A 20-point window can therefore close about one point early or late, which is about 5 percent on that one window. It is the BMS's own percentage. If that percentage is stretched to a fixed nameplate, the ratio `Q = y / x` recovers the stretched scale, not chemical fade the BMS is hiding. Plett's circular-dependence warning applies if the BMS coulomb-counts this percentage with its own capacity. | Yes. It is the only state-of-charge signal. |
| Battery power | Watts, integer on the wire. Venus E 3.0 and several later firmwares omit `bat_power`; the integration then uses `pv_power - ongrid_power - offgrid_power`, which for a pack with no DC input is the inverter AC port. Sign: positive discharges. This is an AC measurement, so it is not cell current. | Yes. |
| `ES.GetMode` | Operating mode. Used only to discard a window that changes mode. A poll that omits the mode is not treated as a change. | Yes, as a gate. |
| `bat_cap` | Nameplate watt-hours where the firmware profile says so. Venus E captures show 5120 Wh. Venus E mini stores remaining energy in this field, so it is not a nameplate and state of health is not created. It is not the estimate. | Nameplate only. |
| Lifetime grid, PV, and load energy | House and inverter meters. Encoding changes with firmware (Wh versus 0.01 kWh, PV1 deciwatts). They do not measure charge into the cells. | No. |
| `Bat.GetStatus` | Remaining and rated watt-hours, temperature, charge flags. BMS bookkeeping. The call is off by default and is suspected of resetting some devices. | No. |
| Cell voltage, pack voltage, cycle count, BMS state of health | Not in the Open API. | Not invented. |

### Granularity, per family

None of these builds expose a second, finer state of charge. The parser
forwards `bat_soc` as the device sent it. Captures and the mock both use
whole percents (`int` in the mock's `get_state`). A fractional percent has
not shown up in a capture, so the estimator treats one count as one point.
Power fields on the wire are whole watts. The mock reports `round(watts)`.

`bat_power` is the exception that is usually missing. The field string is
in the HMG-50 Control **153** table only. It is absent from HMG-50 155/156
and from the archived VNSE3-0, VNSA-0, and VNSD-0 images, and the Venus E
3.0 firmware 150 LAN capture omitted it
(`tools/mock_device/firmware_quirks.py`, Open API note section 4.3). When
it is absent the integration derives it:

```text
bat_power = pv_power - ongrid_power - offgrid_power
```

with the API sign (positive charges). For a Venus E with no PV that is the
inverter AC port, which is what the estimator integrates. For Venus A and
Venus D it is an energy balance, so a wrong PV wattage becomes a wrong
battery wattage. Channel 1 stays deciwatts through firmware 150.9 (issue
[#57](https://github.com/taurgis/has-marstek-local-api/issues/57)); the
parser scales it to watts before that balance. A tenfold PV mistake would
land outside the 15–135 percent nameplate band and be dropped, rather than
published as capacity.

Lifetime energy is the untrustworthy counter. Venus A at 148 and below
stores solar energy in Wh; Venus A 149 and every known family at 150 and
above store it in 0.01 kWh, and EM energy at those generations is 0.1 Wh
(Open API note, section 4.2, issues
[#35](https://github.com/taurgis/has-marstek-local-api/issues/35) and
[#57](https://github.com/taurgis/has-marstek-local-api/issues/57)). Grid
totals on the Venus E 150 capture were Wh. Those counters are not used.

Nameplate `bat_cap` on Control firmware is the rated pack (Venus E 150:
5120 Wh; Venus A: 2080 Wh). Venus E mini, including `VNSEM-0` 301, puts
remaining energy in that field (1916 Wh at 95 percent, rated 2009 Wh,
issue [#86](https://github.com/taurgis/has-marstek-local-api/issues/86)),
so state of health is not created there. A `bat_cap` of 0 is treated as a
silent BMS, not as an empty pack, except on the mini where 0 can be a
drained pack.

Update rate is the fast poll: 30 seconds by default, with 5 seconds between
the calls in the cycle, both configurable. A 20-point window at a steady
power therefore contains on the order of minutes of samples, not a
high-rate current trace. `Bat.GetStatus` would add temperature and the
BMS remaining/rated pair, and it stays off because it is suspected of
resetting some devices (issue
[#14](https://github.com/taurgis/has-marstek-local-api/issues/14)).

HMG-50 (Venus C, and Venus E 2.0 which this integration does not set up)
shares one Wi-Fi receive channel with its meter client, so many Open API
datagrams are simply lost (issue
[#82](https://github.com/taurgis/has-marstek-local-api/issues/82)). A gap
over 180 seconds aborts the open window. That makes HMG-50 a poor source
of clean windows, which is another reason the sensor does not add traffic.

What is trustworthy enough to integrate: `ES.GetStatus` `bat_soc`, the
derived or reported battery watts after the parser's sign flip, and
`ES.GetMode` as a gate. What is not: lifetime energies, `Bat.GetStatus`
bookkeeping, GetMode's CT zeros (the Venus E 150 capture showed
`ct_state=0` while `EM.GetStatus` showed a live meter), and any field the
API does not have.

## The method that those constraints allow

1. Keep a window only while direction, mode, and the sample gap (180 seconds)
   stay clean, power stays at or above 150 W, and state of charge moves at
   least 20 points across at least four samples.
2. Convert the AC integral with one-way efficiency 0.93 (discharge divides,
   charge multiplies). Capacity is that DC energy divided by the
   state-of-charge fraction.
3. Drop a window outside the physical band above.
4. Publish the median of up to 21 windows once three exist.
5. When both directions have three windows, replace that median with the
   geometric mean of the two direction medians.
6. Publish state of health only against a real nameplate. Stay unknown until
   step 4 has something to publish.

## Expected error, stated as a bound rather than a calibration

These are limits, not a claim that a particular Marstek pack was checked on
a DC meter. No real device was available for this change.

- **Quantization.** One state-of-charge count on a 20-point window is about
  5 percent. The median of several windows is tighter. On the mock, with
  integer state of charge and the same 0.93 the estimator uses, a sampled
  discharge lands within 6 percent of the true 4600 Wh. That 6 percent is
  the mock's result, and it is the right order of magnitude for quantization
  plus the mock's power noise when the efficiency assumption matches.
- **Efficiency, one direction only.** The absolute watt-hours scale by
  `η_true / 0.93` on discharge and by `0.93 / η_true` on charge. A real
  one-way efficiency of 0.90 instead of 0.93 is about a 3 percent bias. A
  larger miss is a larger bias. This has not been measured on Marstek
  hardware. Victron's 99 percent figure is coulombic efficiency at a DC
  shunt, not this AC port.
- **Efficiency, both directions.** The geometric mean cancels a factor shared
  by both directions. If the two real one-way efficiencies differ, the
  residual factor is the square root of their ratio. A few percent of
  difference between charge and discharge leaves a residual of about half
  that, in relative terms. The two medians stay visible in the attributes
  through the last window and through `spread_wh`.
- **BMS state of charge.** This can dominate the arithmetic. If the
  percentage does not move when the pack has faded, the sensor will not
  move either. The sensor measures "watt-hours per reported state-of-charge
  point", which equals chemical capacity only when the reported percentage
  is an honest fraction of that capacity.
- **Temperature and rate.** Not corrected. Cold weather and very high power
  make a session read low. Peukert is not applied.
- **Not a nameplate test.** A lab capacity is a full charge and discharge at
  a stated temperature and rate. These windows are whatever the house was
  doing, provided the window was long and clean.

The number to watch over months is the trend. A single reading, especially
before both charge and discharge have contributed, is not a warranty
measurement.
