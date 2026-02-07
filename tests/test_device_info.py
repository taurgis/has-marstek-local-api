"""Tests for device info and binary sensor edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.marstek.binary_sensor import BINARY_SENSORS, MarstekBinarySensor
from custom_components.marstek.device_info import build_device_info, get_device_identifier


def test_get_device_identifier_requires_mac() -> None:
    """Missing MAC data should raise a ValueError."""
    with pytest.raises(ValueError, match="identifier"):
        get_device_identifier({})


def test_binary_sensor_returns_none_when_no_data() -> None:
    """Binary sensor should return None when coordinator has no data."""
    coordinator = MagicMock()
    coordinator.data = None
    coordinator.async_add_listener.return_value = lambda: None

    device_info = {"ble_mac": "AA:BB:CC:DD:EE:FF", "device_type": "Venus"}
    description = BINARY_SENSORS[0]

    entity = MarstekBinarySensor(coordinator, device_info, description)

    assert entity.is_on is None


def test_build_device_info_formats_device_name() -> None:
    """Device name should be short and exclude firmware version."""
    device_info = {
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "VenusA 3.0",
        "version": 147,
    }

    device = build_device_info(device_info)

    assert device["name"] == "Venus A (3.0)"
    assert device["manufacturer"] == "Marstek"
    assert device["sw_version"] == "147"
