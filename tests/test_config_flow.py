"""Tests for Marstek config flow."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN

from tests.conftest import create_mock_client, patch_marstek_integration


@contextmanager
def _patch_discovery(devices: list[dict[str, Any]], error: Exception | None = None):
    """Context manager that patches local discover_devices function."""
    if error:
        with patch(
            "custom_components.marstek.config_flow.discover_devices",
            AsyncMock(side_effect=error),
        ):
            yield
    else:
        with patch(
            "custom_components.marstek.config_flow.discover_devices",
            AsyncMock(return_value=devices),
        ):
            yield


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """Test successful user flow with device selection."""
    devices = [
        {
            "ip": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
            "model": "Venus",
            "firmware": "3.0",
        }
    ]

    with _patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "0"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "1.2.3.4"
    assert format_mac(result["data"]["ble_mac"]) == "aa:bb:cc:dd:ee:ff"


async def test_user_flow_no_devices_redirects_to_manual(hass: HomeAssistant) -> None:
    """Test user flow redirects to manual entry when no devices found."""
    with _patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    # Should redirect to manual entry step when no devices found
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_user_flow_cannot_connect_redirects_to_manual(
    hass: HomeAssistant,
) -> None:
    """Test user flow redirects to manual entry when discovery fails with connection error."""
    with _patch_discovery([], error=OSError("cannot connect")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    # Should redirect to manual entry with error message
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "discovery_failed"}


async def test_user_flow_invalid_discovery_info(hass: HomeAssistant) -> None:
    """Test user flow when device has no BLE MAC (invalid discovery info)."""
    devices = [
        {
            "ip": "1.2.3.4",
            "ble_mac": None,  # missing BLE MAC
            "mac": None,  # also None to trigger invalid_discovery_info
            "wifi_mac": None,  # also None
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "model": "Venus",
            "firmware": "3.0",
        }
    ]

    with _patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "0"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_discovery_info"}


async def test_already_configured_unique_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that already-configured devices are filtered from selection."""
    mock_config_entry.add_to_hass(hass)

    devices = [
        {
            "ip": "1.2.3.99",  # different IP
            "ble_mac": "AA:BB:CC:DD:EE:FF",  # same BLE MAC as mock_config_entry
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        }
    ]

    with _patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    # All discovered devices are already configured, redirects to manual step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "all_devices_configured"}


async def test_mixed_configured_and_new_devices(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that only new devices are selectable when some are already configured."""
    mock_config_entry.add_to_hass(hass)

    devices = [
        {
            "ip": "1.2.3.99",  # Already configured device (same MAC)
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
        {
            "ip": "1.2.3.100",  # New device (different MAC)
            "ble_mac": "BB:CC:DD:EE:FF:00",
            "mac": "BB:CC:DD:EE:FF:00",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek2",
            "wifi_mac": "22:33:44:55:66:77",
            "model": "Venus",
            "firmware": "3.0",
        },
    ]

    with _patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # Only the new device (index 1) should be selectable
        # The configured device is filtered out but its index is preserved
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "1"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "1.2.3.100"
    assert format_mac(result["data"]["ble_mac"]) == "bb:cc:dd:ee:ff:00"


async def test_dhcp_updates_ip(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test DHCP discovery updates existing entry IP."""
    mock_config_entry.add_to_hass(hass)

    discovery_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "1.2.3.5",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == "1.2.3.5"


async def test_integration_discovery_updates_ip(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test integration discovery updates existing entry IP."""
    mock_config_entry.add_to_hass(hass)

    discovery_info = {
        "ip": "1.2.3.99",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": 3,
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == "1.2.3.99"


async def test_options_flow_creates_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test options flow works."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@contextmanager
def _patch_manual_connection(device_info: dict[str, Any] | None = None, error: Exception | None = None):
    """Context manager that patches get_device_info for manual connection validation."""
    if error:
        with patch(
            "custom_components.marstek.config_flow.get_device_info",
            AsyncMock(side_effect=error),
        ):
            yield
    else:
        with patch(
            "custom_components.marstek.config_flow.get_device_info",
            AsyncMock(return_value=device_info),
        ):
            yield


async def test_manual_flow_success(hass: HomeAssistant) -> None:
    """Test successful manual IP entry flow."""
    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": "3.0",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "Venus",
        "firmware": "3.0",
    }

    # First trigger discovery that finds no devices to get to manual step
    with _patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Now submit manual entry
    with _patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "192.168.1.100"
    assert result["data"]["port"] == 30000
    assert format_mac(result["data"]["ble_mac"]) == "aa:bb:cc:dd:ee:ff"


async def test_manual_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Test manual entry flow when device cannot be reached."""
    # First trigger discovery that finds no devices to get to manual step
    with _patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry that fails to connect
    with _patch_manual_connection(error=ConnectionError("cannot connect")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_flow_invalid_response(hass: HomeAssistant) -> None:
    """Test manual entry flow when device returns invalid response (no MAC)."""
    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": None,
        "mac": None,
        "wifi_mac": None,
        "device_type": "Venus",
        "version": "3.0",
    }

    # First trigger discovery that finds no devices to get to manual step
    with _patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry with invalid device info
    with _patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "invalid_discovery_info"}


async def test_manual_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test manual entry flow aborts when device already configured."""
    mock_config_entry.add_to_hass(hass)

    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "AA:BB:CC:DD:EE:FF",  # same as mock_config_entry
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": "3.0",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }

    # First trigger discovery that finds no devices to get to manual step
    with _patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry for already configured device
    with _patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
