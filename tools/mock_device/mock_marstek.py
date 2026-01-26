#!/usr/bin/env python3
"""Mock Marstek device for testing UDP discovery and communication.

This file is kept for backwards compatibility.
The implementation has been refactored into a proper Python package.

Response structures captured from real VenusE 3.0 device at 192.168.0.152.
Enhanced with realistic battery simulation including:
- Dynamic SOC changes based on power flow
- Power fluctuations
- Mode transitions (Auto, Manual, Passive, AI)
- Passive mode countdown timer
- Manual schedule simulation
- Realistic household power consumption patterns for Auto mode

Usage:
    python mock_marstek.py [OPTIONS]
    
    Or as a module:
    python -m mock_device [OPTIONS]
"""

import os
import sys

# Add the parent directory to path so imports work from any location
_this_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_this_dir)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

# Re-export everything for backwards compatibility
from mock_device.const import (
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
from mock_device.device import MockMarstekDevice
from mock_device.simulators import BatterySimulator, HouseholdSimulator, WiFiSimulator
from mock_device.utils import get_local_ip

# For CLI usage
def main():
    """Run the mock device CLI."""
    from mock_device.__main__ import main as _main
    _main()


if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
