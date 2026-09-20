"""Tests for Marstek config flow."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SOCKET_LIMIT,
    DATA_UDP_CLIENTS,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PARALLEL_API_REQUESTS,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DOMAIN,
)
from custom_components.marstek.helpers.flow_schemas import build_manual_entry_schema
from tests.conftest import (
    create_mock_client,
    patch_discovery,
    patch_manual_connection,
    patch_marstek_integration,
)


@pytest.fixture(autouse=True)
async def _drain_config_entry_tasks(hass: HomeAssistant) -> AsyncIterator[None]:
    """Finish create/reload tasks before HA 2026.9 lingering-timer checks."""
    yield
    await hass.async_block_till_done()


def _get_schema_field_default(result: dict[str, Any], field_name: str) -> Any:
    """Extract default value for a field in a flow form schema."""
    schema = result["data_schema"].schema
    key = next(key for key in schema if getattr(key, "schema", None) == field_name)
    default = getattr(key, "default", vol.UNDEFINED)
    if default is vol.UNDEFINED:
        return None
    return default() if callable(default) else default


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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"device": "0"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "1.2.3.4"
    assert result["data"]["port"] == 30003


async def test_user_flow_form_snapshot(
    hass: HomeAssistant, snapshot
) -> None:
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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    # Should redirect to manual entry step when no devices found
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_manual_flow_form_snapshot(
    hass: HomeAssistant, snapshot
) -> None:
    """Test manual entry form structure snapshot."""
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    schema = result["data_schema"].schema
    fields: dict[str, dict[str, object]] = {}
    for key in schema:
        default = getattr(key, "default", None)
        if default is vol.UNDEFINED:
            default = None
        fields[str(key)] = {
            "required": getattr(key, "required", False),
            "default": default,
        }

    snapshot_data = {
        "type": result["type"],
        "step_id": result["step_id"],
        "errors": result.get("errors"),
        "fields": fields,
    }

    assert snapshot_data == snapshot


async def test_user_flow_cannot_connect_redirects_to_manual(
    hass: HomeAssistant,
) -> None:
    """Test user flow redirects to manual entry when discovery fails with connection error."""
    with patch_discovery([], error=OSError("cannot connect")):
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

    with patch_discovery(devices):
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

    with patch_discovery(devices):
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

    with patch_discovery(devices):
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

    # Options flow uses sections - provide nested structure
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "polling_settings": {
                CONF_POLL_INTERVAL_FAST: DEFAULT_POLL_INTERVAL_FAST,
                CONF_POLL_INTERVAL_MEDIUM: DEFAULT_POLL_INTERVAL_MEDIUM,
                CONF_POLL_INTERVAL_SLOW: DEFAULT_POLL_INTERVAL_SLOW,
            },
            "network_settings": {
                CONF_PARALLEL_API_REQUESTS: DEFAULT_PARALLEL_API_REQUESTS,
                CONF_REQUEST_DELAY: DEFAULT_REQUEST_DELAY,
                CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
                CONF_FAILURE_THRESHOLD: DEFAULT_FAILURE_THRESHOLD,
            },
            "power_settings": {
                CONF_ACTION_CHARGE_POWER: DEFAULT_ACTION_CHARGE_POWER,
                CONF_ACTION_DISCHARGE_POWER: DEFAULT_ACTION_DISCHARGE_POWER,
                CONF_SOCKET_LIMIT: False,
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify data is flattened when stored
    assert result["data"][CONF_POLL_INTERVAL_FAST] == DEFAULT_POLL_INTERVAL_FAST
    assert result["data"][CONF_SOCKET_LIMIT] is False


async def test_options_flow_socket_limit_default_by_model(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test socket limit default is enabled for Venus C/D/E models."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus C",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    # Schema now uses sections - find power_settings section and check socket_limit default
    schema = result["data_schema"].schema
    power_section_key = next(
        key for key in schema if getattr(key, "schema", None) == "power_settings"
    )
    # The section value contains a schema - extract it
    power_schema = schema[power_section_key].schema.schema
    socket_key = next(
        key for key in power_schema if getattr(key, "schema", None) == CONF_SOCKET_LIMIT
    )
    assert socket_key.default() is True


async def test_options_flow_keeps_request_delay_visible_when_parallel_enabled(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test request_delay field remains visible even when parallel mode is enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_PARALLEL_API_REQUESTS: True,
            CONF_REQUEST_DELAY: 7.5,
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    schema = result["data_schema"].schema
    network_section_key = next(
        key for key in schema if getattr(key, "schema", None) == "network_settings"
    )
    network_schema = schema[network_section_key].schema.schema
    network_field_names = {
        getattr(key, "schema", None)
        for key in network_schema
    }
    assert CONF_REQUEST_DELAY in network_field_names
    assert CONF_PARALLEL_API_REQUESTS in network_field_names

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "polling_settings": {
                CONF_POLL_INTERVAL_FAST: DEFAULT_POLL_INTERVAL_FAST,
                CONF_POLL_INTERVAL_MEDIUM: DEFAULT_POLL_INTERVAL_MEDIUM,
                CONF_POLL_INTERVAL_SLOW: DEFAULT_POLL_INTERVAL_SLOW,
            },
            "network_settings": {
                CONF_PARALLEL_API_REQUESTS: True,
                CONF_REQUEST_DELAY: 7.5,
                CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
                CONF_FAILURE_THRESHOLD: DEFAULT_FAILURE_THRESHOLD,
            },
            "power_settings": {
                CONF_ACTION_CHARGE_POWER: DEFAULT_ACTION_CHARGE_POWER,
                CONF_ACTION_DISCHARGE_POWER: DEFAULT_ACTION_DISCHARGE_POWER,
                CONF_SOCKET_LIMIT: False,
            },
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REQUEST_DELAY] == 7.5


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
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Now submit manual entry
    with patch_manual_connection(device_info=device_info):
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
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry that fails to connect
    with patch_manual_connection(error=ConnectionError("cannot connect")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_flow_keeps_custom_port_after_failure(
    hass: HomeAssistant,
) -> None:
    """Test manual form keeps the submitted custom port after connection failure."""
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    with patch_manual_connection(error=ConnectionError("cannot connect")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30030}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}
    assert _get_schema_field_default(result, CONF_PORT) == 30030


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
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry with invalid device info
    with patch_manual_connection(device_info=device_info):
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
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry for already configured device
    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_flow_already_configured_via_wifi_mac(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Manual add must abort when only the Wi-Fi MAC matches an existing entry."""
    mock_config_entry.add_to_hass(hass)

    device_info = {
        "ip": "192.168.1.100",
        "device_type": "Venus C",
        "version": 153,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }

    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].unique_id == "aa:bb:cc:dd:ee:ff"


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


