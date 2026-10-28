---
title: Getting started
description: Install the Marstek integration with HACS, add your battery to Home Assistant, put it on the Energy dashboard, and build a first automation.
---

# Getting started

By the end of this page your battery will show up in Home Assistant, feed the Energy dashboard, and run a first automation. Plan on about 20 minutes, including one Home Assistant restart.

Before you begin, go through [Before you start](before-you-start.md). Most setup problems come from Open API being off.

## Step 1: Install the integration

The integration is installed through **HACS**, the Home Assistant Community Store. HACS is a free add-on that installs community integrations and keeps them updated. If you don't have it yet, follow the [HACS installation guide](https://hacs.xyz/docs/use/) first.

This integration is not in the HACS default list, so you add it as a *custom repository*. That only tells HACS where to download it from.

### The quick way

If your Home Assistant is set up for [My Home Assistant](https://my.home-assistant.io/) links, select this button. It opens HACS on the right page:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=taurgis&repository=has-marstek-local-api&category=integration)

Select **Download**, then continue at [restart Home Assistant](#restart-home-assistant).

### Step by step

1. In Home Assistant, open **HACS** from the sidebar.
2. Select the three-dot menu in the top-right corner, then **Custom repositories**.
3. In **Repository**, paste:

   ```text
   https://github.com/taurgis/has-marstek-local-api
   ```

4. In **Type**, select **Integration**, then select **Add**.
5. Close the dialog. Search HACS for **Marstek** and open it.
6. Select **Download**, and confirm.

### Restart Home Assistant

Home Assistant only loads new integrations at startup. Go to **Settings** > **System**, select the power icon in the top-right corner, and select **Restart Home Assistant**.

> [!TIP] No HACS?
> You can copy the `custom_components/marstek` folder from the [repository](https://github.com/taurgis/has-marstek-local-api) into your Home Assistant `config/custom_components/` folder and restart. You will have to repeat that for every update. See [Installation](../installation.md).

## Step 2: Add your battery

[![Open your Home Assistant instance and start setting up the Marstek integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=marstek)

Or by hand:

1. Go to **Settings** > **Devices & services**.
2. Select **Add integration** and search for **Marstek**.
3. Home Assistant searches your network. Pick your battery from the list.

<img src="../screenshots/device-discovery.png" alt="Home Assistant shows a Marstek battery discovered on the local network." width="340" />

**Battery not in the list?** Home Assistant asks for the **IP address** and **port** instead. Type the battery's IP address and the port from the Marstek app (usually 30000). Discovery fails on VLANs and some mesh Wi-Fi systems, so typing the address is normal and works the same way afterwards.

Still nothing? Open [troubleshooting: device not discovered](../troubleshooting.md#device-not-discovered).

> [!TIP] More than one battery?
> Repeat this step for each battery. Every battery becomes its own entry.

## Step 3: Look around the device page

Open **Settings** > **Devices & services** > **Marstek** and select your battery. You'll see something like this:

<img src="../screenshots/device-details.png" alt="Marstek device page in Home Assistant with battery sensors and controls." width="560" />

What you are looking at:

- **Battery level** is the state of charge, in percent.
- **Battery power** is how fast the battery charges or discharges, in watts.
- **Operating mode** is a dropdown. **Auto** is self-consumption: the battery follows your grid meter to keep grid import near zero.
- **Grid** sensors show import and export at the grid meter (when a CT meter is connected).
- Solar (**PV**) sensors appear on Venus A and Venus D.

Some diagnostic sensors, such as **Battery temperature**, start disabled on purpose. The request behind them has been linked to battery resets on some firmware. Turn them on later if you want them. See [Entities](../entities.md).

Readings refresh every 30 seconds. That is normal. The battery doesn't push changes by itself.

## Step 4: Add it to the Energy dashboard

1. Go to **Settings** > **Dashboards** > **Energy**.
2. Under **Electricity grid**, add **Total grid input energy** as grid consumption and **Total grid output energy** as return to grid.
3. On Venus A and Venus D, add **Total solar energy** under **Solar panels**.

The Energy dashboard updates once an hour, so give it a while. The values are in watt-hours, which Home Assistant converts for you. Details and the reasoning are on [Energy dashboard](../energy_dashboard.md).

> [!WARNING]
> Leave **Total load energy** out of the Energy dashboard. What it measures differs between models and firmware.

## Step 5: Build a first automation

A common first automation charges the battery while power is cheap. This one charges at 1500 W from 02:00 for one hour every night, then hands control back to the battery's **Auto** mode.

The simplest tool for this is **passive mode**. It tells the battery "charge (or discharge) at this power for this many seconds". When the time runs out, passive mode ends on its own.

1. Go to **Settings** > **Automations & scenes** and select **Create automation** > **Create new automation**.
2. Select the three-dot menu > **Edit in YAML** and paste:

   ```yaml
   alias: Charge the battery at night
   triggers:
     - trigger: time
       at: "02:00:00"
   actions:
     - action: marstek.set_passive_mode
       data:
         device_id: "<your battery's device ID>"
         power: -1500    # negative = charge, positive = discharge
         duration: 3600  # seconds
     - delay: "01:00:05"
     - action: select.select_option
       target:
         entity_id: select.marstek_operating_mode  # check the name on your device page
       data:
         option: auto  # the stored value is lowercase
   ```

3. Select **Edit in visual editor**. On the **Set passive mode** action, pick your battery so Home Assistant fills in the device ID. On the last action, pick your battery's **Operating mode** entity.
4. Save.

The last step switches the battery back to **Auto** (self-consumption), so it doesn't sit idle after the hour.

### Or with the device actions

The automation editor also has **Charge battery**, **Discharge battery**, and **Stop charging/discharging** as device actions. They are handy, with one thing to know: **Charge** and **Discharge** put the battery in Manual mode with an all-day schedule, and they keep going until you send **Stop**. **Stop** leaves the battery idle in Manual mode. Set **Operating mode** back to **Auto** afterwards if you want self-consumption again.

<img src="../screenshots/automation-action.png" alt="Automation editor with a Marstek device action to charge, discharge, or stop the battery." width="520" />

More recipes, including weekly schedules for time-of-use tariffs, are in [Automations and services](../services.md).

> [!NOTE]
> Venus E mini firmware 301 refuses passive mode. Use the device actions or manual schedules on that model.

## You're done

The battery is in Home Assistant, on the Energy dashboard, and under automation control. Good next steps:

- Tune polling and default power under **Configure** on the battery. See [Options](../options.md).
- Turn on the [Battery health](../usable-capacity.md) dashboard to track capacity over time.
- Read [Limitations](limitations.md) so nothing surprises you later.
