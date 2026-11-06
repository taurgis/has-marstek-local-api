# Lock Screen and Dynamic Island

A [Live Activity](https://companion.home-assistant.io/docs/notifications/live-activities/) (iPhone Lock Screen and Dynamic Island) and an Android Live Update are notifications from the Home Assistant Companion app. This integration does not send them. An automation calls your phone's `notify.mobile_app_<your_device_id_here>` action with `live_update: true` and a stable `tag`. The same tag updates the activity in place. `clear_notification` with that tag ends it.

The examples below use this integration's sensors. Replace every `<battery>` with the entity-id prefix from the device name, including the one inside `tag`. A Venus E 3.0 is named **Venus E (3.0)** and uses the prefix `venus_e_3_0`, so Battery level is `sensor.venus_e_3_0_battery_level`. A device you renamed uses that name instead. Read **Entity ID** on the entity before pasting.

| Entity | Entity ID | Use it for |
|---|---|---|
| **Charging** | `binary_sensor.<battery>_charging` | On while battery power is −50 W or lower |
| **Battery level** | `sensor.<battery>_battery_level` | State of charge, percent |
| **Battery power** | `sensor.<battery>_battery_power` | Watts. Negative charges, positive discharges |
| **Battery status** | `sensor.<battery>_battery_status` | `charging`, `discharging`, or `idle` |
| **Battery low** | `binary_sensor.<battery>_battery_low` | On at the low-battery trip level |

Find the phone's notify action under **Settings → Companion app → Server & devices** in the Companion app. There is one per server. You can paste a payload into **Developer tools → Actions** before you build the automation.

## What you need

| Requirement | Detail |
|---|---|
| Home Assistant | Core 2026.7.0 or newer |
| iPhone | iOS 17.2 or newer. The Lock Screen card is the activity. The Dynamic Island is iPhone 14 Pro and later |
| Companion app (iOS) | 2026.8 or newer. The [2026.7 release](https://github.com/home-assistant/iOS/releases/tag/release%2F2026.7.0%2F2026.2208) kept Live Activities on TestFlight |
| Android | Android 16 or newer. The same payload is a Live Update, pinned to the shade, the Lock Screen, and the always-on display |

The Companion requirements box also lists iPad on iOS 17.2. The same page says Live Activities are not available on iPad, which is an Apple limit, so plan on an iPhone.

Allow Live Activities in iOS **Settings → Home Assistant**. While the feature is in beta, **Settings → Live Activities** in the Companion app shows whether it is enabled. The phone and Home Assistant exchange a token the first time. That fails when the phone cannot reach Home Assistant, so an activity does not start or update until the connection is stable. Away from home, that means a working remote URL.

On Android, `title` has to be set or the phone shows an ordinary banner. On Samsung phones, **Live notifications for all apps** in developer options is what makes the status-bar chip show.

> [!WARNING] iOS limits how often these update
> Each update is a push. iOS delays or drops updates it considers too frequent, and it also limits how often a *new* activity can start. Starting and ending one over and over while you test spends that budget. After that, the automation still succeeds, Home Assistant logs nothing, and the phone stays quiet. The budget comes back on its own, typically over minutes to hours. Rebooting the phone does not reset it.
>
> An activity also expires after [up to 8 hours](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities#Understand-constraints). A charge that runs longer than that disappears until the next update starts it again.
>
> Do not trigger on every change of **Battery power**. It changes on every poll (30 seconds by default). The charging example runs when **Charging** turns on or off, and when **Battery level** crosses a 5% band. The discharge example uses **Battery power** only as a threshold (above or below 50 W), not as a value that is pushed on every reading.

`title` is fixed when the activity starts. Later updates do not change it. `silent: true` (iOS) and `alert_once: true` (Android) keep the later updates quiet. They do not silence the first one. `progress` and `progress_max` draw the bar. The bar is the state of charge out of 100.

## Charging progress

This starts when **Charging** turns on, refreshes when **Battery level** enters a new 5% band (0–4, 5–9, 10–14, and so on), and clears when **Charging** turns off. A jump from 41% to 46% still updates, because those readings are in different bands. A move from 41% to 44% does not. If charging is already on when you turn the automation on, the next band change starts the activity.

The message shows charging power as a positive number of watts. The sensor itself is negative while charging.

```yaml
alias: Marstek charging on the Lock Screen
description: Live Activity while the battery charges. Updates about every 5%.
mode: queued
max: 10
triggers:
  - trigger: state
    id: started
    entity_id: binary_sensor.<battery>_charging
    to: "on"
    not_from: "on"
  - trigger: state
    id: level
    entity_id: sensor.<battery>_battery_level
  - trigger: state
    id: stopped
    entity_id: binary_sensor.<battery>_charging
    from: "on"
    to: "off"
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: stopped
        sequence:
          - action: notify.mobile_app_<your_device_id_here>
            data:
              message: clear_notification
              data:
                tag: marstek_<battery>_charging
      - conditions:
          - condition: trigger
            id: level
          - condition: state
            entity_id: binary_sensor.<battery>_charging
            state: "on"
          - condition: template
            value_template: >-
              {{ trigger.from_state is not none
                 and trigger.to_state.state | is_number
                 and trigger.from_state.state | is_number
                 and (trigger.to_state.state | int // 5)
                    != (trigger.from_state.state | int // 5) }}
        sequence:
          - action: notify.mobile_app_<your_device_id_here>
            data:
              title: Marstek
              message: >-
                Charging at {{ states('sensor.<battery>_battery_power') | float(0) | abs | round(0) }} W
                · {{ states('sensor.<battery>_battery_level') | int(0) }}%
              data:
                tag: marstek_<battery>_charging
                live_update: true
                silent: true
                alert_once: true
                critical_text: "{{ states('sensor.<battery>_battery_level') | int(0) }}%"
                progress: "{{ states('sensor.<battery>_battery_level') | int(0) }}"
                progress_max: 100
                notification_icon: "mdi:battery-charging"
                notification_icon_color: "#4CAF50"
                color: "#4CAF50"
      - conditions:
          - condition: trigger
            id: started
        sequence:
          - action: notify.mobile_app_<your_device_id_here>
            data:
              title: Marstek
              message: >-
                Charging at {{ states('sensor.<battery>_battery_power') | float(0) | abs | round(0) }} W
                · {{ states('sensor.<battery>_battery_level') | int(0) }}%
              data:
                tag: marstek_<battery>_charging
                live_update: true
                critical_text: "{{ states('sensor.<battery>_battery_level') | int(0) }}%"
                progress: "{{ states('sensor.<battery>_battery_level') | int(0) }}"
                progress_max: 100
                notification_icon: "mdi:battery-charging"
                notification_icon_color: "#4CAF50"
                color: "#4CAF50"
```

A brief dropout does not clear it: **Charging** has to go to `off`. Coming back to `on` sends the start payload again, which refreshes the same tag.

## Discharge session

There is no discharging binary sensor. **Battery status** becomes `discharging` as soon as power is above 0 W, so idle noise would start and end the activity and spend the iOS start budget. **Charging** only turns on at −50 W or below, for the same reason. This example waits until **Battery power** rises above 50 W, updates on the same 5% bands, and clears when power falls below 50 W. The bar shrinks as the state of charge falls, because `progress` is that percentage.

If power hovers around 50 W, add `for: "00:01:00"` to the start and stop triggers so a short spike does not count.

```yaml
alias: Marstek discharge on the Lock Screen
description: Live Activity while the battery discharges. Updates about every 5%.
mode: queued
max: 10
triggers:
  - trigger: numeric_state
    id: started
    entity_id: sensor.<battery>_battery_power
    above: 50
  - trigger: state
    id: level
    entity_id: sensor.<battery>_battery_level
  - trigger: numeric_state
    id: stopped
    entity_id: sensor.<battery>_battery_power
    below: 50
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: stopped
        sequence:
          - action: notify.mobile_app_<your_device_id_here>
            data:
              message: clear_notification
              data:
                tag: marstek_<battery>_discharging
      - conditions:
          - condition: trigger
            id: level
          - condition: numeric_state
            entity_id: sensor.<battery>_battery_power
            above: 50
          - condition: template
            value_template: >-
              {{ trigger.from_state is not none
                 and trigger.to_state.state | is_number
                 and trigger.from_state.state | is_number
                 and (trigger.to_state.state | int // 5)
                    != (trigger.from_state.state | int // 5) }}
        sequence:
          - action: notify.mobile_app_<your_device_id_here>
            data:
              title: Marstek
              message: >-
                Discharging at {{ states('sensor.<battery>_battery_power') | float(0) | abs | round(0) }} W
                · {{ states('sensor.<battery>_battery_level') | int(0) }}%
              data:
                tag: marstek_<battery>_discharging
                live_update: true
                silent: true
                alert_once: true
                critical_text: "{{ states('sensor.<battery>_battery_level') | int(0) }}%"
                progress: "{{ states('sensor.<battery>_battery_level') | int(0) }}"
                progress_max: 100
                notification_icon: "mdi:battery-arrow-down"
                notification_icon_color: "#FF9800"
                color: "#FF9800"
      - conditions:
          - condition: trigger
            id: started
        sequence:
          - action: notify.mobile_app_<your_device_id_here>
            data:
              title: Marstek
              message: >-
                Discharging at {{ states('sensor.<battery>_battery_power') | float(0) | abs | round(0) }} W
                · {{ states('sensor.<battery>_battery_level') | int(0) }}%
              data:
                tag: marstek_<battery>_discharging
                live_update: true
                critical_text: "{{ states('sensor.<battery>_battery_level') | int(0) }}%"
                progress: "{{ states('sensor.<battery>_battery_level') | int(0) }}"
                progress_max: 100
                notification_icon: "mdi:battery-arrow-down"
                notification_icon_color: "#FF9800"
                color: "#FF9800"
```

**Battery low** is a normal notification, not a progress session. The paste-ready automation is in [Battery automations](services.md#battery-automations). The two activities above use different tags, so a charge and a later discharge do not clear each other. Two batteries need two tags, which is why the device prefix is part of `tag`.

More fields (a countdown, colors, tap behavior) are in the [Companion Live Activities docs](https://companion.home-assistant.io/docs/notifications/live-activities/).
