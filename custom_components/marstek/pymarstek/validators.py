"""Request validation for pymarstek to protect devices from invalid commands.

Marstek devices are sensitive to malformed requests. This module provides
validation at multiple levels to ensure only well-formed commands are sent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

from .const import (
    CMD_BATTERY_STATUS,
    CMD_DISCOVER,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
    CMD_WIFI_STATUS,
)

# Validation limits - keep in sync with device capabilities
MAX_POWER_VALUE: Final = 5000  # 5kW max (matches device specs)
MAX_DEVICE_ID: Final = 255
MAX_TIME_SLOTS: Final = 10  # Schedule slots 0-9
MAX_WEEK_SET: Final = 127  # 7 bits for 7 days (all days = 127)
MAX_PASSIVE_DURATION: Final = 86400  # 24 hours in seconds

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
    global _strict_mode  # noqa: PLW0603
    _strict_mode = enabled
    _LOGGER.info("Strict validation mode %s", "enabled" if enabled else "disabled")


def is_strict_mode() -> bool:
    """Check if strict validation mode is enabled."""
    return _strict_mode


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
}

# Valid operating modes (as expected by Marstek device API)
VALID_MODES: Final[frozenset[str]] = frozenset({"Auto", "AI", "Manual", "Passive"})

# Time format pattern HH:MM
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _time_to_minutes(time_str: str) -> int:
    """Convert HH:MM time string to minutes since midnight."""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


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
    start_time: str, end_time: str, *, allow_equal: bool = False
) -> None:
    """Validate that end_time is after start_time.
    
    Args:
        start_time: Start time in HH:MM format (assumed already validated)
        end_time: End time in HH:MM format (assumed already validated)
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
                f"Schedule duration is only {duration_mins} minutes - very short schedules may not be effective",
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
    if not isinstance(request_id, int) or request_id < 0:
        raise ValidationError(
            f"command id must be a non-negative integer (got {request_id})",
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
    
    return command
