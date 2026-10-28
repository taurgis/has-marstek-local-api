---
title: What is this?
description: What the Marstek integration for Home Assistant does, how it talks to your battery, and what it does not do.
---

# What is this?

This is a custom integration for [Home Assistant](https://www.home-assistant.io/). An integration is a plug-in that teaches Home Assistant to talk to one kind of device. This one talks to **Marstek home batteries**: Venus A, Venus C, Venus D, Venus E 3.0, and Venus E mini.

Once it is set up, the battery appears in Home Assistant as a device. It shows how full the battery is, how much power flows in and out, and what the grid and solar panels are doing. You can also tell the battery to charge, discharge, or stop, by hand or from an automation.

## How it talks to the battery

Marstek batteries have a built-in local connection that Marstek calls the **Open API**. When you switch it on in the Marstek app, the battery answers questions from other devices on your home network.

Every 30 seconds, Home Assistant asks the battery a short question, such as "how full are you?", and the battery answers. Asking at a fixed interval like this is called **polling**.

::: details Technical details
The messages are small JSON requests sent over UDP, a lightweight network protocol, on port 30000 unless you changed the port in the Marstek app. Marstek documents the requests in its Open API specification; this repository keeps a copy in [the Open API protocol notes](https://github.com/taurgis/has-marstek-local-api/blob/main/docs/marstek_device_openapi.MD).
:::

Three things follow from that design:

- **No cloud.** The messages never leave your home network. The integration keeps working when Marstek's servers are down, and it needs no Marstek account or password.
- **No extra hardware.** Some other ways to connect a Marstek battery need a wired adapter or a small extra computer (a Modbus adapter, an ESP32 board, or an MQTT broker). This one doesn't. Home Assistant and the battery only need to share a network.
- **Changes show up on the next check.** The battery never announces a change by itself. A change you make in the Marstek app appears in Home Assistant the next time it asks.

## What it does

- Shows battery, grid, and solar readings as Home Assistant sensors.
- Feeds battery and solar energy totals to the Energy dashboard. It adds grid totals when the CT meter reports them. The CT meter is the clamp-on meter at your fuse box that the battery uses to measure grid power.
- Switches the operating mode: Auto, AI, and UPS (backup) on firmware that supports it. Firmware is the software inside the battery.
- Charges or discharges at a set power for a set time (passive mode).
- Writes the battery's built-in charge and discharge schedules (manual mode).
- Sets depth of discharge (how low the battery may discharge), Bluetooth advertising, and the panel LED, on firmware that accepts them.
- Estimates usable capacity and battery health from long charge and discharge runs.
- Finds the battery again when its IP address changes.

The full list, with links, is on [Features](features.md).

## What it does not do

- It does not replace the Marstek app. You still need the app to set up the battery, update firmware, and turn on Open API.
- It does not update firmware or factory-reset the battery. The Open API can do both, but the integration leaves them out on purpose.
- It does not work with **Venus E 2.0**. Regular requests from Home Assistant can disconnect that model from its CT meter.
- It does not read back everything it can write. For example, Marstek offers no way to read the current schedule slots or depth of discharge, so Home Assistant shows the last value it sent.

The honest list of rough edges is on [Limitations](limitations.md).

## Why it is careful

Marstek's Open API is young, and some firmware versions misbehave when they are asked too much. Some switch the Open API off, or even reset their settings, when they get too many requests ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14), [#15](https://github.com/taurgis/has-marstek-local-api/issues/15)). Because of that, the integration:

- sends one request at a time to each battery by default, with a pause between requests,
- asks for slow-changing data (Wi-Fi, battery details) only every 5 minutes, and only when you use those sensors,
- checks the firmware version and leaves out controls the battery would reject,
- warns you in **Settings** > **System** > **Repairs** when your firmware version is known to be fragile.

[Why a battery goes offline](../firmware-resets.md) explains the different kinds of dropouts in plain language.

## Next

Check that your battery is on the [supported devices](supported-devices.md) list, then go through [Before you start](before-you-start.md).
