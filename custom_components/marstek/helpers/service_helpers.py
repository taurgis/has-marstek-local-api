"""Service helpers and schemas for Marstek integration."""

from __future__ import annotations

from datetime import time
from typing import Any

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from ..const import DOMAIN, WEEKDAY_MAP
from ..mode_config import build_manual_mode_config
from ..pymarstek import MAX_PASSIVE_DURATION, MAX_POWER_VALUE, MAX_TIME_SLOTS
from ..pymarstek.validators import (
    ValidationError,
)
from ..pymarstek.validators import (
    normalize_time_value as normalize_time_value_raw,
)
from ..pymarstek.validators import (
    validate_time_range as validate_time_range_raw,
)

ATTR_DEVICE_ID = "device_id"


def coerce_device_id(value: Any) -> str:
    """Accept a device ID string or a single-item list from selectors."""
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise vol.Invalid("device_id must contain exactly one device")
        value = value[0]
    return cv.string(value)


ATTR_POWER = "power"
ATTR_DURATION = "duration"
ATTR_SCHEDULE_SLOT = "schedule_slot"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"
ATTR_DAYS = "days"
ATTR_ENABLE = "enable"
ATTR_SCHEDULES = "schedules"

DEFAULT_SCHEDULE_DAYS: tuple[str, ...] = (
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
)

# Field validators shared by the service schemas below. Voluptuous validators
# are stateless, so one instance can serve every schema that needs the field.
_POWER_VALIDATOR = vol.All(
    vol.Coerce(int), vol.Range(min=-MAX_POWER_VALUE, max=MAX_POWER_VALUE)
)
_SCHEDULE_SLOT_VALIDATOR = vol.All(
    vol.Coerce(int), vol.Range(min=0, max=MAX_TIME_SLOTS - 1)
)
_DAYS_VALIDATOR = vol.All(cv.ensure_list, [vol.In(WEEKDAY_MAP.keys())])

SERVICE_SET_PASSIVE_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): coerce_device_id,
        vol.Required(ATTR_POWER): _POWER_VALIDATOR,
        vol.Optional(ATTR_DURATION, default=3600): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_PASSIVE_DURATION)
        ),
    }
)

SERVICE_SET_MANUAL_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): coerce_device_id,
        vol.Optional(ATTR_SCHEDULE_SLOT, default=0): _SCHEDULE_SLOT_VALIDATOR,
        vol.Required(ATTR_START_TIME): cv.time,
        vol.Required(ATTR_END_TIME): cv.time,
        vol.Required(ATTR_POWER): _POWER_VALIDATOR,
        vol.Optional(ATTR_DAYS, default=list(DEFAULT_SCHEDULE_DAYS)): _DAYS_VALIDATOR,
        vol.Optional(ATTR_ENABLE, default=True): cv.boolean,
    }
)

SCHEDULE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCHEDULE_SLOT): _SCHEDULE_SLOT_VALIDATOR,
        vol.Required(ATTR_START_TIME): cv.string,
        vol.Required(ATTR_END_TIME): cv.string,
        vol.Optional(ATTR_POWER, default=0): _POWER_VALIDATOR,
        vol.Optional(ATTR_DAYS, default=list(DEFAULT_SCHEDULE_DAYS)): _DAYS_VALIDATOR,
        vol.Optional(ATTR_ENABLE, default=True): cv.boolean,
    }
)

SERVICE_CLEAR_MANUAL_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): coerce_device_id,
    }
)

SERVICE_SET_MANUAL_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): coerce_device_id,
        vol.Required(ATTR_SCHEDULES): vol.All(
            cv.ensure_list,
            [SCHEDULE_ITEM_SCHEMA],
        ),
    }
)

SERVICE_REQUEST_DATA_SYNC_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): coerce_device_id,
    }
)


def calculate_week_set(days: list[str]) -> int:
    """Calculate week_set bitmask from list of day names."""
    week_set = 0
    for day in days:
        day_lower = day.lower()
        if day_lower in WEEKDAY_MAP:
            week_set |= WEEKDAY_MAP[day_lower]
    return week_set


def normalize_time_value(value: time | str, field_name: str) -> str:
    """Normalize a time value to HH:MM, raising a HA-friendly error."""
    try:
        return normalize_time_value_raw(value)
    except ValidationError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_time_format",
            translation_placeholders={"field": field_name, "value": str(value)},
        ) from err


def validate_time_range(start: str, end: str) -> None:
    """Validate that end time is after start time."""
    try:
        validate_time_range_raw(start, end)
    except ValidationError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_time_range",
            translation_placeholders={"start": start, "end": end},
        ) from err


def build_manual_schedule_config(
    *,
    schedule_slot: int,
    start_time_raw: time | str,
    end_time_raw: time | str,
    power: int,
    days: list[str],
    enable: bool,
) -> tuple[dict[str, Any], str, str]:
    """Build manual schedule config and normalized time strings."""
    start_time_str = normalize_time_value(start_time_raw, ATTR_START_TIME)
    end_time_str = normalize_time_value(end_time_raw, ATTR_END_TIME)
    validate_time_range(start_time_str, end_time_str)

    week_set = calculate_week_set(days)
    config = build_manual_mode_config(
        power=power,
        enable=enable,
        time_num=schedule_slot,
        start_time=start_time_str,
        end_time=end_time_str,
        week_set=week_set,
    )
    return config, start_time_str, end_time_str
