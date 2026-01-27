"""Constants for the Marstek integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "marstek"

PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
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

# Polling interval configuration (in seconds)
# Options keys
CONF_POLL_INTERVAL_FAST: Final = "poll_interval_fast"
CONF_POLL_INTERVAL_MEDIUM: Final = "poll_interval_medium"
CONF_POLL_INTERVAL_SLOW: Final = "poll_interval_slow"
CONF_REQUEST_DELAY: Final = "request_delay"
CONF_REQUEST_TIMEOUT: Final = "request_timeout"

# Default polling intervals
DEFAULT_POLL_INTERVAL_FAST: Final = 30  # Real-time power data (ES.GetMode, ES.GetStatus, EM.GetStatus)
DEFAULT_POLL_INTERVAL_MEDIUM: Final = 60  # PV data - changes with sun
DEFAULT_POLL_INTERVAL_SLOW: Final = 300  # WiFi and battery details - rarely change
DEFAULT_REQUEST_DELAY: Final = 10.0  # Delay between API requests during polling
DEFAULT_REQUEST_TIMEOUT: Final = 10.0  # Timeout for each API request

INITIAL_SETUP_REQUEST_DELAY: Final = 2.0  # Faster delay during first data fetch
