"""Constants for the Marstek integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

from .firmware_profile import DeviceFamily, FirmwareProfile, resolve_firmware_profile

DOMAIN: Final = "marstek"
DATA_UDP_CLIENTS: Final = "udp_clients"  # dict[int, MarstekUDPClient] keyed by bind port
DATA_UDP_CLIENTS_LOCK: Final = "udp_clients_lock"
DATA_DISCOVERY_LOCK: Final = "discovery_lock"
DATA_UDP_CLIENT_OWNERS: Final = "udp_client_owners"  # bind_port -> entry ids
DATA_ENTRY_BIND_PORTS: Final = "entry_bind_ports"  # entry id -> leased bind port
DATA_SUPPRESS_RELOADS: Final = "suppress_reload_entry_ids"  # Set of entry_ids to skip reload

PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
]

# Entity keys backed by Wifi.GetStatus. The coordinator only sends the
# request while at least one of these entities is enabled.
WIFI_STATUS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "wifi_rssi",
        "wifi_sta_ip",
        "wifi_sta_gate",
        "wifi_sta_mask",
        "wifi_sta_dns",
    }
)

# Entity keys backed by Bat.GetStatus. The coordinator only sends the
# request while at least one of these entities is enabled, because the
# call is suspected to trigger device resets on some firmwares (issue #14).
BAT_STATUS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "bat_temp",
        "bat_capacity",
        "bat_rated_capacity",
        "bat_charg_flag",
        "bat_dischrg_flag",
    }
)

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
MODE_UPS: Final = "ups"

SELECTABLE_BASE_MODES: Final[list[str]] = [
    MODE_AUTO,
    MODE_AI,
    MODE_MANUAL,
    MODE_PASSIVE,
]

# Complete recognized operating-mode vocabulary (parsing and enum translation)
OPERATING_MODES: Final[list[str]] = [
    *SELECTABLE_BASE_MODES,
    MODE_UPS,
]

# API mode values (as expected by Marstek device)
API_MODE_AUTO: Final = "Auto"
API_MODE_AI: Final = "AI"
API_MODE_MANUAL: Final = "Manual"
API_MODE_PASSIVE: Final = "Passive"
API_MODE_UPS: Final = "UPS"

# Mapping from HA modes to API modes
MODE_TO_API: Final[dict[str, str]] = {
    MODE_AUTO: API_MODE_AUTO,
    MODE_AI: API_MODE_AI,
    MODE_MANUAL: API_MODE_MANUAL,
    MODE_PASSIVE: API_MODE_PASSIVE,
    MODE_UPS: API_MODE_UPS,
}

# Mapping from API modes to HA modes
API_TO_MODE: Final[dict[str, str]] = {
    API_MODE_AUTO: MODE_AUTO,
    API_MODE_AI: MODE_AI,
    API_MODE_MANUAL: MODE_MANUAL,
    API_MODE_PASSIVE: MODE_PASSIVE,
    API_MODE_UPS: MODE_UPS,
}

# Integer ES.GetMode values used by some Rev 3.1 firmwares / vendor libraries
WIRE_INT_MODE_TO_HA: Final[dict[int, str]] = {
    0: MODE_AUTO,
    1: MODE_AI,
    2: MODE_MANUAL,
    3: MODE_PASSIVE,
    4: MODE_UPS,
}


def normalize_operating_mode(raw: object) -> str | None:
    """Map an ES.GetMode wire value to a Home Assistant operating-mode key.

    The Open API documents string names and this integration still *sends*
    those strings on ``ES.SetMode``. Integer codes ``0-4`` are accepted on
    *read* only so firmwares that speak the vendor library encoding still
    show a mode. Unknown strings keep being lowercased instead of rejected.
    Booleans are ignored because ``bool`` is a subclass of ``int``.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return WIRE_INT_MODE_TO_HA.get(raw)
    if isinstance(raw, str) and raw:
        folded = raw.casefold()
        for api_name, ha_name in API_TO_MODE.items():
            if api_name.casefold() == folded:
                return ha_name
        if folded in MODE_TO_API:
            return folded
        if raw.isascii() and raw.isdecimal():
            return WIRE_INT_MODE_TO_HA.get(int(raw, 10))
        return raw.lower()
    return None


def ha_operating_mode(raw: object) -> str | None:
    """Return a Core enum option for a device-reported mode, or None.

    Open API wire values use PascalCase (``Auto``). Home Assistant 2026.9
    rejects enum sensor and select states that are not in ``options``, so map
    known names onto ``OPERATING_MODES`` and drop unknown values. Older Core
    still accepts the same lowercase options.
    """
    mode = normalize_operating_mode(raw)
    if mode is None or mode not in OPERATING_MODES:
        return None
    return mode


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

# Device power limits (AC charge/discharge) in watts per family.
# Values are maximum absolute power in either direction unless socket limit is enabled.
# Use DeviceFamily so "VenusE" / "Venus E2.0" cannot inherit Venus E 3.x limits
# via substring match (``venuse`` in ``venuse20``).
_FAMILY_POWER_LIMITS: Final[dict[DeviceFamily, int]] = {
    DeviceFamily.VENUS_A: 1500,
    DeviceFamily.VENUS_C: 2500,
    DeviceFamily.VENUS_D: 2200,
    DeviceFamily.VENUS_E: 2500,
    DeviceFamily.VENUS_E_MINI: 2500,
}

_FAMILY_SOCKET_LIMIT_DEFAULTS: Final[frozenset[DeviceFamily]] = frozenset(
    {
        DeviceFamily.VENUS_C,
        DeviceFamily.VENUS_D,
        DeviceFamily.VENUS_E,
        DeviceFamily.VENUS_E_MINI,
    }
)


def device_default_socket_limit(device_type: str | None) -> bool:
    """Get default socket limit setting for a device type."""
    family = resolve_firmware_profile(device_type, None).family
    return family in _FAMILY_SOCKET_LIMIT_DEFAULTS


def device_supports_pv(device_type: str | None) -> bool:
    """Return PV support through the canonical firmware profile."""
    return resolve_firmware_profile(device_type, None).supports_pv


def selectable_operating_modes(profile: FirmwareProfile) -> list[str]:
    """Return operating-mode select options authorized by the firmware profile."""
    if profile.supports_ups:
        return list(OPERATING_MODES)
    return list(SELECTABLE_BASE_MODES)


def get_device_power_limits(
    device_type: str | None,
    *,
    socket_limit: bool = False,
) -> tuple[int, int]:
    """Get per-device power limits for charge/discharge.

    Returns:
        (min_charge_power, max_discharge_power)
    """
    family = resolve_firmware_profile(device_type, None).family
    max_abs = _FAMILY_POWER_LIMITS.get(family, MAX_DISCHARGE_POWER)

    min_charge_power = -max_abs
    max_discharge_power = SOCKET_LIMIT_POWER if socket_limit else max_abs
    return min_charge_power, max_discharge_power
