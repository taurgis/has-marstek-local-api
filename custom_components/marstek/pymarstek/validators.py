"""Request validation for pymarstek to protect devices from invalid commands.

Marstek devices are sensitive to malformed requests. This module provides
validation at multiple levels to ensure only well-formed commands are sent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import time as dt_time
from typing import Any, Final

from .const import (
    BLE_ADV_DISABLED,
    BLE_ADV_ENABLED,
    CMD_BATTERY_STATUS,
    CMD_BLE_ADV,
    CMD_DISCOVER,
    CMD_DOD_SET,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_LED_CTRL,
    CMD_PV_GET_STATUS,
    CMD_WIFI_STATUS,
    DOD_MAX_VALUE,
    DOD_MIN_VALUE,
    LED_OFF,
    LED_ON,
)

_LOGGER = logging.getLogger(__name__)

# Validation limits - keep in sync with device capabilities
MAX_POWER_VALUE: Final = 5000  # 5kW max (matches device specs)
MAX_DEVICE_ID: Final = 255
MAX_TIME_SLOTS: Final = 10  # Schedule slots 0-9
MAX_WEEK_SET: Final = 127  # 7 bits for 7 days (all days = 127)
MAX_PASSIVE_DURATION: Final = 86400  # 24 hours in seconds
# Control firmware stores JSON-RPC `id` as uint16 (`ldrh` in VNSE3-0
# json_data.c). Values above 65535 wrap on the device; 0 collides with
# parse-error replies. Generated IDs skip 0; validators still allow 0 so
# we can match truncated echoes.
MAX_JSON_RPC_ID: Final = 65535

# Strict mode thresholds - values beyond these trigger warnings
STRICT_POWER_WARN_THRESHOLD: Final = 4500  # Warn if power is >90% of max
STRICT_MIN_SCHEDULE_DURATION: Final = 5  # Warn if schedule duration < 5 minutes

# Global strict mode flag - set via enable_strict_mode()
_strict_mode: bool = False


def enable_strict_mode(enabled: bool = True) -> None:
    """Enable or disable strict validation mode.

    In strict mode, additional warnings are logged for:
    - Power values close to device limits (>90%)
    - Very short schedule durations (<5 minutes)
    - Potentially risky configurations

    Args:
        enabled: Whether to enable strict mode
    """
    global _strict_mode
    _strict_mode = enabled
    _LOGGER.info("Strict validation mode %s", "enabled" if enabled else "disabled")


def json_rpc_wire_id(value: Any) -> int | None:
    """Return the 16-bit JSON-RPC id Control firmware actually stores.

    Non-integers and bools (bool is a subclass of int) are not ids.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value & MAX_JSON_RPC_ID


def _strict_warn(message: str, field: str | None = None) -> None:
    """Log a strict mode warning if strict mode is enabled."""
    if _strict_mode:
        field_info = f" (field: {field})" if field else ""
        _LOGGER.warning("[STRICT] %s%s", message, field_info)


class ValidationError(Exception):
    """Raised when a request fails validation."""

    def __init__(self, message: str, field: str | None = None) -> None:
        """Initialize validation error.

        Args:
            message: Error description
            field: Optional field name that failed validation
        """
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class MethodSpec:
    """Specification for a valid API method."""

    method: str
    required_params: frozenset[str]
    optional_params: frozenset[str] = frozenset()
    is_write_command: bool = False  # True for commands that modify device state


