# Why a battery goes offline

A Marstek battery can disappear from Home Assistant for a few seconds or a
minute and then come back. This page explains the different reasons and what
each one looks like. Five things can happen, from mild to severe:

| What happened | How long it is gone | Does it keep working meanwhile? | Shown in Home Assistant as |
|---|---|---|---|
| [Network dropout](#network-dropout) | One poll or more | Yes | Dropout |
| [Brief network reset](#brief-network-reset) | About 10 seconds | Yes, it keeps its last power | Brief network reset (not counted) |
| [Full restart](#full-restart) | 30 seconds to a minute | No, it stops and starts again | Restart (counted) |
| [Local API switched off](#local-api-switched-off) | Until you turn it back on | Yes, but Home Assistant cannot reach it | Connection repair |
| [Factory reset](#factory-reset) | Until it is set up again | No | Connection repair |

The **Restarts** sensor and the Restarts tab of the Battery health card list
the first three. How the integration tells them apart is in
[Restarts and dropouts](usable-capacity.md#restarts-and-dropouts).

## Network dropout

**What happens.** The battery keeps running, but its answer never reaches
Home Assistant. A weak Wi-Fi signal, a busy access point or a router that
restarts are the usual causes.

**What you notice.** Sensors go unavailable for one poll or a few, then carry
on as if nothing happened. Energy totals, the operating mode and the meter
link are unchanged.

**What you can do.** Move the access point closer or use a network cable.
Many dropouts in a row point at the network, not the battery.

### Technical details

- A dropout is an outage with no restart trace: no energy totals restored,
  no CT meter lost, no mode change and no start-up check at a linked plug.
- On Wi-Fi, firmware 150 and newer get up to three repeat requests within one
  poll (after 0.5, 2.5 and 5 seconds), so a single lost reply is recovered
  and never counts. Older firmware gets one request, and one lost reply is a
  failed poll. See [Wi-Fi Open API timeouts](../tools/firmware/ANALYSIS.md#wi-fi-open-api-timeouts-fc41d-firmware-150-included).
- On Venus C and other HMG-50 units, Home Assistant's requests and the
  device's own meter share one Wi-Fi receive channel, so some loss is
  expected there ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)).

## Brief network reset

**What happens.** The battery sends its data to the Marstek cloud. When that
keeps failing, it switches its network chip (Wi-Fi or Ethernet) off and on
again to try to fix the connection. The battery itself does not restart.

**What you notice.** Home Assistant loses the battery for about 10 seconds,
usually one missed poll or none. The battery keeps delivering the power it
was delivering. The meter readings stop updating for about a minute and then
catch up. A linked smart plug may show the same short start-up check it
shows after a restart. Energy totals and the operating mode are kept.

**What you can do.** These repeat as long as the cloud cannot be reached:
about every 15 to 30 minutes on Wi-Fi, about every 35 minutes on Ethernet.
If you see many, check whether a firewall, DNS filter or separate network
blocks the battery from the internet. Control firmware 151 lists fixes for
failing uploads.

### Technical details

- Present in Control 150, 1509 and 151 for Venus E, Venus A and Venus D.
  Older Control firmware, HMG-50 (Venus C, Venus E 2.0) and the Venus E mini
  do not have it. On Ethernet only Control 150 has it.
- Ethernet: the HTTP task (`0x08014ecc`) resets the CH395 chip when more than
  one upload record is queued and its 1800 s timer has run out. A user's LAN
  log showed 5 to 9 seconds without ping about every 35 minutes, with a DHCP
  Discover after each.
- Wi-Fi: the reset routine (`0x0800b224`) takes the modem lock, pulls the
  FC41D reset lines PE4/PE5 low for 1 second, waits 2 seconds more and lets
  the module rejoin from its saved network. No `AT+QIOPEN` is sent; the
  firmware's socket checks reopen the Local API socket within seconds and
  the CT meter socket about 75 seconds after the pulse.
- Measured in the firmware emulator on Venus E Control 150 without a cloud:
  first reset 31 minutes after boot, then three more within 10 minutes.
  Each time the Open API was gone about 10 seconds. `EM.GetStatus` kept
  `ct_state: 1` with the last `total_power`, and in Auto the battery held
  its last output until CT data returned.
- The integration lists a start-up check at the plug with no other trace and
  at most one failed poll as a brief network reset and leaves it out of the
  **Restarts** count. Why the plug sees a start-up check after a chip reset
  is not known.
- Source: [Network chip resets](../tools/firmware/ANALYSIS.md#network-chip-resets).

## Full restart

**What happens.** The battery's controller stops and starts again, like
turning a computer off and on. Some units do this every few hours or days
for no visible reason, also with the Local API switched on. A firmware update, a
restart from the Marstek app and some of the battery's own safety checks
also restart it.

**What you notice.** Home Assistant loses the battery for 30 seconds to a
minute. Energy used since the battery last saved its totals is lost, so a
lifetime total can step back a little. The meter shows as disconnected for
about a minute after start-up. A command from Home Assistant, such as
Passive mode, can end, and the battery goes back to its normal mode. A
linked smart plug shows a short start-up check: the standby draw drops, the
battery draws about 2.5 kW for 5 to 35 seconds, and then standby returns.

**What you can do.** Nothing is needed; the battery carries on by itself.
If it restarts often, note the times from the Restarts tab (or download the
CSV) and report them to Marstek.

### Technical details

- The software reboot (`0x08027e18`, logs `system will reboot!`) is called
  from OTA updates, develop mode, the MQTT `Reset` command, and two
  battery-state checks (`0x0802b394` after 3600 s, and `0x0802b444`). A
  hardware watchdog (IWDG, `0x08016f48`) restarts a stalled controller.
- At boot the Control firmware writes its saved lifetime counters back to
  the inverter (CAN command 0x16/0x17), so a raw `ES.GetStatus` total comes
  back lower than before. Nothing else makes these totals fall, so this
  counts even when no poll failed.
- The integration also counts a lost CT meter or a changed mode, but only
  when the battery was unreachable in between.
- A restart that misses only one poll and leaves nothing but the plug's
  start-up check cannot be told from a brief network reset and is listed as
  one.
- Source: [Restarts and dropouts](usable-capacity.md#restarts-and-dropouts)
  and the [firmware emulator notes](../tools/firmware_emulator/README.md).

## Local API switched off

**What happens.** On some older firmware the battery reset part of its own
settings after trouble with its network connection, and the Local API came
back switched off. The battery keeps running but no longer answers Home
Assistant.

**What you notice.** The battery stays unavailable and Home Assistant opens a
connection repair. The Marstek app still works.

**What you can do.** Turn **Local API** back on in the Marstek app, and
update the firmware. Firmware 150 fixed the Ethernet problem behind this.

### Technical details

- Firmware before Control generation 150 is treated as *reset-prone*. On it
  the integration sends one request at a time, never repeats a request, and
  never sends `Bat.GetStatus`, which is suspected of triggering resets
  ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)).
- The best explanation from the firmware: a Local API reply sent through the
  CH395 Ethernet chip (or a second copy of it) corrupted the Ethernet and
  HTTP path, and the recovery cleared the settings, including the Local API
  flag (EEPROM `0x371`). Control 150's changelog lists "Local API send
  anomaly on Ethernet" as fixed.
- Source: [firmware analysis](../tools/firmware/ANALYSIS.md).

## Factory reset

**What happens.** The battery forgets all its settings, including the network
and the Local API. This only happens when someone asks for it, in the
Marstek app or with the `Reset.Factory` request.

**What you notice.** The battery is gone from the network until it is set up
again.

**What you can do.** Set it up again in the Marstek app, turn on **Local
API**, and use **Reconfigure** in Home Assistant if its address changed.

### Technical details

- `Reset.Factory` and `Set.Ver` are part of Open API Rev 3.1 since Control
  150. The integration does not send them and does not expose them.
