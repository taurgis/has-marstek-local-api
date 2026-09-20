"""The user step: discovery results and entry creation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from tests.conftest import (
    patch_discovery,
)

from ._helpers import (
    _pooled_udp_client,
)


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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "0"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "1.2.3.4"
    assert format_mac(result["data"]["ble_mac"]) == "aa:bb:cc:dd:ee:ff"


async def test_user_flow_can_switch_to_manual_with_discovered_devices(
    hass: HomeAssistant,
) -> None:
    """Test user flow offers manual entry path even when devices are discovered."""
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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"device": "__manual__"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_user_flow_discovery_probes_multiple_ports(hass: HomeAssistant) -> None:
    """Test initial user discovery probes default and common custom ports."""
    with patch(
        "custom_components.marstek.config_flow.discover_devices",
        AsyncMock(return_value=[]),
    ) as mock_discover:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert mock_discover.call_args is not None
    assert mock_discover.call_args.kwargs["ports"] == [
        30000,
        30001,
        30002,
        30003,
        30004,
        30030,
    ]


async def test_user_flow_uses_discovered_custom_port(hass: HomeAssistant) -> None:
    """Test user flow stores discovered custom port when creating an entry."""
    devices = [
        {
            "ip": "1.2.3.4",
            "port": 30003,
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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "0"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "1.2.3.4"
    assert result["data"]["port"] == 30003


async def test_user_flow_form_snapshot(hass: HomeAssistant, snapshot) -> None:
    """Test user flow form structure snapshot."""
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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    schema = result["data_schema"].schema
    device_key, validator = next(iter(schema.items()))
    options = dict(getattr(validator, "container", {}))

    snapshot_data = {
        "type": result["type"],
        "step_id": result["step_id"],
        "errors": result.get("errors"),
        "description_placeholders": result.get("description_placeholders"),
        "fields": {str(device_key): options},
    }

    assert snapshot_data == snapshot


async def test_user_flow_no_devices_redirects_to_manual(hass: HomeAssistant) -> None:
    """Test user flow redirects to manual entry when no devices found."""
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    # Should redirect to manual entry step when no devices found
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_user_flow_cannot_connect_redirects_to_manual(
    hass: HomeAssistant,
) -> None:
    """Test user flow redirects to manual entry when discovery fails with connection error."""
    with patch_discovery([], error=OSError("cannot connect")):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "0"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_discovery_info"}


async def test_user_flow_connection_error_redirects_to_manual(
    hass: HomeAssistant,
) -> None:
    """Test user flow redirects to manual when ConnectionError occurs."""
    with patch_discovery([], error=ConnectionError("Network unreachable")):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    # Should redirect to manual entry with cannot_connect error
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("device_type", ["Venus E2.0", "VenusE", "HMG-50"])
async def test_user_flow_filters_unsupported_venus_e2(
    hass: HomeAssistant, device_type: str
) -> None:
    """Discovery lists omit HMG-50 / Venus E2 so it cannot be added as Venus E."""
    devices = [
        {
            "ip": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "wifi_mac": "11:22:33:44:55:66",
            "device_type": device_type,
            "version": 153,
            "wifi_name": "marstek",
            "model": device_type,
            "firmware": "153",
        }
    ]

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

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

    with patch_discovery(devices):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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


async def test_user_discovery_pauses_udp_receivers(hass: HomeAssistant) -> None:
    """Add Integration discovery must pause pooled listeners on each scan port."""
    client = _pooled_udp_client(hass)

    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["step_id"] == "manual"
    client.async_pause_receiver.assert_awaited()
    client.async_resume_receiver.assert_awaited()
