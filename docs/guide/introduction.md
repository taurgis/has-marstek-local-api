---
title: What is this?
description: What the Marstek integration for Home Assistant does, how it talks to your battery, and what it does not do.
---

# What is this?

This is a custom integration for [Home Assistant](https://www.home-assistant.io/). An integration is a plug-in that teaches Home Assistant to talk to one kind of device. This one talks to **Marstek home batteries**: Venus A, Venus C, Venus D, Venus E 3.0, and Venus E mini.

Once it is set up, the battery appears in Home Assistant as a device. It shows how full the battery is, how much power flows in and out, and what the grid and solar panels are doing. You can also tell the battery to charge, discharge, or stop, by hand or from an automation.

## How it talks to the battery

Marstek batteries have a built-in local interface that Marstek calls the **Open API**. When you switch it on in the Marstek app, the battery answers questions from other devices on your home network.

Home Assistant sends the battery a short question every 30 seconds, such as "what is your state of charge?", and the battery answers. This is called **polling**. The messages use UDP, a lightweight network protocol, on port 30000 unless you changed it.

Three things follow from that design:

- **No cloud.** The messages never leave your home network. The integration keeps working when Marstek's servers are down, and it needs no Marstek account or password.
- **No extra hardware.** You don't need a Modbus adapter, an ESP32, or an MQTT broker. Home Assistant and the battery only need to share a network.
- **Polling, not pushing.** The battery never announces a change by itself. A change you make in the Marstek app shows up on the next poll.

## What it does

- Shows battery, grid, and solar readings as Home Assistant sensors.
- Feeds grid and solar energy totals to the Energy dashboard.
- Switches the operating mode (Auto, AI, and UPS on firmware that supports it).
- Charges or discharges at a set power for a set time (passive mode).
- Writes the battery's built-in charge and discharge schedules (manual mode).
- Sets depth of discharge, Bluetooth advertising, and the panel LED, on firmware that accepts them.
- Estimates usable capacity and battery health, if you turn that on.
- Finds the battery again when its IP address changes.

The full list, with links, is on [Features](features.md).

## What it does not do

- It does not replace the Marstek app. You still need the app to set up the battery, update firmware, and turn on Open API.
- It does not update firmware or factory-reset the battery. Those requests exist in the Open API, but they are left out on purpose.
- It does not work with **Venus E 2.0**. Polling that model can disconnect it from its CT meter.
- It does not read back everything it can write. For example, Marstek offers no way to read the current schedule slots or depth of discharge, so Home Assistant shows the last value it sent.

The honest list of rough edges is on [Limitations](limitations.md).

## Why it is careful

Marstek's Open API is young, and some firmware versions misbehave under load. Some switch the Open API off, or even reset their settings, when they get too many requests ([#15](https://github.com/taurgis/has-marstek-local-api/issues/15)). Because of that, the integration:

- sends one request at a time to each battery, with a pause between requests,
- polls slow-changing data (Wi-Fi, battery cell details) only every 5 minutes, and only when you use those sensors,
- checks the firmware version and leaves out controls the battery would reject,
- warns you in **Settings** > **System** > **Repairs** when your firmware is known to be fragile.

[Why a battery goes offline](../firmware-resets.md) explains the different kinds of dropouts in plain language.

## Next

Check that your battery is on the [supported devices](supported-devices.md) list, then go through [Before you start](before-you-start.md).
