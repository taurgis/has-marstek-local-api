# Troubleshooting

Find the heading that matches what you see. Each section starts with what to do, then explains why. The parts meant for developers and bug reports sit in collapsed **Technical details** blocks.

If the battery keeps going offline and coming back, start with [Why a battery goes offline](firmware-resets.md). It explains the different kinds of outage and how to tell them apart.

## Missing UPS, Panel LED, Bluetooth advertising, or depth of discharge

You expect UPS mode, a **Panel LED** switch, a **Bluetooth advertising** switch or a **Depth of discharge** number, and it is not there.

These controls only appear when the battery's [firmware](guide/glossary.md#firmware) can accept them. On other firmware the integration leaves them out completely, so you never see a control that cannot work.

| Control | When it appears |
|---|---|
| Depth of discharge, Bluetooth advertising, Panel LED | Venus A, D and E 3.0 with firmware **150 or newer** (never on Venus C, which uses the [HMG-50](guide/glossary.md#hmg-50) board). Venus E mini when its firmware number is known, with no 150 minimum. |
| UPS on the operating-mode select | Venus A, D and E 3.0, or Venus E mini, with firmware **150 or newer** (never on Venus C / HMG-50) |

If they are missing:

1. Check the device model on the device page. Venus E mini is a different model from Venus E; it reports `VNSEM-0`.
2. Check the firmware number in the **Device version** diagnostic sensor, or under **Download diagnostics** > `firmware_profile`.
3. If the firmware number is unknown or cannot be read, the integration plays safe and shows none of these controls and no UPS mode.
4. After a firmware update, reload the integration or wait for the next background scan. **Reconfigure** also reads the new firmware number. You do not need to delete and re-add the device.

**The controls show the last value Home Assistant wrote.** The battery has no way to report its depth of discharge, Bluetooth or LED setting, so Home Assistant remembers the last value it sent successfully. A change made in the Marstek app, by another controller, or by a device reboot does not show up in Home Assistant.

**Bluetooth advertising looks reversed after an upgrade.** Earlier versions of the integration sent this value inverted, so **on** stopped advertising. If the switch does not match the device, turn it off and on again once after upgrading.

<details class="details custom-block">
<summary>Technical details</summary>

- Step 2 reads the Open API `ver` that discovery reports. Step 4 applies to a firmware update that crosses a capability or reset-safety gate. The background scanner includes a unicast fallback for when broadcast cannot reach the device.
- The Open API documents **no GET methods** for DOD, Bluetooth advertising or LED, which is why Home Assistant restores the last successful write.
- The Open API PDF documents `Ble.Adv` backwards. That is where the inverted Bluetooth value in earlier versions came from.
- Older notes that "LED is not in the API" applied to legacy Open API firmware. Capable firmware has a **Panel LED** switch (`Led.Ctrl`).
- `Set.Ver` and factory reset are intentionally not exposed in the default UI.

</details>

## Venus E mini: Manual power shows as ---, or Passive times out

These are separate problems on Venus E mini (`VNSEM-0`) firmware **301** ([#104](https://github.com/taurgis/has-marstek-local-api/issues/104)).

**Manual schedule power shows `---`.** Update the integration to **1.3.2 or newer**. Older versions sent schedules in a format the mini accepts without storing the power: the Marstek app showed the slot's time, weekday and on/off state, but the power stayed `---`. Since 1.3.2 an owner confirmed that Manual mode with a power value works on firmware 301 ([#86](https://github.com/taurgis/has-marstek-local-api/issues/86)). If a slot is still refused, look in the Marstek app for a schedule that overlaps it and turn that one off first.

**Passive mode is missing or refused.** The firmware on this model does not support Passive mode. On firmware 301 the integration leaves Passive out of the operating-mode select, and the **Set passive mode** action refuses it without sending anything. Older integration versions sent it anyway and reported `Request timeout` or a parse error. Use Auto, AI, Manual or UPS instead.

**The mini shows as unavailable.** Put the mini on a /24 network, where the [subnet mask](guide/glossary.md#subnet-24) is `255.255.255.0`. That is the usual home-router default. Firmware 301 sends every reply to the address made of the first three numbers of its own IP plus `.255` (for `192.168.1.40`, that is `192.168.1.255`), whatever the network's subnet mask. On a /24 network that is the broadcast address and Home Assistant hears it. On a larger network, such as a /16 or a /23, the replies never arrive.

<details class="details custom-block">
<summary>Technical details</summary>

**Manual.** The device answered `set_result: true` for positive, negative and zero `power` after a six-field `manual_cfg` with no `manual_set`, yet the power stayed `---`. Rev 3.1 documents an extra `manual_set` field for Venus E mini only (0 disable, 1 charge, 2 discharge, 3 auto). The official Manual example uses `"power": 100` with `"manual_set": 1`. Since integration 1.3.2 the mini gets that field, with `power` as a non-negative magnitude (Home Assistant services stay signed). An enabled 0 W slot sends `manual_set` 1, not auto (3).

**Passive.** The device answers `ES.SetMode` Passive with `Parse error` (`-32700`, data `401`) and JSON-RPC id `0`, not the request id. The firmware image explains it: its `ES.SetMode` only knows Auto, AI, Manual and UPS, and the word "Passive" is not in it. Auto, AI, UPS, depth of discharge, the panel LED and Bluetooth advertising were all acknowledged by the firmware in the emulator.

What `data` 401 means is not documented beyond the image's reject path. A UDP capture of the JSON the official app sends when a schedule's power is a number, and of Passive if the app can set it on this model, is the evidence that would show a different payload. The `VNSEM-0` 301 mock reproduces this report. Its reads come from [#86](https://github.com/taurgis/has-marstek-local-api/issues/86). Its Manual and Passive answers were not taken from a successful app capture.

</details>

## Device not discovered

Home Assistant does not list your battery when you add the integration.

- Check that **Open API is enabled** in the Marstek app. The [Open API](guide/glossary.md#open-api-local-api) is off by default.
- Make sure Home Assistant and the battery are on the **same network segment**. Discovery does not cross into another segment, such as a [VLAN](guide/glossary.md#vlan).
- Make sure [UDP](guide/glossary.md#udp) **port 30000** is allowed. Access-point or client isolation on the router can block discovery.
- If setup fails with `No valid response from device`, another program may already be using the battery's Open API port. Stop that program, or change the Open API port in the Marstek app.

<details class="details custom-block">
<summary>Technical details</summary>

This integration **sends from each device's configured Open API port** (default 30000, but the port is user-configurable in the Marstek app). Some firmware replies only to that listen port and ignores ephemeral source ports. Devices on different ports each get their own local socket. Devices that share a port share that socket: adding a second same-port device reuses it for `Marstek.GetDevice` instead of binding again. If another integration already bound a device's port without `SO_REUSEPORT`, setup fails with `No valid response from device`.

</details>

## Entities unavailable

The battery's sensors show as unavailable.

- Check the Wi-Fi signal and how stable the network is.
- Ask for fresh data with the `marstek.request_data_sync` action.
- Raise the request timeout or **Failures before unavailable** in [Options](options.md).

This is expected behavior after a few failed requests. The device's Open API can be unstable, so the integration keeps the last known values through short failures. Entities only turn unavailable after the number of failed polls set in **Failures before unavailable** (3 by default). [Why a battery goes offline](firmware-resets.md) helps you tell a network problem from a battery restart.

## Connection instability

The battery often stops answering for a while, or values update unevenly.

- Poll less often: raise the fast and medium polling intervals.
- Make sure only one controller talks to the battery.
- If **Parallel API requests** is on, turn it off first, especially on Wi-Fi.
- On Venus E 3.0, firmware **150** is Marstek's fix for Local API send failures over **Ethernet** ([#15](https://github.com/taurgis/has-marstek-local-api/issues/15)). On any firmware below 150 the **Parallel API requests** option has no effect.
- A network cable usually answers faster and more reliably than Wi-Fi.
- The diagnostic **Restarts** sensor tells a device that rebooted from one the network lost. Its `dropouts_30d` attribute counts outages with no restart behind them. See [Restarts and dropouts](usable-capacity.md#restarts-and-dropouts).

<details class="details custom-block">
<summary>Technical details</summary>

- Firmware **below Control 150** (including Venus E app **147.6**, Open API `ver` **1476**) ignores the parallel option.
- Wi-Fi Open API still uses the Quectel FC41D `AT+QISEND` path. On a Venus E 3.0 150, about one Wi-Fi request in four gets no reply to its first copy, and losses come in bursts: the firmware drops a reply when the Wi-Fi module stays busy for about two seconds, then ignores requests for a second or two.
- On known-safe firmware (Control 150+; never on HMG-50, where a second copy would land on the shared meter channel) the integration sends **read-only** unicasts again at 0.5 s, 2.5 s and 5 s while there is no reply, staying inside the configured request timeout (at most four datagrams). A wired LAN, or Home Assistant talking to the Ethernet IP of a dual-homed device, typically answers before 500 ms and never gets an extra copy. Writes, unknown firmware and reset-prone builds stay one-shot.
- In diagnostics, check `polling_config.request_strategy` and `polling_config.request_delay_effective` to confirm actual request behavior.

</details>

## Local API disables itself / settings reset to defaults

The battery stops answering Home Assistant. In the Marstek app the Local API is switched off, and other settings may be back at their defaults. Wi-Fi settings often survive.

What to do now:

1. Turn the Local API back on in the Marstek app.
2. Update the battery's firmware in the Marstek app. If the update over the network cable fails, use Wi-Fi or Bluetooth.
3. Until you can update, keep **Parallel API requests** off and use a wired network if you can.
4. Do not factory-reset the battery to fix this. Home Assistant does not offer a factory reset (`Reset.Factory` is not exposed).

This is a bug in the battery's firmware, not in Home Assistant. While the Open API is polled, some firmware versions switch the Local API off and wipe user settings. On Venus E 3.0, firmware **150** is the published fix ("Optimized Local API send anomaly on Ethernet"). An owner confirmed it in [#15](https://github.com/taurgis/has-marstek-local-api/issues/15) after updating in the Marstek app.

The integration treats these firmware versions as reset-prone:

- Venus A, D, E 3.0 or Venus E mini below 150
- Venus C ([HMG-50](guide/glossary.md#hmg-50)) below 156

On those versions it shows a **warning** in **Settings** > **System** > [**Repairs**](guide/glossary.md#repairs) as soon as the battery is set up, even if the first connection attempt still fails. The warning has no fix button; it is there to tell you to update. The integration also sends less traffic to the battery, as listed in the technical details.

Venus E 2.0 is not supported. Existing config entries for that model fail setup with an error instead of polling.

<details class="details custom-block">
<summary>Technical details</summary>

Also treated as reset-prone: a known model with a missing `ver`, and an unknown model with a Control-like `ver` of 100–149.

On reset-prone firmware:

1. Parallel API requests stay off even if the option is enabled.
2. Unicast Open API calls to that device are sent one at a time.
3. `Bat.GetStatus` is **not sent**, and battery-detail entities are omitted ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)).
4. Writes wait for the current poll cycle to finish, and discovery waits for in-flight unicasts before binding the listen port.
5. Changing IP and firmware at the same time updates both in one reload.

Firmware research notes: [tools/firmware/ANALYSIS.md](../tools/firmware/ANALYSIS.md).

</details>

## Grid energy totals look frozen

**Total grid input energy** or **Total grid output energy** stops rising, while **On-grid power** still shows power flowing.

You usually do not need to do anything. Some Venus firmware versions keep returning the same total. The integration keeps the total moving by adding up the reported grid power between updates, and it restores the corrected total after Home Assistant restarts.

If the totals still look wrong, compare the grid energy sensors with `sensor.<device>_on_grid_power` and include debug logs in your [bug report](#what-to-include-in-a-bug-report).

<details class="details custom-block">
<summary>Technical details</summary>

The frozen fields are `total_grid_input_energy` and `total_grid_output_energy`, while `on_grid_power` continues to show import or export activity.

</details>

## Energy statistics look wrong after a firmware update

A firmware update restarts the battery, and some updates change how its lifetime counters are reported. Match what you see to the section that explains it:

- **A total stops rising** while the battery is clearly charging or discharging: [Grid energy totals look frozen](#grid-energy-totals-look-frozen).
- **One huge spike** on the day of the update: [Energy totals jump to an impossible value](#energy-totals-jump-to-an-impossible-value).
- **Solar about 10× too low or too high** from then on (Venus A and Venus D): [Venus A solar totals look 10× too low, then jump](#venus-a-solar-totals-look-10×-too-low-then-jump).

The integration ignores readings of zero while the battery boots, so a restart alone should not dent the Energy dashboard. It never rewrites data points Home Assistant already stored. Fix those under **Developer tools** > **Statistics**, and include diagnostics in a [bug report](#what-to-include-in-a-bug-report) if the pattern is new.

## Energy totals jump to an impossible value

The Energy dashboard shows one huge spike, often far more energy than the battery could move.

To remove a spike that is already stored:

1. Go to **Developer tools** > **Statistics**.
2. Find the sensor.
3. Use **Adjust sum** on the spike to cancel it out.

Home Assistant's guide to this tab: <https://www.home-assistant.io/docs/tools/dev-tools/#statistics-tab>

Home Assistant adds every upward jump of a lifetime energy sensor to its long-term totals and does not drop spikes ([sensor entity](https://contributors.home-assistant.io/core/entities/sensor/)). So the integration now ignores readings that cannot be real energy from a home battery. Spikes stored before this guard existed stay until you adjust them.

<details class="details custom-block">
<summary>Technical details</summary>

Lifetime energy sensors (`total_grid_*`, `total_pv_energy`, `total_load_energy`, and meter `em_*_energy`) are `total_increasing`. Home Assistant adds every upward jump to long-term `sum` statistics.

The integration ignores readings that cannot be Wh on a home battery: negative, non-finite, above 1 GWh, or a single-poll jump above 5 MWh. A later sane Open API value replaces a previously stored garbage floor instead of keeping it forever ([#70](https://github.com/taurgis/has-marstek-local-api/issues/70)).

</details>

## Venus A solar totals look 10× too low, then jump

On Venus A, solar production in the Energy dashboard was about 10 times too low, then showed one large spike after you updated the integration.

If you see that one-time spike at the upgrade, go to **Developer tools** > **Statistics** to find and correct the transition: <https://www.home-assistant.io/docs/tools/dev-tools/#statistics-tab>

Venus A firmware 149 reports total solar energy in units of 0.01 kWh. Older integration versions read that number as watt-hours, so solar production was about 10× too low. The integration now converts it correctly. History already recorded is **not** rewritten. The first corrected value is a large step up, which Home Assistant adds to the total. It is **not** a meter reset: for a `total_increasing` sensor, Home Assistant treats a drop of 10% or more as a meter reset and ignores smaller drops as noise.

<details class="details custom-block">
<summary>Technical details</summary>

For example, raw `total_pv_energy` `25742` means `257.42 kWh`.

Firmware **148** (including app labels such as `148.3`) keeps solar energy in Wh. Integration **1.1.0** introduced firmware-gated **solar energy** scaling: only Venus A **149** (and known families at **150+**) starts the 0.01 kWh encoding ([#35](https://github.com/taurgis/has-marstek-local-api/issues/35)). Venus A Open API `ver` **1487** (app 148.7) and Venus D **149** / **1492** use the same 0.01 kWh encoding, so later integration versions scale them as well.

That energy fix is separate from PV1 power, below.

</details>

## PV1 power looks 10× too high

The **PV1 power** sensor (solar input 1) reads about 10 times higher than the Marstek app.

Update the integration. This was a bug after integration **1.1.0** ([#57](https://github.com/taurgis/has-marstek-local-api/issues/57)), first seen on firmware **148.3** and also on **150.9**. Current versions divide PV1 by 10 again, as 1.0.0 did. PV1 power is a different value from total solar energy, so the previous section does not apply.

If PV1 is still about 10× higher than the Marstek app, enable [debug logging](#debug-logging) and attach the log to a [bug report](#what-to-include-in-a-bug-report).

<details class="details custom-block">
<summary>Technical details</summary>

PV1 is `PV.GetStatus` channel 1. Channel 1 is still deciwatts on those builds; the integration divides it by 10. The Rev 3.1 PDF labels PV as watts, but skipping ÷10 at `ver >= 150` is what made PV1 jump.

In the debug log, capture `Send:` / `Recv:` for `PV.GetStatus` (`pv1_power`) plus `Marstek.GetDevice` (`ver`). App labels such as `148.3` and `150.9` map to Open API integers `148` and `150`. Diagnostics should show `pv_channel_1_power_scale` of `0.1`.

</details>

## Venus A solar or load energy stays at 0 Wh

The total solar energy sensor stays unavailable, even while the solar inputs show power.

If you need solar energy in the meantime, compare the sensor with `sensor.<device>_pv_power` and consider Home Assistant's Integration helper, which turns a power sensor into an energy total.

Some Venus A firmware versions report total solar energy as `0` while PV channel power is active. The integration treats such a zero as invalid instead of showing a misleading lifetime counter. So `total_pv_energy` may stay unavailable until the device reports a real non-zero total.

`total_load_energy` is shown as the device reports it. Marstek documents it as load or off-grid energy, and its exact meaning varies by firmware.

## Venus C stops charging from excess solar in Auto mode

Your Venus C is in Auto (self-consumption) mode, the battery is far from full, you are exporting to the grid, and it does not charge. It starts charging again the moment Home Assistant stops polling, or after you switch Open API off and on in the Marstek app ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)).

What you can do:

1. Raise the polling intervals (**Configure** > **Polling Intervals**). Less traffic means fewer lost meter readings. It does not remove the problem.
2. If Auto mode keeps stalling, read grid power from the meter directly, with a HomeWizard P1 or Shelly integration rather than through the battery. Keep the Marstek entry on slow intervals for battery state only.
3. Changing which meter the **battery** regulates on is a separate option. A HomeWizard P1 or an Eco-Tracker moves the meter feed off the shared channel; a Shelly does not. Treat this as worth testing; it is not a fix (see the technical details).

The integration cannot repair this. It is a limitation of the Venus C firmware. Venus C runs on the [HMG-50](guide/glossary.md#hmg-50) board, which uses a single Wi-Fi receive channel for both the Local API and the device's **own** link to its meter. Home Assistant's requests compete with the meter readings that Auto mode regulates on. When enough readings are lost, the device treats the meter as disconnected and stops charging, even with clear grid export. Marstek warns about the same defect on Venus E 2.0 and CT003.

No HMG-50 firmware fixes it, **156 included**. Its "Optimized OpenApi interface stability" note covers the API itself, not the shared channel.

On every Venus C, the integration:

- shows a **warning** in **Settings** > **System** > **Repairs**;
- keeps **Parallel API requests** and Wi-Fi repeat requests off, so it never puts a second message on the meter's channel.

<details class="details custom-block">
<summary>Technical details</summary>

HMG-50 Control is the firmware behind both Venus C and Venus E 2.0. It gives the Local API server and the device's own UDP meter client one Wi-Fi receive channel.

Which meter you pair decides whether it shares that socket. A Marstek CT and a Shelly are both read over the UDP client. A HomeWizard P1 and an Eco-Tracker are read over the Wi-Fi module's separate HTTP client. A Shelly uses the same UDP client as the Local API. All of them still share the module's single AT link, which is why moving to a HomeWizard P1 or Eco-Tracker may help but is not a fix.

Firmware research notes: [tools/firmware/HMG50_METER_CHANNEL.md](../tools/firmware/HMG50_METER_CHANNEL.md).

</details>

## Venus E 2.0

Venus **E 2.0 is not supported** ([HMG-50](guide/glossary.md#hmg-50)). Existing config entries fail setup, and discovery and manual add stop with an error. Using the integration with this model may disconnect the device from its CT003 meter, because of the shared meter channel described in the section above.

<details class="details custom-block">
<summary>Technical details</summary>

Open API `Marstek.GetDevice` reports `device: "VenusE"`, which is not Venus E 3.0 (`VenusE 3.0` / `VNSE3-0`).

</details>

## Debug logging

When you report an issue, a debug log helps us see the network messages and what the integration did with them.

### Preferred: enable debug logging from the UI (no restart)

1. Go to **Settings** > **Devices & services**.
2. Open the **Marstek** integration.
3. Open the **⋮** menu > **Enable debug logging**.
4. Reproduce the problem.
5. Go back to the Marstek integration menu > **Disable debug logging**. Home Assistant then downloads the log file.

Official Home Assistant docs: <https://www.home-assistant.io/docs/configuration/troubleshooting/#enabling-debug-logging>

### Advanced: configuration.yaml (logger integration)

Add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.marstek: debug
    custom_components.marstek.pymarstek: debug
```

Then restart Home Assistant.

Official Home Assistant docs: <https://www.home-assistant.io/integrations/logger/>

### What to include in a bug report

- Home Assistant version and Marstek integration version
- Steps to reproduce
- The downloaded debug log, or the part around the error or traceback
- Downloaded diagnostics (Marstek integration menu > **⋮** > **Download diagnostics**)

Debug logs may include personal details such as addresses on your network. Include only the parts that matter.
