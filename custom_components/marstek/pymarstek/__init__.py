"""Python library for Marstek energy storage communication."""

from .command_builder import (
    build_command,
    discover,
    get_battery_status,
    get_em_status,
    get_es_mode,
    get_es_status,
    get_pv_status,
    get_wifi_status,
    reset_request_id,
    set_es_mode_manual_charge,
    set_es_mode_manual_discharge,
)
from .data_parser import (
    merge_device_status,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
)
from .compatibility import CompatibilityMatrix
from .udp import MarstekUDPClient
from .validators import (
    MAX_PASSIVE_DURATION,
    MAX_POWER_VALUE,
    MAX_TIME_SLOTS,
    MAX_WEEK_SET,
    ValidationError,
    enable_strict_mode,
    is_strict_mode,
    validate_json_message,
)

__all__ = [
    "MAX_PASSIVE_DURATION",
    "MAX_POWER_VALUE",
    "MAX_TIME_SLOTS",
    "MAX_WEEK_SET",
    "CompatibilityMatrix",
    "MarstekUDPClient",
    "ValidationError",
    "build_command",
    "discover",
    "enable_strict_mode",
    "get_battery_status",
    "get_em_status",
    "get_es_mode",
    "get_es_status",
    "get_pv_status",
    "get_wifi_status",
    "is_strict_mode",
    "merge_device_status",
    "parse_es_mode_response",
    "parse_es_status_response",
    "parse_pv_status_response",
    "reset_request_id",
    "set_es_mode_manual_charge",
    "set_es_mode_manual_discharge",
    "validate_json_message",
]
