# Services

The integration exposes services for advanced control and automation.

> Tip: When automating control commands, prefer calling these services rather than trying to “poke” entity state.

All services target a device via `device_id` (select the Marstek device in the UI). The only exception is `marstek.request_data_sync`, where `device_id` is optional and omitting it refreshes every configured device.

Home Assistant device IDs are **32-character hex** strings. Quote them in YAML so they stay strings:

```yaml
action: marstek.set_passive_mode
data:
  device_id: "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
  power: -500
  duration: 300
```

If you are pasting YAML, prefer the device picker in Developer Tools, or pass the battery entity ID / MAC instead of copying a truncated ID from a template.

Services are registered when Home Assistant starts and remain available even if
no Marstek devices are currently loaded. If no matching device or config entry
is available, the service call will return an error.

## How modes are set

| Mode | How to set it |
|------|----------------|
| Auto, AI | Operating-mode select |
| UPS | Operating-mode select, only when the firmware profile allows it (`ver >= 150` on ES-capable families, including Venus E mini) |
| Manual, Passive | Parameterized services below (the select does not apply empty defaults) |

`Set.Ver` and factory reset are **not** services, entities, or recommended actions.

Schedule slot numbers are profile-specific:

| Family | Valid `schedule_slot` |
|--------|----------------------|
| Venus A / C / D / E | `0–9` |
| Venus E mini | `0–5` (slot `6` and above is rejected) |

The service schema accepts `0–9` in the UI; the integration then validates against the device's current firmware profile.

## `marstek.set_passive_mode`

Set passive mode with a target power and duration.

- `device_id` (required): target Marstek device
- `power` (required, W): negative = charge, positive = discharge
	- Input range is `-5000..5000`, but the effective limit is validated per device/model (and may depend on the **Socket limit** option).
- `duration` (optional, seconds): how long to keep passive mode active (default `3600`, range `0..86400`)

<img src="screenshots/automation-passive.png" alt="Passive automation" width="520" />

## `marstek.set_manual_schedule`

Configure one schedule slot.

- `device_id` (required): target Marstek device
- `schedule_slot` (optional): slot index (default `0`; max `9` on Venus A/C/D/E, max `5` on Venus E mini)
- `start_time` (required): start time
- `end_time` (required): end time
- `power` (required, W): negative = charge, positive = discharge
	- Input range is `-5000..5000`, but the effective limit is validated per device/model (and may depend on the **Socket limit** option).
- `days` (optional): list of weekday values (default all days)
	- Valid values: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`
- `enable` (optional): enable/disable this slot (default `true`)

<img src="screenshots/automation-manual-schedule-single.png" alt="Single schedule automation" width="520" />

## `marstek.set_manual_schedules`

Configure multiple schedules at once (YAML list).

- `device_id` (required): target Marstek device
- `schedules` (required): YAML list of schedule objects
	- Required per item: `schedule_slot`, `start_time`, `end_time`
	- Optional per item: `days`, `power`, `enable`
	- `start_time`/`end_time` must be strings in `HH:MM` format

### Example YAML (common patterns)

> Replace `YOUR_DEVICE_ID` with the device ID from the device selector in the UI.

#### What to paste into the **`schedules`** input field

In the Home Assistant service UI, the `schedules` field expects **just the YAML list**. Example:

```yaml
- schedule_slot: 0
	start_time: "10:00"
	end_time: "15:30"
	days: [mon, tue, wed, thu, fri]
	power: -2000
	enable: true
- schedule_slot: 1
	start_time: "18:00"
	end_time: "22:30"
	days: [mon, tue, wed, thu, fri]
	power: 1200
	enable: true
```

#### 1) Weekday daytime charging (solar/top-up) + evening discharge (peak shaving)

```yaml
service: marstek.set_manual_schedules
data:
	device_id: YOUR_DEVICE_ID
	schedules:
		- schedule_slot: 0
			start_time: "10:00"
			end_time: "15:30"
			days: [mon, tue, wed, thu, fri]
			power: -2000
			enable: true
		- schedule_slot: 1
			start_time: "18:00"
			end_time: "22:30"
			days: [mon, tue, wed, thu, fri]
			power: 1200
			enable: true
