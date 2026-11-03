# Why a battery goes offline

A Marstek battery can disappear from Home Assistant for a few seconds or a minute and then come back. Most of the time you do not need to do anything: the battery keeps running and Home Assistant picks it up again.

Two cases do need you:

- **The battery stays unavailable and Home Assistant shows a connection repair.** Open the Marstek app and check that the Local API is still switched on. See [Local API switched off](#local-api-switched-off).
- **It drops out many times a day.** Look at the **Restarts** tab of the [Battery health card](usable-capacity.md#restarts) to see which kind of outage it is, then read that section below.

Five things can happen, from mild to severe:

| What happened | How long it is gone | Does it keep working meanwhile? | Shown in Home Assistant as |
|---|---|---|---|
| [Network dropout](#network-dropout) | One poll or more | Yes | Dropout |
| [Brief network reset](#brief-network-reset) | About 10 seconds | Yes, it keeps its last power | Brief network reset (not counted) |
| [Full restart](#full-restart) | 30 seconds to a minute | No, it stops and starts again | Restart (counted) |
| [Local API switched off](#local-api-switched-off) | Until you turn it back on | Yes, but Home Assistant cannot reach it | Connection repair |
| [Factory reset](#factory-reset) | Until it is set up again | No | Connection repair |

A poll is one round of questions Home Assistant asks the battery, every 30 seconds by default ([polling](guide/glossary.md#polling)). The **Restarts** sensor and the Restarts tab of the Battery health card list the first three kinds. How the integration tells them apart is in [Restarts and dropouts](usable-capacity.md#restarts-and-dropouts).

## Network dropout

**What you notice.** Sensors keep their last values through a short dropout. They only show as unavailable after several polls in a row fail (3 by default, set with **Failures before unavailable**). Then they carry on as if nothing happened. Energy totals, the operating mode and the meter link are unchanged.

**What you can do.** Move the access point closer to the battery, or use a network cable. Many dropouts in a row point at the network, not the battery.

**What happens.** The battery keeps running, but its answer never reaches Home Assistant. The usual causes are a weak Wi-Fi signal, a busy access point, or a router that restarts.

<details class="details custom-block">
<summary>Technical details</summary>

- A dropout is an outage with no restart trace: no energy totals restored, no CT meter lost, no mode change and no start-up check at a linked plug.
- On Wi-Fi, firmware 150 and newer get up to three repeat requests within one poll (after 0.5, 2.5 and 5 seconds), so a single lost reply is recovered and never counts. Older firmware gets one request, and one lost reply is a failed poll. HMG-50 units (Venus C, Venus E 2.0) never get repeat requests, because they would compete with the battery's own meter traffic. See [Wi-Fi Open API timeouts](../tools/firmware/ANALYSIS.md#wi-fi-open-api-timeouts-fc41d-firmware-150-included).
- On Venus C and other HMG-50 units, Home Assistant's requests and the device's own meter share one Wi-Fi receive channel, so some loss is expected there ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)).

</details>

## Brief network reset

**What you notice.** Home Assistant loses the battery for about 10 seconds, usually for one missed poll or none. The battery keeps delivering the power it was delivering. The meter readings stop updating for about a minute and then catch up. A linked smart plug may show the same short start-up check it shows after a restart. Energy totals and the operating mode are kept.

**What you can do.** If you see many of these, check whether a firewall, DNS filter or separate network blocks the battery from the internet. They repeat for as long as the battery cannot reach the Marstek cloud: anywhere from a few minutes to about half an hour apart on Wi-Fi, and about every 35 minutes on Ethernet. Control firmware 151 lists fixes for failing uploads.

**What happens.** The battery sends its data to the Marstek cloud. When that keeps failing, it switches its network chip (Wi-Fi or Ethernet) off and on again to try to fix the connection. The battery itself does not restart.

<details class="details custom-block">
<summary>Technical details</summary>

- Present in Control 150 and 151 for Venus E, Venus A and Venus D, and in Venus A 1508 and 1509. Older Control firmware, HMG-50 (Venus C, Venus E 2.0) and the Venus E mini do not have it. On Ethernet only Control 150 has it.
- Ethernet: the HTTP task (`0x08014ecc`) resets the CH395 chip when more than one upload record is queued and its 1800 s timer has run out. An owner's LAN log showed 5 to 9 seconds without ping about every 35 minutes, with a DHCP Discover after each.
- Wi-Fi: the reset routine (`0x0800b224`) takes the modem lock, pulls the FC41D reset lines PE4/PE5 low for 1 second, waits 2 seconds more and lets the module rejoin from its saved network. No `AT+QIOPEN` is sent; the firmware's socket checks reopen the Local API socket within seconds and the CT meter socket about 75 seconds after the pulse.
- Measured in the firmware emulator on Venus E Control 150 without a cloud: first reset 31 minutes after boot, then three more within 10 minutes. Each time the Open API was gone about 10 seconds. `EM.GetStatus` kept `ct_state: 1` with the last `total_power`, and in Auto the battery held its last output until CT data returned.
- With the emulator's stand-in cloud answering, the same image ran 46 minutes without a reset: every upload was accepted and nothing queued up.
- On Ethernet the emulator has no cloud. The same image queued its uploads and reset the CH395 when its 1800 s timer ran out, then got its address and reopened the Local API socket within a second. On a real unit DHCP takes the 5 to 9 seconds seen in the LAN log.
- The integration lists a start-up check at the plug with no other trace and at most one failed poll as a brief network reset, and leaves it out of the **Restarts** count. Why the plug sees a start-up check after a chip reset is not known.
- Source: [Network chip resets](../tools/firmware/ANALYSIS.md#network-chip-resets).

</details>

## Full restart

**What you notice.** Home Assistant loses the battery for 30 seconds to a minute. A linked smart plug shows a short start-up check: the standby draw drops, the battery draws about 2.5 kW for 5 to 35 seconds, and then standby returns. After the restart:

- The meter shows as disconnected for about a minute.
- A command from Home Assistant, such as Passive mode, can end, and the battery goes back to its normal mode.
- The solar and load totals can dip a little. The battery loses the energy it counted since it last saved its totals. The integration holds the grid import and export totals at their last value, so those do not dip.

**What you can do.** Nothing is needed; the battery carries on by itself. If it restarts often, note the times from the Restarts tab (or download the CSV) and report them to Marstek.

**What happens.** The battery's controller stops and starts again, like turning a computer off and on. Some units do this every few hours or days for no visible reason, also with the Local API switched on. A firmware update, a restart from the Marstek app and some of the battery's own safety checks also restart it.

<details class="details custom-block">
<summary>Technical details</summary>

- The software reboot (`0x08027e18`, logs `system will reboot!`) is called from OTA updates, develop mode, the MQTT `Reset` command, and two battery-state checks (`0x0802b394` after 3600 s, and `0x0802b444`). A hardware watchdog (IWDG, `0x08016f48`) restarts a stalled controller.
- At boot the Control firmware writes its saved lifetime counters back to the inverter (CAN command 0x16/0x17), so a raw `ES.GetStatus` total comes back lower than before. Nothing else is known to make these totals fall, so this counts even when no poll failed.
- The integration also counts a lost CT meter or a changed mode, but only when the battery was unreachable in between.
- A restart that misses only one poll and leaves nothing but the plug's start-up check cannot be told from a brief network reset, and is listed as one.
- Source: [Restarts and dropouts](usable-capacity.md#restarts-and-dropouts) and the [firmware emulator notes](../tools/firmware_emulator/README.md).

</details>

## Local API switched off

**What you notice.** The battery stays unavailable and Home Assistant opens a connection repair. The Marstek app still works.

**What you can do.**

1. Turn **Local API** back on in the Marstek app.
2. Update the battery's firmware. Firmware 150 fixed the Ethernet problem behind this.

[Troubleshooting](troubleshooting.md#local-api-disables-itself-settings-reset-to-defaults) lists which firmware versions are affected.

**What happens.** On some older firmware the battery reset part of its own settings after trouble with its network connection, and the Local API came back switched off. The battery keeps running but no longer answers Home Assistant.

<details class="details custom-block">
<summary>Technical details</summary>

- Firmware before Control generation 150 is treated as *reset-prone*. On it the integration sends one request at a time and never sends extra copies of a read request. Commands such as mode changes are still retried if the battery doesn't answer. It also never sends `Bat.GetStatus`, which is suspected of triggering resets ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)).
- The best explanation from the firmware: a Local API reply sent through the CH395 Ethernet chip (or a second copy of it) corrupted the Ethernet and HTTP path, and the recovery cleared the settings, including the Local API flag (EEPROM `0x371`). Control 150's changelog lists "Local API send anomaly on Ethernet" as fixed.
- Source: [firmware analysis](../tools/firmware/ANALYSIS.md).

</details>

## Factory reset

**What you notice.** The battery is gone from the network until it is set up again.

**What you can do.** Set it up again in the Marstek app and turn on **Local API**. If its address changed, use **Reconfigure** in Home Assistant.

**What happens.** The battery forgets all its settings, including the network and the Local API. This only happens when someone asks for it, in the Marstek app or with the `Reset.Factory` request.

<details class="details custom-block">
<summary>Technical details</summary>

- `Reset.Factory` and `Set.Ver` are in the firmware from Control 149 (Venus A 148.7) on. The integration does not send them and does not expose them.

</details>
