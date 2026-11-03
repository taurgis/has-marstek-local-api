---
layout: home
title: Marstek for Home Assistant
titleTemplate: false
description: Monitor and control Marstek home batteries (Venus A, C, D, E 3.0, E mini) from Home Assistant over your local network. Free, open source, no cloud account.

hero:
  name: Marstek for Home Assistant
  text: Local control for your home battery
  tagline: Live charge, power, and solar in Home Assistant. Charge or discharge from automations. Works with Venus A, C, D, E 3.0, and E mini over your own network, with no Marstek cloud account and no extra hardware.
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started
    - theme: alt
      text: Will it work with my battery?
      link: /guide/supported-devices
    - theme: alt
      text: See it in action
      link: /screenshots

features:
  - icon: 🔋
    title: Live battery state
    details: Charge level, battery power, and grid power every 30 seconds, plus solar on Venus A and Venus D every minute.
    link: /entities
    linkText: Every sensor
  - icon: ⚡
    title: Charge and discharge from automations
    details: Charge, Discharge, and Stop actions in the automation editor, plus timed passive mode and manual schedules for cheap-tariff charging.
    link: /services
    linkText: Automations and services
  - icon: 📊
    title: Energy dashboard ready
    details: Lifetime battery and solar energy counters for Home Assistant's Energy dashboard, plus grid totals from the CT meter (the clamp-on meter at your fuse box) when it reports them.
    link: /energy_dashboard
    linkText: Set it up
  - icon: 🩺
    title: Battery health, optional
    details: Estimates usable capacity and state of health from long charge and discharge runs. The dashboard and smart-plug linking are opt-in.
    link: /usable-capacity
    linkText: How it works
  - icon: 🔍
    title: Finds the battery for you
    details: Searches your home network for batteries, and keeps track of one when your router gives it a new network address (IP address).
    link: /configuration
    linkText: Adding a battery
  - icon: 🛡️
    title: Gentle on your battery
    details: Asks the battery one thing at a time, with pauses, and hides controls its firmware would reject, to keep older firmware stable. Readings linked to battery resets stay off unless you turn them on.
    link: /guide/limitations
    linkText: Known limitations
---

<div class="mk-proof">

- Free and MIT-licensed
- No cloud account
- [Tested against Marstek's own firmware](/development#test-matrix) in an emulator for most versions
- [![Latest release](https://img.shields.io/github/v/release/taurgis/has-marstek-local-api?label=release&color=0f766e)](https://github.com/taurgis/has-marstek-local-api/releases)
- [![GitHub stars](https://img.shields.io/github/stars/taurgis/has-marstek-local-api?style=flat&color=0f766e)](https://github.com/taurgis/has-marstek-local-api)

</div>

## See it in Home Assistant

<div class="mk-gallery">
<figure>
<a href="/screenshots#discovery-and-setup"><img src="./screenshots/device-details.png" alt="Marstek device page in Home Assistant with battery level, power, operating mode, and controls." loading="lazy" /></a>
<figcaption>The device page: charge, power, mode, and controls.</figcaption>
</figure>
<figure>
<a href="/screenshots#controlling-the-battery"><img src="./screenshots/automation-action.png" alt="Automation editor with Marstek device actions to charge, discharge, or stop the battery." loading="lazy" /></a>
<figcaption>Charge, discharge, or stop from the visual automation editor.</figcaption>
</figure>
<figure>
<a href="/screenshots#battery-health-sidebar-dashboard"><img src="./screenshots/battery-health-dashboard-desktop.png" alt="Battery health dashboard comparing usable capacity and state of health over time." loading="lazy" /></a>
<figcaption>An optional battery health dashboard.</figcaption>
</figure>
</div>

[Take the visual tour →](/screenshots)

## Is this for me?

You're set if:

- You have a **Marstek Venus A, C, D, E 3.0, or Venus E mini**. Venus E 2.0 does not work with it. See [supported devices](/guide/supported-devices).
- You run **Home Assistant 2025.10 or newer** with [HACS](https://hacs.xyz/) (the Home Assistant Community Store) installed, or you are comfortable copying a folder into Home Assistant.
- The battery and Home Assistant are on the **same home network**.
- You can switch on **Open API** for the battery in the Marstek app. Open API is the battery's local connection for other devices on your network. It is off by default.

Not sure what some of these words mean? The [glossary](/guide/glossary) explains them in one line each.

## Three steps to a working battery

1. **Turn on Open API** in the Marstek app. [Before you start](/guide/before-you-start) shows what to check.
2. **Install the integration** from HACS and restart Home Assistant.
3. **Add the battery** under **Settings** > **Devices & services**. Most batteries are found automatically. Two cases need you to type the battery's IP address instead: a Venus E mini, and a battery on a separate network (VLAN) from Home Assistant.

The [getting started guide](/guide/getting-started) walks through each step with screenshots, then shows you how to add the battery to the Energy dashboard and build a first automation.

<div class="mk-cta">

[Get started](/guide/getting-started){.mk-button} [Check your battery](/guide/supported-devices){.mk-button .mk-button-alt}

</div>

> [!NOTE] Independent project
> This is a free, MIT-licensed community project. It is not made, endorsed, or supported by Marstek. Read the [limitations](/guide/limitations) before you rely on it for anything important.