async def test_reauth_flow_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test successful reauth flow."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    device_info = {
        "ip": "192.168.1.200",
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
            user_input={"host": "192.168.1.200"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == "192.168.1.200"


async def test_reauth_confirm_form_snapshot(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot
) -> None:
    """Test reauth confirm form structure snapshot."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    schema = result["data_schema"].schema
    fields: dict[str, dict[str, object]] = {}
    for key in schema:
        default = getattr(key, "default", None)
        if default is vol.UNDEFINED:
            default = None
        fields[str(key)] = {
            "required": getattr(key, "required", False),
            "default": default,
        }

    snapshot_data = {
        "type": result["type"],
        "step_id": result["step_id"],
        "errors": result.get("errors"),
        "description_placeholders": result.get("description_placeholders"),
        "fields": fields,
    }

    assert snapshot_data == snapshot


async def test_reauth_flow_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow with connection failure."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch_manual_connection(error=TimeoutError("Connection timeout")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.200"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_device_returns_none(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow when device returns None."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM

    with patch_manual_connection(device_info=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.200"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test successful reconfigure flow."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    device_info = {
        "ip": "192.168.1.201",
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
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    updated_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated_entry.data["host"] == "192.168.1.201"
    assert updated_entry.data["port"] == 30000
    assert updated_entry.data["version"] == "3.0"
    assert updated_entry.data["device_type"] == "Venus"


async def test_reconfigure_wifi_unique_id_matches_ble_device(
    hass: HomeAssistant,
) -> None:
    """Reconfigure must not fail when the entry unique_id is the Wi-Fi MAC."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={
            "host": "1.2.3.4",
            "wifi_mac": "11:22:33:44:55:66",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )
    assert result["step_id"] == "reconfigure_confirm"

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
        "wifi_mac": "11:22:33:44:55:66",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.unique_id == "11:22:33:44:55:66"
    assert updated.data["host"] == "192.168.1.201"


async def test_reconfigure_unchanged_entry_still_schedules_a_reload(
    hass: HomeAssistant,
) -> None:
    """Resubmitting the same host means "retry this device", so reload it.

    The reload is scheduled by the entry's update listener when the data
    changes. An unchanged entry never reaches that listener, so the flow has
    to schedule the reload itself.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "host": "192.168.1.201",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
    }

    with (
        patch_manual_connection(device_info=device_info),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
        ) as schedule_reload,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    schedule_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_changed_entry_leaves_the_reload_to_the_listener(
    hass: HomeAssistant,
) -> None:
    """A changed entry must not be reloaded twice.

    async_update_entry notifies the update listener, which reloads. Asking
    Home Assistant to schedule a second reload is what it reports as
    deprecated and stops honouring in 2026.12.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "host": "192.168.1.200",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
    }

    with (
        patch_marstek_integration(),
        patch_manual_connection(device_info=device_info),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
        ) as schedule_reload:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={"host": "192.168.1.201", "port": 30000},
            )
            await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == (
        "192.168.1.201"
    )
    schedule_reload.assert_not_called()


