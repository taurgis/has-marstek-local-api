"""Constants for the Marstek integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "marstek"
DATA_UDP_CLIENT: Final = "udp_client"  # Key for shared UDP client in hass.data
DATA_SUPPRESS_RELOADS: Final = "suppress_reload_entry_ids"  # Set of entry_ids to skip reload

PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
]

# UDP Configuration
DEFAULT_UDP_PORT: Final = 30000  # Default UDP port for Marstek devices
DISCOVERY_TIMEOUT: Final = 10.0  # Wait 10s for each broadcast

# Commands
CMD_ES_SET_MODE: Final = "ES.SetMode"
CMD_ES_GET_MODE: Final = "ES.GetMode"

# Operating modes (lowercase for translation key compatibility)
MODE_AUTO: Final = "auto"
MODE_AI: Final = "ai"
MODE_MANUAL: Final = "manual"
MODE_PASSIVE: Final = "passive"

OPERATING_MODES: Final[list[str]] = [
    MODE_AUTO,
    MODE_AI,
    MODE_MANUAL,
    MODE_PASSIVE,
]

# API mode values (as expected by Marstek device)
API_MODE_AUTO: Final = "Auto"
API_MODE_AI: Final = "AI"
API_MODE_MANUAL: Final = "Manual"
API_MODE_PASSIVE: Final = "Passive"

# Mapping from HA modes to API modes
MODE_TO_API: Final[dict[str, str]] = {
    MODE_AUTO: API_MODE_AUTO,
    MODE_AI: API_MODE_AI,
    MODE_MANUAL: API_MODE_MANUAL,
    MODE_PASSIVE: API_MODE_PASSIVE,
}

# Mapping from API modes to HA modes
API_TO_MODE: Final[dict[str, str]] = {
    API_MODE_AUTO: MODE_AUTO,
    API_MODE_AI: MODE_AI,
    API_MODE_MANUAL: MODE_MANUAL,
    API_MODE_PASSIVE: MODE_PASSIVE,
}

# Weekday bitmask mapping for manual schedules
# mon=1, tue=2, wed=4, thu=8, fri=16, sat=32, sun=64
WEEKDAY_MAP: Final[dict[str, int]] = {
    "mon": 1,
    "tue": 2,
    "wed": 4,
    "thu": 8,
    "fri": 16,
    "sat": 32,
    "sun": 64,
}
WEEKDAYS_ALL: Final = 127  # All days enabled

# Power limits (in watts)
MAX_CHARGE_POWER: Final = -5000  # Negative for charging
MAX_DISCHARGE_POWER: Final = 5000  # Positive for discharging
SOCKET_LIMIT_POWER: Final = 800  # Plug socket limit (typical NL/BE)

# Polling interval configuration (in seconds)
# Options keys
CONF_POLL_INTERVAL_FAST: Final = "poll_interval_fast"
CONF_POLL_INTERVAL_MEDIUM: Final = "poll_interval_medium"
CONF_POLL_INTERVAL_SLOW: Final = "poll_interval_slow"
CONF_PARALLEL_API_REQUESTS: Final = "parallel_api_requests"
CONF_REQUEST_DELAY: Final = "request_delay"
CONF_REQUEST_TIMEOUT: Final = "request_timeout"
CONF_FAILURE_THRESHOLD: Final = "failure_threshold"
CONF_ACTION_CHARGE_POWER: Final = "action_charge_power"
CONF_ACTION_DISCHARGE_POWER: Final = "action_discharge_power"
CONF_SOCKET_LIMIT: Final = "socket_limit"

# Default polling intervals
# Real-time power data (ES.GetMode, ES.GetStatus, EM.GetStatus)
DEFAULT_POLL_INTERVAL_FAST: Final = 30
DEFAULT_POLL_INTERVAL_MEDIUM: Final = 60  # PV data - changes with sun
DEFAULT_POLL_INTERVAL_SLOW: Final = 300  # WiFi and battery details - rarely change
DEFAULT_PARALLEL_API_REQUESTS: Final = False
DEFAULT_REQUEST_DELAY: Final = 5.0  # Delay between API requests during polling
DEFAULT_REQUEST_TIMEOUT: Final = 10.0  # Timeout for each API request
DEFAULT_FAILURE_THRESHOLD: Final = 3  # Failures before entities become unavailable
DEFAULT_ACTION_CHARGE_POWER: Final = -1300  # W (negative for charging)
DEFAULT_ACTION_DISCHARGE_POWER: Final = 800  # W (positive for discharging)
DEFAULT_SOCKET_LIMIT: Final = False

INITIAL_SETUP_REQUEST_DELAY: Final = 2.0  # Faster delay during first data fetch

# Device capability detection
# PV component is supported by Venus A and Venus D; Venus C/E do NOT.
# Device names from API: "VenusA", "VenusD", "VenusE 3.0", etc.
_DEVICE_PV_SUPPORT_TOKENS: Final[frozenset[str]] = frozenset({
    "venusa",
    "venusd",
})

# Device power limits (AC charge/discharge) in watts per model
# Values are maximum absolute power in either direction unless socket limit is enabled.
_DEVICE_POWER_LIMITS: Final[dict[str, int]] = {
    "venusa": 1200,
    "venusc": 2500,
    "venusd": 2200,
    "venuse": 2500,
}

_DEVICE_SOCKET_LIMIT_DEFAULTS: Final[frozenset[str]] = frozenset({
    "venusc",
    "venusd",
    "venuse",
})


def _normalize_device_type(device_type: str | None) -> str:
    """Normalize device type for matching (lowercase, alnum only)."""
    if not device_type:
        return ""
    return "".join(ch for ch in device_type if ch.isalnum()).lower()


def device_default_socket_limit(device_type: str | None) -> bool:
    """Get default socket limit setting for a device type."""
    normalized = _normalize_device_type(device_type)
    return any(token in normalized for token in _DEVICE_SOCKET_LIMIT_DEFAULTS)


def device_supports_pv(device_type: str | None) -> bool:
    """Check if a device type supports PV (solar) components.

    Venus A and Venus D support PV. Venus C/E do NOT have PV component support.

    Args:
        device_type: Device type string (e.g., "VenusE 3.0", "VenusD")

    Returns:
        True if device supports PV, False otherwise
    """
    normalized = _normalize_device_type(device_type)
    return any(token in normalized for token in _DEVICE_PV_SUPPORT_TOKENS)


def get_device_power_limits(
    device_type: str | None,
    *,
    socket_limit: bool = False,
) -> tuple[int, int]:
    """Get per-device power limits for charge/discharge.

    Returns:
        (min_charge_power, max_discharge_power)
    """
    normalized = _normalize_device_type(device_type)
    max_abs = None
    for token, limit in _DEVICE_POWER_LIMITS.items():
        if token and token in normalized:
            max_abs = limit
            break

    if max_abs is None:
        max_abs = MAX_DISCHARGE_POWER

    min_charge_power = -max_abs
    max_discharge_power = SOCKET_LIMIT_POWER if socket_limit else max_abs
    return min_charge_power, max_discharge_power
