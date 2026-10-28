---
title: FAQ
description: Short answers to common questions about the Marstek integration for Home Assistant, from cloud use and the Marstek app to safety and updates.
---

# Frequently asked questions

## Does it need the Marstek cloud or an account?

No. Home Assistant talks to the battery directly on your network. You still need the Marstek app once, to set up the battery and turn on Open API.

## Can I keep using the Marstek app?

Yes. The app and Home Assistant can both see the battery. A change made in the app shows up in Home Assistant on the next poll. Marstek says enabling Open API may disable some app features to prevent conflicting commands.

## Is it safe for my battery?

The integration is built to go easy on the battery: one request at a time by default, firmware-aware controls, and risky requests off by default. Still, some Marstek firmware misbehaves under polling, and the Open API is provided by Marstek "as is". Keep the battery firmware up to date, and read [Limitations](limitations.md). The project comes with no warranty.

## Is it secure?

The Open API has no password. Anyone on the same network can read and control the battery once it's on. Keep the battery on a trusted network, not a guest network. The integration itself sends nothing outside your network.

## How often does it update?

Power and charge every 30 seconds, solar every 60 seconds, and diagnostics every 5 minutes. You can change these under **Configure** on the battery. Going faster puts more load on the battery and isn't recommended. See [Options](../options.md).

## Why is a sensor missing?

Most often the battery's firmware doesn't support it, so the integration leaves it out. Solar sensors only exist on Venus A and Venus D. Battery details such as temperature start disabled, and on older, fragile firmware they are left out completely. Venus C below firmware 155 has no grid meter sensors. See [Entities](../entities.md) and [troubleshooting](../troubleshooting.md#missing-ups-panel-led-bluetooth-advertising-or-depth-of-discharge).

## Why did my battery go unavailable?

Usually a network hiccup or a battery restart. The integration keeps the last values until three polls in a row have failed, then marks it unavailable. You can change that number under **Configure** > **Network Settings** (**Failures before unavailable**). [Why a battery goes offline](../firmware-resets.md) explains the different cases and how to tell them apart.

## Is it as reliable as a Modbus integration?

Not quite, and it's worth knowing why. Modbus uses a wired connection and doesn't depend on the Open API at all. The Open API needs no extra hardware, but it runs over Wi-Fi, drops some replies, and older firmware can switch it off or reset settings under polling. On firmware 150 or newer, Venus A, D, and E 3.0 are not known to reset under polling. Venus C has its own [caveat](supported-devices.md#venus-c-caveat). If your battery keeps resetting anyway, read [Limitations](limitations.md#choices-this-integration-makes) and consider a [Modbus integration](comparison.md#modbus-integrations).

## Can I run it next to a Modbus integration?

They use separate connections on the battery, so both can be installed. Both can also change the operating mode, though, so let only one of them control the battery. One owner reported having to [disconnect Modbus before firmware 150 would install](https://thuisbatterijgids.net/community/t/marstek-venus-e-3-0-met-home-assistant-local-api-instabiel-modbus-werkt-goed/614). If an update stalls, try that first.

## Can it keep my grid import at zero?

Let the battery do that itself. In **Auto** mode it follows its CT meter and reacts within seconds. Home Assistant only sees new values every 30 seconds, so an automation that chases a P1 meter by changing passive power will lag behind and overshoot.

No Marstek CT meter, but a P1 or other grid meter in Home Assistant? [AstraMeter](https://github.com/tomquist/AstraMeter) can pose as a Marstek CT meter and feed it that reading, so Auto mode follows your existing meter.

## Does it support peak shaving?

Not yet. The Marstek app's **Peak shaving** (maximum grid import) setting is not part of the Open API on firmware 150 or 151, so no Open API integration can read or change it. Marstek only exposes it through its cloud (MQTT) link and Bluetooth. If a later firmware adds it, the integration will follow ([#122](https://github.com/taurgis/has-marstek-local-api/issues/122)).

## Does it work with more than one battery?

Yes. Add each battery separately. The Battery health dashboard shows them side by side.

## Can I control the battery from the Energy dashboard?

No. The Energy dashboard only shows energy. Control comes from the **Operating mode** select, device actions, and services in automations. See [Automations and services](../services.md).

## How do I update the integration?

HACS shows an update when a new release is out. Install it and restart Home Assistant. The [changelog](https://github.com/taurgis/has-marstek-local-api/blob/main/CHANGELOG.md) lists what changed.

## How do I remove it?

Delete each battery under **Settings** > **Devices & services** > **Marstek**, then remove the integration in HACS and restart. See [Installation](../installation.md) and the [README](https://github.com/taurgis/has-marstek-local-api#removing-the-integration).

## Where do I get help?

- Search [Troubleshooting](../troubleshooting.md) first.
- Then check [existing issues](https://github.com/taurgis/has-marstek-local-api/issues), or open a new one with [debug logs and diagnostics](../troubleshooting.md#what-to-include-in-a-bug-report).
- Marstek support can't help with this integration. It is an independent project.