```

#### 2) Nighttime charging (cheap tariff window)

```yaml
service: marstek.set_manual_schedules
data:
	device_id: YOUR_DEVICE_ID
	schedules:
		- schedule_slot: 0
			start_time: "00:30"
			end_time: "05:30"
			days: [mon, tue, wed, thu, fri, sat, sun]
			power: -2500
			enable: true
```

#### 3) Weekend-only discharge (self-consumption boost)

```yaml
service: marstek.set_manual_schedules
data:
	device_id: YOUR_DEVICE_ID
	schedules:
		- schedule_slot: 2
			start_time: "09:00"
			end_time: "12:00"
			days: [sat, sun]
			power: 800
			enable: true
```

#### 4) Disable a slot without changing its stored times

```yaml
service: marstek.set_manual_schedules
data:
	device_id: YOUR_DEVICE_ID
	schedules:
		- schedule_slot: 1
			start_time: "18:00"
			end_time: "22:30"
			enable: false
```

#### 5) “Workday profile” with three time windows (morning charge, daytime idle, evening discharge)

```yaml
service: marstek.set_manual_schedules
data:
	device_id: YOUR_DEVICE_ID
	schedules:
		- schedule_slot: 0
			start_time: "06:00"
			end_time: "08:00"
			days: [mon, tue, wed, thu, fri]
			power: -1500
			enable: true
		- schedule_slot: 1
			start_time: "12:00"
			end_time: "13:00"
			days: [mon, tue, wed, thu, fri]
			power: 0
			enable: true
		- schedule_slot: 2
			start_time: "18:00"
			end_time: "23:00"
			days: [mon, tue, wed, thu, fri]
			power: 1000
			enable: true
```

<img src="screenshots/automation-manual-schedule-multiple.png" alt="Multiple schedules automation" width="520" />

## `marstek.clear_manual_schedules`

Clear all manual schedule slots.

- `device_id` (required): target Marstek device

Note: This clears every slot the current firmware profile allows (10 slots on Venus A/C/D/E, 6 on Venus E mini) sequentially, so it may take a short while.

<img src="screenshots/automation-clear-manual-schedule.png" alt="Clear schedules automation" width="520" />

## `marstek.request_data_sync`

Trigger an immediate refresh.

- `device_id` (optional): when omitted, all Marstek devices are refreshed

---

## Device Actions

The integration provides **device actions** for use in automations. These appear in the automation editor when you select "Device" as the action type and choose your Marstek battery.

<img src="screenshots/automation-action.png" alt="Device action in automation" width="520" />

### Charge battery

Starts charging the battery at the specified power.

| Parameter | Required | Description |
|-----------|----------|-------------|
| Power (W) | No | Charge power in watts (0–5000). If omitted, uses the **Default charge power** from device options. |

### Discharge battery

Starts discharging the battery at the specified power.

| Parameter | Required | Description |
|-----------|----------|-------------|
| Power (W) | No | Discharge power in watts (0–5000). If omitted, uses the **Default discharge power** from device options. |

### Stop charging/discharging

Immediately stops any active charge or discharge operation. No parameters required.

### Power validation

All device actions respect the **Socket limit** setting configured in [Options](options.md). If socket limit is enabled (the default for Venus C, Venus D, Venus E and Venus E mini), discharge power above 800 W is rejected. With it disabled, the cap is the per-family maximum listed in [Options](options.md#power--behavior).

### Technical details

- Actions use **Manual mode** internally with a 24-hour schedule (00:00–23:59, all days)
- Commands include retry logic (up to 8 attempts) with exponential backoff
- Verification confirms the device responded correctly before completing
- Polling is paused during command execution to avoid UDP traffic conflicts

---

## Notes on safety & responsiveness

- By default, the integration avoids concurrent UDP bursts; control actions are designed to pause polling while sending commands.
- If **Parallel API requests** is enabled in [Options](options.md), status polling intentionally uses concurrent requests and may be less stable on Wi-Fi.
- If your device becomes unresponsive, increase request delay/timeout in [Options](options.md).
