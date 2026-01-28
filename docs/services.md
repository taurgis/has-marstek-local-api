# Services

The integration exposes services for advanced control and automation.

> Tip: When automating control commands, prefer calling these services rather than trying to “poke” entity state.

## `marstek.set_passive_mode`

Set passive mode with a target power and duration.

- `power` (W): negative = charge, positive = discharge (range typically -5000..5000)
- `duration` (seconds): default 3600

![Passive automation](screenshots/automation-passive.png)

## `marstek.set_manual_schedule`

Configure one schedule slot (0–9).

![Single schedule automation](screenshots/automation-manual-schedule-single.png)

## `marstek.set_manual_schedules`

Configure multiple schedules at once (YAML list).

![Multiple schedules automation](screenshots/automation-manual-schedule-multiple.png)

## `marstek.clear_manual_schedules`

Clear all manual schedule slots.

![Clear schedules automation](screenshots/automation-clear-manual-schedule.png)

## `marstek.request_data_sync`

Trigger an immediate refresh.

## Notes on safety & responsiveness

- The integration avoids concurrent UDP bursts; control actions are designed to pause polling while sending commands.
- If your device becomes unresponsive, increase request delay/timeout in [Options](options.md).
