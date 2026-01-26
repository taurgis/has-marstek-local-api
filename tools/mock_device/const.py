"""Constants for mock Marstek device."""

# Default mock device configuration
# Using clearly fake MAC addresses (locally administered range: 02:xx:xx:xx:xx:xx)
# These should NEVER match real device MACs
DEFAULT_CONFIG = {
    "device": "VenusE 3.0",
    "ver": 145,
    "ble_mac": "02deadbeef01",  # Mock BLE MAC - device 1
    "wifi_mac": "02cafebabe01",  # Mock WiFi MAC - device 1
    "wifi_name": "MockNetwork",
}

# Mock MAC address prefixes for multi-device testing
# Format: {prefix}{device_number:02d} e.g. 02deadbeef01, 02deadbeef02
MOCK_BLE_PREFIX = "02deadbeef"  # + 2-digit hex device number
MOCK_WIFI_PREFIX = "02cafebabe"  # + 2-digit hex device number

# Battery capacity in Wh (typical for Venus 3.0 is ~5120Wh)
BATTERY_CAPACITY_WH = 5120

# Mode constants
MODE_AUTO = "Auto"
MODE_AI = "AI"
MODE_MANUAL = "Manual"
MODE_PASSIVE = "Passive"

# Battery status labels
STATUS_CHARGING = "Buying"
STATUS_DISCHARGING = "Selling"
STATUS_IDLE = "Idle"

# Default simulation settings
DEFAULT_MAX_CHARGE_POWER = 3000
DEFAULT_MAX_DISCHARGE_POWER = 3000
DEFAULT_POWER_FLUCTUATION_PCT = 5  # ±5%
DEFAULT_UPDATE_INTERVAL = 1.0  # seconds
DEFAULT_UDP_PORT = 30000

# SOC limits
SOC_MIN_DISCHARGE = 5  # Don't discharge below this
SOC_TAPER_DISCHARGE = 10  # Start tapering discharge below this
SOC_TAPER_CHARGE = 90  # Start tapering charge above this
SOC_RESERVE = 10  # Reserve for auto mode
