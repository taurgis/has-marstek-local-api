"""Tests for device info and binary sensor edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.marstek.binary_sensor import BINARY_SENSORS, MarstekBinarySensor
from custom_components.marstek.device_info import get_device_identifier


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
