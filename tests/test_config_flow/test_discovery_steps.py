"""DHCP and integration discovery, and the confirm step."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from tests.conftest import (
    patch_manual_connection,
)

from ._helpers import (
    _get_schema_field_default,
    _pooled_udp_client,
)


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


async def test_dhcp_does_not_reset_custom_port(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test DHCP discovery updates IP but keeps an existing custom port."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "port": 30030},
    )

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
    updated_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated_entry.data["host"] == "1.2.3.5"
    assert updated_entry.data["port"] == 30030


async def test_dhcp_unchanged_ip(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test DHCP discovery with unchanged IP logs and ignores."""
    mock_config_entry.add_to_hass(hass)

    # Use simple NamedTuple-like object for DHCP discovery info
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",  # Same as mock_config_entry host
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",  # Same as mock_config_entry
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )

    # Should abort because IP hasn't changed
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_dhcp_no_existing_entry(hass: HomeAssistant) -> None:
    """Test DHCP discovery with no existing entry shows confirm step."""
    # Don't add any config entry - test line 365-366

    # Use simple NamedTuple-like object for DHCP discovery info
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )

    # Should show confirm form (no existing entry to update)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_dhcp_confirm_creates_entry(hass: HomeAssistant) -> None:
    """Test DHCP discovery confirm creates entry for new device."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"

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

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30000},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "192.168.1.100"
    assert format_mac(result["data"]["ble_mac"]) == "aa:bb:cc:dd:ee:ff"


async def test_dhcp_confirm_wifi_mac_keeps_ble_unique_id(
    hass: HomeAssistant,
) -> None:
    """DHCP often reports the Wi-Fi MAC; confirm still creates a BLE unique_id."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "112233445566",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"

    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30000},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"


