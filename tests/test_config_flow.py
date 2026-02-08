"""Tests for Marstek config flow."""

from __future__ import annotations



import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DOMAIN,
    CONF_SOCKET_LIMIT,
)
from custom_components.marstek.helpers.flow_schemas import build_manual_entry_schema

from tests.conftest import (
    create_mock_client,
    patch_discovery,
    patch_manual_connection,
    patch_marstek_integration,
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
    updated_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated_entry.data["host"] == "192.168.1.201"
    assert updated_entry.data["port"] == 30000


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