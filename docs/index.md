---
layout: home
title: Marstek for Home Assistant
description: Monitor and control Marstek home batteries (Venus A, C, D, E 3.0, E mini) from Home Assistant over your local network. Free, open source, no cloud account.

hero:
  name: Marstek for Home Assistant
  text: Your home battery, on your own network.
  tagline: See charge, power, and solar in Home Assistant, and tell the battery when to charge or discharge. Everything stays on your local network. No Marstek cloud account and no extra hardware.
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started
    - theme: alt
      text: Will it work with my battery?
      link: /guide/supported-devices
    - theme: alt
      text: Compare integrations
      link: /guide/comparison

features:
  - icon: 🔋
    title: Live battery state
    details: State of charge, charge and discharge power, grid power per phase, and solar on Venus A and Venus D. Updated every 30 seconds by default.
    link: /entities
    linkText: Every sensor
  - icon: ⚡
    title: Charge and discharge from automations
    details: Charge, Discharge, and Stop actions in the automation editor, plus timed passive mode and manual schedules for cheap-tariff charging.
    link: /services
    linkText: Automations and services
  - icon: 📊
    title: Energy dashboard ready
    details: Grid import, grid export, and solar totals plug straight into Home Assistant's Energy dashboard.
    link: /energy_dashboard
    linkText: Set it up
  - icon: 🩺
    title: Battery health, optional
    details: Estimates usable capacity and state of health from long charge and discharge runs. Off until you turn it on.
    link: /usable-capacity
    linkText: How it works
  - icon: 🔍
    title: Finds the battery for you
    details: Discovers batteries on your network and follows them when the router hands out a new IP address.
    link: /configuration
    linkText: Adding a battery
  - icon: 🛡️
    title: Careful with your battery
    details: One request at a time, features gated by firmware version, and risky requests left off by default. Some Marstek firmware resets when it is polled too hard.
    link: /guide/limitations
    linkText: Known limitations
---

## Is this for me?

This integration fits if **all** of these are true:

- You have a **Marstek Venus A, C, D, E 3.0, or Venus E mini**. Venus E 2.0 does not work with it. See [supported devices](/guide/supported-devices).
- You run **Home Assistant 2025.10 or newer** with [HACS](https://hacs.xyz/) installed, or you are comfortable copying a folder into Home Assistant.
- The battery and Home Assistant are on the **same home network**.
- You can switch on **Open API** for the battery in the Marstek app.

Not sure what some of these words mean? The [glossary](/guide/glossary) explains them in one line each.

## Three steps to a working battery

1. **Turn on Open API** in the Marstek app. [Before you start](/guide/before-you-start) shows what to check.
2. **Install the integration** from HACS and restart Home Assistant.
3. **Add the battery** under **Settings** > **Devices & services**. Most batteries are found automatically.

The [getting started guide](/guide/getting-started) walks through each step with screenshots, then shows you how to add the battery to the Energy dashboard and build a first automation.

> [!NOTE] Independent project
> This is a free, MIT-licensed community project. It is not made, endorsed, or supported by Marstek. Read the [limitations](/guide/limitations) before you rely on it for anything important.