async def test_dhcp_updates_ip_without_unique_id(hass: HomeAssistant) -> None:
    """Test DHCP discovery updates entries that lack unique_id but have MACs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "1.2.3.5",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == "1.2.3.5"


async def test_dhcp_wifi_mac_updates_ble_unique_id_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """DHCP uses the Wi-Fi MAC; entries identified by BLE MAC must still match."""
    mock_config_entry.add_to_hass(hass)

    discovery_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "1.2.3.9",
            "hostname": "marstek",
            "macaddress": "112233445566",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.data["host"] == "1.2.3.9"
    assert updated.unique_id == "aa:bb:cc:dd:ee:ff"


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


async def test_integration_discovery_updates_ip_and_metadata(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Host and firmware metadata land in one config-entry update."""
    mock_config_entry.add_to_hass(hass)

    discovery_info = {
        "ip": "1.2.3.99",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "VenusE 3.0",
        "version": 147,
        "wifi_name": "AirPort-38",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "VenusE 3.0",
        "firmware": "147",
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.data["host"] == "1.2.3.99"
    assert updated.data["device_type"] == "VenusE 3.0"
    assert updated.data["version"] == 147
    assert updated.data["wifi_name"] == "AirPort-38"


async def test_integration_discovery_without_port_keeps_current_port(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test integration discovery without port does not overwrite custom port."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "port": 30030},
    )

    discovery_info = {
        "ip": "1.2.3.99",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": 3,
        # No port key in payload
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated_entry.data["host"] == "1.2.3.99"
    assert updated_entry.data["port"] == 30030


async def test_integration_discovery_updates_port_same_ip(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test integration discovery updates existing entry port when IP is unchanged."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "port": 30000},
    )

    discovery_info = {
        "ip": "1.2.3.4",  # Same as existing entry
        "port": 30003,
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
    assert hass.config_entries.async_entries(DOMAIN)[0].data["port"] == 30003


async def test_integration_discovery_confirm_creates_entry(
    hass: HomeAssistant,
) -> None:
    """Test integration discovery confirm creates entry for new device."""
    discovery_info = {
        "ip": "192.168.1.101",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": 3,
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"

    device_info = {
        "ip": "192.168.1.101",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": "3.0",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "Venus",
        "firmware": "3.0",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.101", "port": 30000},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "192.168.1.101"


async def test_integration_discovery_missing_ble_mac(hass: HomeAssistant) -> None:
    """Test integration discovery aborts when ble_mac is missing."""
    discovery_data = {
        "ip": "192.168.1.100",
        # Missing ble_mac
        "device_type": "Venus",
        "version": "3.0",
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_data
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


@pytest.mark.parametrize("device_type", ["VenusE", "HMG-50", "Venus E2.0"])
async def test_integration_discovery_aborts_venus_e2(
    hass: HomeAssistant, device_type: str
) -> None:
    """Scanner discovery of HMG-50 / Venus E2 must abort, not confirm as Venus E."""
    discovery_info = {
        "ip": "172.28.0.29",
        "ble_mac": "02:de:ad:be:ef:09",
        "mac": "02:de:ad:be:ef:09",
        "device_type": device_type,
        "version": 153,
        "port": 30000,
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


async def test_integration_discovery_wifi_only_updates_existing_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Scanner discovery without BLE MAC still updates the matching entry."""
    mock_config_entry.add_to_hass(hass)

    discovery_info = {
        "ip": "1.2.3.99",
        "wifi_mac": "11:22:33:44:55:66",
        "device_type": "Venus C",
        "version": 153,
        "port": 30000,
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.data["host"] == "1.2.3.99"
    assert updated.unique_id == "aa:bb:cc:dd:ee:ff"


async def test_integration_discovery_wifi_only_confirms_new_device(
    hass: HomeAssistant,
) -> None:
    """A Wi-Fi-only GetDevice payload can start a confirm flow."""
    discovery_info = {
        "ip": "192.168.1.26",
        "wifi_mac": "DE:AD:BE:EF:00:01",
        "device_type": "Venus C",
        "version": 153,
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_confirm_unique_id_mismatch(hass: HomeAssistant) -> None:
    """Test confirm step errors when discovered MAC does not match device."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"

    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "11:22:33:44:55:66",
        "mac": "11:22:33:44:55:66",
        "device_type": "Venus",
        "version": "3.0",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"]["base"] == "unique_id_mismatch"


async def test_confirm_cannot_connect(hass: HomeAssistant) -> None:
    """Test confirm step shows cannot_connect error on failure."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection(device_info=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"]["base"] == "cannot_connect"


async def test_confirm_keeps_custom_port_after_failure(hass: HomeAssistant) -> None:
    """Test confirm form keeps submitted custom port after connection failure."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection(device_info=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30030},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"]["base"] == "cannot_connect"
    assert _get_schema_field_default(result, CONF_PORT) == 30030


async def test_confirm_invalid_discovery_info(hass: HomeAssistant) -> None:
    """Test confirm step errors when device info is missing MACs."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["step_id"] == "confirm"

    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": None,
        "mac": None,
        "wifi_mac": None,
        "device_type": "Venus",
        "version": "3.0",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"]["base"] == "invalid_discovery_info"


async def test_confirm_value_error(hass: HomeAssistant) -> None:
    """Test confirm step handles invalid responses (ValueError)."""
    dhcp_info = type(
        "DhcpInfo",
        (),
        {
            "ip": "192.168.1.100",
            "hostname": "marstek",
            "macaddress": "aabbccddeeff",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=dhcp_info
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection(error=ValueError("invalid")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.100", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"]["base"] == "invalid_discovery_info"


async def test_confirm_device_changed_endpoint_reuses_pooled_udp_client(
    hass: HomeAssistant,
) -> None:
    """Confirm device must reuse the pooled client when IP/port change."""
    client = _pooled_udp_client(hass)
    discovery_info = {
        "ip": "172.28.0.23",
        "ble_mac": "AA:BB:CC:DD:EE:04",
        "mac": "AA:BB:CC:DD:EE:04",
        "device_type": "VenusD",
        "version": 145,
        "port": 30002,
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=discovery_info
    )
    assert result["step_id"] == "confirm"

    device_info = {
        "ip": "172.28.0.20",
        "ble_mac": "AA:BB:CC:DD:EE:04",
        "mac": "AA:BB:CC:DD:EE:04",
        "device_type": "VenusD",
        "version": "145",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "VenusD",
        "firmware": "145",
    }

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new_callable=AsyncMock,
        return_value=device_info,
    ) as mock_get_device_info:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "172.28.0.20", "port": 30000},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "172.28.0.20"
    assert result["data"]["port"] == 30000
    mock_get_device_info.assert_awaited_once()
    assert mock_get_device_info.await_args is not None
    assert mock_get_device_info.await_args.kwargs["udp_client"] is client
    client.async_pause_receiver.assert_not_called()
    client.async_resume_receiver.assert_not_called()
