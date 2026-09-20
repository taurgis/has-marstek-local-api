"""Mock Marstek device package."""

from .const import (
    BATTERY_CAPACITY_WH,
    DEFAULT_CONFIG,
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_IDLE,
)
from .device import MockMarstekDevice
from .simulators import BatterySimulator, HouseholdSimulator, WiFiSimulator

__all__ = [
    "BATTERY_CAPACITY_WH",
    "DEFAULT_CONFIG",
    "MODE_AI",
    "MODE_AUTO",
    "MODE_MANUAL",
    "MODE_PASSIVE",
    "STATUS_CHARGING",
    "STATUS_DISCHARGING",
    "STATUS_IDLE",
    "BatterySimulator",
    "HouseholdSimulator",
    "MockMarstekDevice",
    "WiFiSimulator",
]
