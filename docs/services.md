# Services

The integration exposes services for advanced control and automation.

> Tip: When automating control commands, prefer calling these services rather than trying to “poke” entity state.

All services target a device via `device_id` (select the Marstek device).

## `marstek.set_passive_mode`

Set passive mode with a target power and duration.

- `device_id` (required): target Marstek device
- `power` (required, W): negative = charge, positive = discharge
	- Input range is `-5000..5000`, but the effective limit is validated per device/model (and may depend on the **Socket limit** option).
- `duration` (optional, seconds): how long to keep passive mode active (default `3600`, range `0..86400`)

<img src="screenshots/automation-passive.png" alt="Passive automation" width="700" />

## `marstek.set_manual_schedule`

Configure one schedule slot (0–9).

- `device_id` (required): target Marstek device
- `schedule_slot` (optional): slot `0..9` (default `0`)
- `start_time` (required): start time
- `end_time` (required): end time
- `power` (required, W): negative = charge, positive = discharge
	- Input range is `-5000..5000`, but the effective limit is validated per device/model (and may depend on the **Socket limit** option).
- `days` (optional): list of weekday values (default all days)
	- Valid values: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`
- `enable` (optional): enable/disable this slot (default `true`)

<img src="screenshots/automation-manual-schedule-single.png" alt="Single schedule automation" width="700" />

## `marstek.set_manual_schedules`

Configure multiple schedules at once (YAML list).

- `device_id` (required): target Marstek device
- `schedules` (required): YAML list of schedule objects
	- Required per item: `schedule_slot`, `start_time`, `end_time`
	- Optional per item: `days`, `power`, `enable`
	- `start_time`/`end_time` must be strings in `HH:MM` format

### Example YAML (common patterns)

> Replace `YOUR_DEVICE_ID` with the device ID from the device selector in the UI.

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

<img src="screenshots/automation-manual-schedule-multiple.png" alt="Multiple schedules automation" width="700" />

## `marstek.clear_manual_schedules`

Clear all manual schedule slots.

- `device_id` (required): target Marstek device

Note: This clears all 10 slots sequentially (protocol limitation), so it may take a short while.

<img src="screenshots/automation-clear-manual-schedule.png" alt="Clear schedules automation" width="700" />

## `marstek.request_data_sync`

Trigger an immediate refresh.

- `device_id` (optional): when omitted, all Marstek devices are refreshed

## Device actions (automation UI)

Besides services, the integration also exposes **device actions** you can use in automations:

- Charge
- Discharge
- Stop

These actions use the per-device **Power settings** from [Options](options.md) and perform retries + verification.

## Notes on safety & responsiveness

- The integration avoids concurrent UDP bursts; control actions are designed to pause polling while sending commands.
- If your device becomes unresponsive, increase request delay/timeout in [Options](options.md).