# Define valid methods and their parameter requirements
VALID_METHODS: dict[str, MethodSpec] = {
    CMD_DISCOVER: MethodSpec(
        method=CMD_DISCOVER,
        required_params=frozenset(),
        optional_params=frozenset({"ble_mac"}),
    ),
    CMD_BATTERY_STATUS: MethodSpec(
        method=CMD_BATTERY_STATUS,
        required_params=frozenset(),
        optional_params=frozenset({"id"}),
    ),
    CMD_ES_STATUS: MethodSpec(
        method=CMD_ES_STATUS,
        required_params=frozenset(),
        optional_params=frozenset({"id"}),
    ),
    CMD_ES_MODE: MethodSpec(
        method=CMD_ES_MODE,
        required_params=frozenset(),
        optional_params=frozenset({"id"}),
    ),
    CMD_ES_SET_MODE: MethodSpec(
        method=CMD_ES_SET_MODE,
        required_params=frozenset({"id", "config"}),
        is_write_command=True,
    ),
    CMD_PV_GET_STATUS: MethodSpec(
        method=CMD_PV_GET_STATUS,
        required_params=frozenset(),
        optional_params=frozenset({"id"}),
    ),
    CMD_WIFI_STATUS: MethodSpec(
        method=CMD_WIFI_STATUS,
        required_params=frozenset(),
        optional_params=frozenset({"id"}),
    ),
    CMD_EM_STATUS: MethodSpec(
        method=CMD_EM_STATUS,
        required_params=frozenset(),
        optional_params=frozenset({"id"}),
    ),
    CMD_DOD_SET: MethodSpec(
        method=CMD_DOD_SET,
        required_params=frozenset({"value"}),
        is_write_command=True,
    ),
    CMD_BLE_ADV: MethodSpec(
        method=CMD_BLE_ADV,
        required_params=frozenset({"enable"}),
        is_write_command=True,
    ),
    CMD_LED_CTRL: MethodSpec(
        method=CMD_LED_CTRL,
        required_params=frozenset({"state"}),
        is_write_command=True,
    ),
}

# Valid operating modes (as expected by Marstek device API)
VALID_MODES: Final[frozenset[str]] = frozenset(
    {"Auto", "AI", "Manual", "Passive", "UPS"}
)

# Time format pattern HH:MM
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _parse_time_parts(value: str) -> tuple[int, int, int | None]:
    """Parse time string into hour, minute, and optional second.

    Accepts HH:MM or HH:MM:SS with optional zero padding. Raises
    ValidationError on invalid formats or ranges.
    """
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValidationError(
            f"time must be in HH:MM or HH:MM:SS format (got '{value}')",
            "time",
        )

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else None
    except ValueError as err:
        raise ValidationError(
            f"time must contain numeric values (got '{value}')",
            "time",
        ) from err

    if hour < 0 or hour > 23:
        raise ValidationError(f"hour must be 0-23 (got {hour})", "time")
    if minute < 0 or minute > 59:
        raise ValidationError(f"minute must be 0-59 (got {minute})", "time")
    if second is not None and (second < 0 or second > 59):
        raise ValidationError(f"second must be 0-59 (got {second})", "time")

    return hour, minute, second


def normalize_time_value(value: str | dt_time) -> str:
    """Normalize time values to HH:MM strings.

    Accepts datetime.time or a string in HH:MM or HH:MM:SS format.
    """
    if isinstance(value, dt_time):
        return value.strftime("%H:%M")

    if not isinstance(value, str):
        raise ValidationError(
            f"time must be a string or time object (got {type(value).__name__})",
            "time",
        )

    hour, minute, _second = _parse_time_parts(value)
    return f"{hour:02d}:{minute:02d}"


def _time_to_minutes(value: str | dt_time) -> int:
    """Convert time value to minutes since midnight."""
    normalized = normalize_time_value(value)
    hour_str, minute_str = normalized.split(":")
    return int(hour_str) * 60 + int(minute_str)


def validate_time_format(time_str: str, field_name: str = "time") -> None:
    """Validate time string is in HH:MM format.

    Args:
        time_str: Time string to validate
        field_name: Name of the field for error messages

    Raises:
        ValidationError: If format is invalid
    """
    if not isinstance(time_str, str):
        raise ValidationError(f"{field_name} must be a string", field_name)
    if not TIME_PATTERN.match(time_str):
        raise ValidationError(
            f"{field_name} must be in HH:MM format (got '{time_str}')", field_name
        )


def validate_time_range(
    start_time: str | dt_time, end_time: str | dt_time, *, allow_equal: bool = False
) -> None:
    """Validate that end_time is after start_time.

    Args:
        start_time: Start time in HH:MM format or datetime.time
        end_time: End time in HH:MM format or datetime.time
        allow_equal: If True, allow start_time == end_time

    Raises:
        ValidationError: If end_time is not after start_time
    """
    start_mins = _time_to_minutes(start_time)
    end_mins = _time_to_minutes(end_time)

    if allow_equal:
        if end_mins < start_mins:
            raise ValidationError(
                f"end_time ({end_time}) must be >= start_time ({start_time})",
                "end_time",
            )
    else:
        if end_mins <= start_mins:
            raise ValidationError(
                f"end_time ({end_time}) must be after start_time ({start_time})",
                "end_time",
            )