async def test_reconfigure_unloaded_entry_schedules_the_reload(
    hass: HomeAssistant,
) -> None:
    """An entry that is not loaded carries no update listener.

    Nothing would pick the corrected host up until Home Assistant's own
    setup-retry backoff came round, which grows to ten minutes, so the flow
    has to ask for the reload itself.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "host": "192.168.1.200",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
    }

    with (
        patch_manual_connection(device_info=device_info),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
        ) as schedule_reload,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "192.168.1.201"
    schedule_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_confirm_form_snapshot(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot
) -> None:
    """Test reconfigure confirm form structure snapshot."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["step_id"] == "reconfigure_confirm"

    schema = result["data_schema"].schema
    fields: dict[str, dict[str, object]] = {}
    for key in schema:
        default = getattr(key, "default", None)
        if default is vol.UNDEFINED:
            default = None
        fields[str(key)] = {
            "required": getattr(key, "required", False),
            "default": default,
        }

    snapshot_data = {
        "type": result["type"],
        "step_id": result["step_id"],
        "errors": result.get("errors"),
        "description_placeholders": result.get("description_placeholders"),
        "fields": fields,
    }

    assert snapshot_data == snapshot


async def test_reconfigure_flow_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow with connection failure."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    with patch_manual_connection(error=TimeoutError("Connection timeout")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_keeps_custom_port_after_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure form keeps custom port on failed reconnect attempt."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["step_id"] == "reconfigure_confirm"

    with patch_manual_connection(error=TimeoutError("Connection timeout")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30030},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"]["base"] == "cannot_connect"
    assert _get_schema_field_default(result, CONF_PORT) == 30030


async def test_manual_flow_value_error(hass: HomeAssistant) -> None:
    """Test manual entry flow when device returns ValueError (invalid data)."""
    # First trigger discovery that finds no devices to get to manual step
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    # Submit manual entry that raises ValueError
    with patch_manual_connection(error=ValueError("Invalid device data")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "invalid_discovery_info"}


def test_manual_entry_schema_rejects_invalid_port() -> None:
    """Test manual entry schema rejects ports outside valid range."""
    schema = build_manual_entry_schema(30000)

    with pytest.raises(vol.Invalid):
        schema({CONF_HOST: "192.168.1.100", CONF_PORT: 0})

    with pytest.raises(vol.Invalid):
        schema({CONF_HOST: "192.168.1.100", CONF_PORT: 70000})


async def test_reauth_flow_empty_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow with empty host shows error."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Submit empty host - should show form with error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"host": ""},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_empty_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow with empty host shows error."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    # Submit empty host - should show form with error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"host": "", "port": 30000},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_device_returns_none(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow when device returns None shows error."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    with patch_manual_connection(device_info=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


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


async def test_user_flow_connection_error_redirects_to_manual(
    hass: HomeAssistant,
) -> None:
    """Test user flow redirects to manual when ConnectionError occurs."""
    with patch_discovery([], error=ConnectionError("Network unreachable")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    # Should redirect to manual entry with cannot_connect error
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


def _pooled_udp_client(hass: HomeAssistant) -> MagicMock:
    """Install a pooled UDP client so config-flow pause/resume is observable."""
    client = MagicMock()
    client.async_pause_receiver = AsyncMock()
    client.async_resume_receiver = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[DATA_UDP_CLIENTS] = {30000: client}
    return client


async def test_manual_add_reuses_pooled_udp_client(hass: HomeAssistant) -> None:
    """Manual IP/port entry must send GetDevice on the pooled client, not pause."""
    client = _pooled_udp_client(hass)
    device_info = {
        "ip": "172.28.0.20",
        "ble_mac": "AA:BB:CC:DD:EE:01",
        "mac": "AA:BB:CC:DD:EE:01",
        "device_type": "VenusE 3.0",
        "version": "145",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "VenusE 3.0",
        "firmware": "145",
    }

    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    client.async_pause_receiver.reset_mock()
    client.async_resume_receiver.reset_mock()

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
    mock_get_device_info.assert_awaited_once()
    assert mock_get_device_info.await_args is not None
    assert mock_get_device_info.await_args.kwargs["udp_client"] is client
    client.async_pause_receiver.assert_not_called()
    client.async_resume_receiver.assert_not_called()


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


async def test_user_discovery_pauses_udp_receivers(hass: HomeAssistant) -> None:
    """Add Integration discovery must pause pooled listeners on each scan port."""
    client = _pooled_udp_client(hass)

    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    assert result["step_id"] == "manual"
    client.async_pause_receiver.assert_awaited()
    client.async_resume_receiver.assert_awaited()


@pytest.mark.parametrize(
    "device_type",
    ["Venus E2.0", "VenusE", "HMG-50"],
)
async def test_manual_flow_rejects_venus_e2(
    hass: HomeAssistant, device_type: str
) -> None:
    """HMG-50 Open API names are not a supported family."""
    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": device_type,
        "version": 153,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": device_type,
        "firmware": "153",
    }

    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "unsupported_device"}


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
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


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

