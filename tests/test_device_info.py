"""Tests for device info and binary sensor edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
)

from custom_components.marstek.binary_sensor import MarstekBinarySensor
from custom_components.marstek.device_info import (
    build_device_info,
    get_device_identifier,
    stale_access_point_connections,
)
from custom_components.marstek.helpers.binary_sensor_descriptions import BINARY_SENSORS


def test_get_device_identifier_requires_mac() -> None:
    """Missing MAC data should raise a ValueError."""
    with pytest.raises(ValueError, match="identifier"):
        get_device_identifier({})
    with pytest.raises(ValueError, match="identifier"):
        get_device_identifier({"ble_mac": "test-no-ble-mac"})


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


def test_build_device_info_registers_only_ble_mac() -> None:
    """``wifi_mac`` is the access point's BSSID, so it is not this device's MAC."""
    device = build_device_info(
        {
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "wifi_mac": "11:22:33:44:55:66",
            "mac": "11:22:33:44:55:66",
            "device_type": "VenusA 3.0",
        }
    )

    assert device["connections"] == {(CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff")}
    assert device["serial_number"] == "aa:bb:cc:dd:ee:ff"


def test_build_device_info_falls_back_to_network_mac_without_ble() -> None:
    """Without a BLE MAC the reported MACs are all Home Assistant can match on."""
    device = build_device_info(
        {"wifi_mac": "11:22:33:44:55:66", "mac": "11:22:33:44:55:66", "device_type": "Venus"}
    )

    assert device["connections"] == {(CONNECTION_NETWORK_MAC, "11:22:33:44:55:66")}


def test_stale_access_point_connections() -> None:
    """Only the reported BSSIDs are stale; other network MACs stay."""
    info = {"ble_mac": "AA:BB:CC:DD:EE:FF", "wifi_mac": "11:22:33:44:55:66", "mac": ""}
    registered = {
        (CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff"),
        (CONNECTION_NETWORK_MAC, "11:22:33:44:55:66"),
        (CONNECTION_NETWORK_MAC, "77:88:99:aa:bb:cc"),
    }

    assert stale_access_point_connections(info, registered) == {
        (CONNECTION_NETWORK_MAC, "11:22:33:44:55:66")
    }
    # Without a BLE MAC the network MAC is the device's identity; keep it.
    assert stale_access_point_connections({"wifi_mac": "11:22:33:44:55:66"}, registered) == set()


def test_build_device_info_skips_unusable_macs() -> None:
    """Placeholder MAC values must not become device registry connections."""
    device = build_device_info(
        {
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "wifi_mac": "",
            "mac": "not-a-mac",
            "device_type": "Venus",
        }
    )

    assert device["connections"] == {(CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff")}