def validate_device_id(device_id: Any, field_name: str = "id") -> None:
    """Validate device ID is a valid integer.

    Args:
        device_id: Device ID to validate
        field_name: Name of the field for error messages

    Raises:
        ValidationError: If device_id is invalid
    """
    if not isinstance(device_id, int):
        raise ValidationError(
            f"{field_name} must be an integer (got {type(device_id).__name__})",
            field_name,
        )
    if device_id < 0 or device_id > MAX_DEVICE_ID:
        raise ValidationError(
            f"{field_name} must be between 0 and {MAX_DEVICE_ID} (got {device_id})",
            field_name,
        )


def _require_int(value: Any, field_name: str) -> int:
    """Require a non-boolean integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be an integer (got {type(value).__name__})",
            field_name,
        )
    return value


def validate_dod_value(value: Any, field_name: str = "value") -> None:
    """Validate depth-of-discharge as an integer in 30-88 inclusive."""
    dod = _require_int(value, field_name)
    if dod < DOD_MIN_VALUE or dod > DOD_MAX_VALUE:
        raise ValidationError(
            f"{field_name} must be between {DOD_MIN_VALUE} and {DOD_MAX_VALUE} "
            f"(got {dod})",
            field_name,
        )


def validate_sys_flag(
    value: Any,
    field_name: str,
    allowed: frozenset[int],
) -> None:
    """Validate a SYS 0/1 flag, rejecting booleans."""
    flag = _require_int(value, field_name)
    if flag not in allowed:
        allowed_text = ", ".join(str(item) for item in sorted(allowed))
        raise ValidationError(
            f"{field_name} must be one of {allowed_text} (got {flag})",
            field_name,
        )


def validate_power_value(power: Any, field_name: str = "power") -> None:
    """Validate power value is within reasonable range.

    Args:
        power: Power value in watts
        field_name: Name of the field for error messages

    Raises:
        ValidationError: If power value is invalid
    """
    if not isinstance(power, int):
        raise ValidationError(
            f"{field_name} must be an integer (got {type(power).__name__})",
            field_name,
        )
    if abs(power) > MAX_POWER_VALUE:
        raise ValidationError(
            f"{field_name} must be between -{MAX_POWER_VALUE} and {MAX_POWER_VALUE} (got {power})",
            field_name,
        )

    # Strict mode: warn about high power values
    if abs(power) > STRICT_POWER_WARN_THRESHOLD:
        _strict_warn(
            f"{field_name}={power}W is >90% of max ({MAX_POWER_VALUE}W) - verify this is intended",
            field_name,
        )


def validate_week_set(week_set: Any, field_name: str = "week_set") -> None:
    """Validate week_set bitmask.

    Args:
        week_set: Bitmask for days of week (0-127)
        field_name: Name of the field for error messages

    Raises:
        ValidationError: If week_set is invalid
    """
    if not isinstance(week_set, int):
        raise ValidationError(
            f"{field_name} must be an integer (got {type(week_set).__name__})",
            field_name,
        )
    if week_set < 0 or week_set > MAX_WEEK_SET:
        raise ValidationError(
            f"{field_name} must be between 0 and {MAX_WEEK_SET} (got {week_set})",
            field_name,
        )


def validate_manual_config(config: dict[str, Any]) -> None:
    """Validate manual mode configuration.

    Args:
        config: Manual configuration dictionary

    Raises:
        ValidationError: If configuration is invalid
    """
    required_fields = {"time_num", "start_time", "end_time", "week_set", "power", "enable"}

    # Check required fields
    missing = required_fields - set(config.keys())
    if missing:
        raise ValidationError(
            f"manual_cfg missing required fields: {', '.join(sorted(missing))}",
            "manual_cfg",
        )

    # Validate time_num (schedule slot)
    time_num = config.get("time_num")
    if not isinstance(time_num, int) or time_num < 0 or time_num >= MAX_TIME_SLOTS:
        raise ValidationError(
            f"time_num must be between 0 and {MAX_TIME_SLOTS - 1} (got {time_num})",
            "time_num",
        )

    # Validate times
    validate_time_format(config["start_time"], "start_time")
    validate_time_format(config["end_time"], "end_time")

    # Validate time range (end must be after start, unless slot is disabled)
    enable = config.get("enable")
    if enable == 1:  # Only validate range for enabled slots
        validate_time_range(config["start_time"], config["end_time"])

        # Strict mode: warn about very short schedules
        start_mins = _time_to_minutes(config["start_time"])
        end_mins = _time_to_minutes(config["end_time"])
        duration_mins = end_mins - start_mins
        if duration_mins < STRICT_MIN_SCHEDULE_DURATION:
            _strict_warn(
                "Schedule duration is only "
                f"{duration_mins} minutes - very short schedules may not be effective",
                "duration",
            )

    # Validate week_set
    validate_week_set(config["week_set"])

    # Validate power
    validate_power_value(config["power"])

    # Validate enable flag
    enable = config.get("enable")
    if enable not in (0, 1):
        raise ValidationError(
            f"enable must be 0 or 1 (got {enable})",
            "enable",
        )


def validate_ups_config(config: dict[str, Any]) -> None:
    """Validate UPS mode configuration.

    Args:
        config: UPS configuration dictionary

    Raises:
        ValidationError: If configuration is invalid
    """
    if "enable" not in config:
        raise ValidationError(
            "ups_cfg missing required field: enable",
            "enable",
        )

    enable = config.get("enable")
    if enable not in (0, 1):
        raise ValidationError(
            f"enable must be 0 or 1 (got {enable})",
            "enable",
        )


def validate_passive_config(config: dict[str, Any]) -> None:
    """Validate passive mode configuration.

    Args:
        config: Passive configuration dictionary

    Raises:
        ValidationError: If configuration is invalid
    """
    required_fields = {"power", "cd_time"}

    # Check required fields
    missing = required_fields - set(config.keys())
    if missing:
        raise ValidationError(
            f"passive_cfg missing required fields: {', '.join(sorted(missing))}",
            "passive_cfg",
        )

    # Validate power
    validate_power_value(config["power"])

    # Validate cd_time (countdown duration)
    cd_time = config.get("cd_time")
    if not isinstance(cd_time, int):
        raise ValidationError(
            f"cd_time must be an integer (got {type(cd_time).__name__})",
            "cd_time",
        )
    if cd_time < 0 or cd_time > MAX_PASSIVE_DURATION:
        raise ValidationError(
            f"cd_time must be between 0 and {MAX_PASSIVE_DURATION} seconds (got {cd_time})",
            "cd_time",
        )


def validate_es_set_mode_config(config: dict[str, Any]) -> None:
    """Validate ES.SetMode config parameter.

    Args:
        config: Mode configuration dictionary

    Raises:
        ValidationError: If configuration is invalid
    """
    if not isinstance(config, dict):
        raise ValidationError(
            f"config must be a dictionary (got {type(config).__name__})",
            "config",
        )

    # Validate mode
    mode = config.get("mode")
    if mode not in VALID_MODES:
        raise ValidationError(
            f"mode must be one of {sorted(VALID_MODES)} (got '{mode}')",
            "mode",
        )

    # Validate mode-specific configuration
    if mode == "Manual":
        manual_cfg = config.get("manual_cfg")
        if manual_cfg is None:
            raise ValidationError(
                "manual_cfg is required when mode is 'Manual'",
                "manual_cfg",
            )
        if not isinstance(manual_cfg, dict):
            raise ValidationError(
                f"manual_cfg must be a dictionary (got {type(manual_cfg).__name__})",
                "manual_cfg",
            )
        validate_manual_config(manual_cfg)

    elif mode == "Passive":
        passive_cfg = config.get("passive_cfg")
        if passive_cfg is None:
            raise ValidationError(
                "passive_cfg is required when mode is 'Passive'",
                "passive_cfg",
            )
        if not isinstance(passive_cfg, dict):
            raise ValidationError(
                f"passive_cfg must be a dictionary (got {type(passive_cfg).__name__})",
                "passive_cfg",
            )
        validate_passive_config(passive_cfg)

    elif mode == "UPS":
        ups_cfg = config.get("ups_cfg")
        if ups_cfg is None:
            raise ValidationError(
                "ups_cfg is required when mode is 'UPS'",
                "ups_cfg",
            )
        if not isinstance(ups_cfg, dict):
            raise ValidationError(
                f"ups_cfg must be a dictionary (got {type(ups_cfg).__name__})",
                "ups_cfg",
            )
        validate_ups_config(ups_cfg)


def validate_method(method: str) -> MethodSpec:
    """Validate that a method is known and allowed.

    Args:
        method: Method name to validate

    Returns:
        MethodSpec for the validated method

    Raises:
        ValidationError: If method is unknown
    """
    if not isinstance(method, str):
        raise ValidationError(
            f"method must be a string (got {type(method).__name__})",
            "method",
        )

    spec = VALID_METHODS.get(method)
    if spec is None:
        raise ValidationError(
            f"Unknown method '{method}'. Valid methods: {', '.join(sorted(VALID_METHODS.keys()))}",
            "method",
        )

    return spec


def validate_params(method: str, params: dict[str, Any]) -> None:
    """Validate parameters for a specific method.

    Args:
        method: The API method name
        params: Parameters dictionary to validate

    Raises:
        ValidationError: If parameters are invalid
    """
    spec = validate_method(method)

    if not isinstance(params, dict):
        raise ValidationError(
            f"params must be a dictionary (got {type(params).__name__})",
            "params",
        )

    # Check required parameters
    missing = spec.required_params - set(params.keys())
    if missing:
        raise ValidationError(
            f"Missing required parameters for {method}: {', '.join(sorted(missing))}",
            "params",
        )

    # Check for unknown parameters
    allowed = spec.required_params | spec.optional_params
    if allowed:  # Only check if there are defined parameters
        unknown = set(params.keys()) - allowed
        if unknown:
            raise ValidationError(
                f"Unknown parameters for {method}: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(allowed))}",
                "params",
            )

    # Validate common parameters
    if "id" in params:
        validate_device_id(params["id"])

    # Method-specific validation
    if method == CMD_ES_SET_MODE and "config" in params:
        validate_es_set_mode_config(params["config"])
    elif method == CMD_DOD_SET:
        validate_dod_value(params["value"])
    elif method == CMD_BLE_ADV:
        validate_sys_flag(
            params["enable"],
            "enable",
            frozenset({BLE_ADV_ENABLED, BLE_ADV_DISABLED}),
        )
    elif method == CMD_LED_CTRL:
        validate_sys_flag(params["state"], "state", frozenset({LED_OFF, LED_ON}))


def validate_command(command: dict[str, Any]) -> None:
    """Validate a complete command structure.

    Args:
        command: Command dictionary with id, method, params

    Raises:
        ValidationError: If command structure is invalid
    """
    if not isinstance(command, dict):
        raise ValidationError(
            f"command must be a dictionary (got {type(command).__name__})",
            "command",
        )

    # Validate required fields
    if "id" not in command:
        raise ValidationError("command missing required field 'id'", "id")
    if "method" not in command:
        raise ValidationError("command missing required field 'method'", "method")

    # Validate id
    request_id = command.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0:
        raise ValidationError(
            f"command id must be a non-negative integer (got {request_id})",
            "id",
        )
    if request_id > MAX_JSON_RPC_ID:
        raise ValidationError(
            f"command id must be <= {MAX_JSON_RPC_ID} (got {request_id}); "
            "Marstek Control firmware stores JSON-RPC ids as uint16",
            "id",
        )

    # Validate method and params
    method = command["method"]
    params = command.get("params", {})
    validate_params(method, params)


def validate_json_message(message: str) -> dict[str, Any]:
    """Validate and parse a JSON command message.

    Args:
        message: JSON string to validate

    Returns:
        Parsed command dictionary

    Raises:
        ValidationError: If message is invalid
    """
    if not isinstance(message, str):
        raise ValidationError(
            f"message must be a string (got {type(message).__name__})",
            "message",
        )

    if not message.strip():
        raise ValidationError("message cannot be empty", "message")

    # Limit message size (reasonable max for UDP)
    if len(message) > 65535:
        raise ValidationError(
            f"message too large ({len(message)} bytes, max 65535)",
            "message",
        )

    try:
        command = json.loads(message)
    except json.JSONDecodeError as err:
        raise ValidationError(f"Invalid JSON: {err}", "message") from err

    # Validate command structure
    validate_command(command)

    # After validation, we know it's a valid dict
    return dict(command)
