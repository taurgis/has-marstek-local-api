"""The manual step: host and port entered by hand."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from custom_components.marstek.helpers.flow_schemas import build_manual_entry_schema
from tests.conftest import (
    patch_discovery,
    patch_manual_connection,
)

from ._helpers import (
    _get_schema_field_default,
    _pooled_udp_client,
)


async def test_manual_flow_form_snapshot(hass: HomeAssistant, snapshot) -> None:
    """Test manual entry form structure snapshot."""
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "manual"

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].unique_id == "aa:bb:cc:dd:ee:ff"


async def test_manual_flow_value_error(hass: HomeAssistant) -> None:
    """Test manual entry flow when device returns ValueError (invalid data)."""
    # First trigger discovery that finds no devices to get to manual step
    with patch_discovery([]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "manual"

    # Submit manual entry that raises ValueError
    with patch_manual_connection(error=ValueError("Invalid device data")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "invalid_discovery_info"}


@pytest.mark.parametrize(
    "device_type",
    ["Venus E2.0", "VenusE", "HMG-50"],
)
async def test_manual_flow_rejects_venus_e2(hass: HomeAssistant, device_type: str) -> None:
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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "manual"

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"host": "192.168.1.100", "port": 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "unsupported_device"}


def test_manual_entry_schema_rejects_invalid_port() -> None:
    """Test manual entry schema rejects ports outside valid range."""
    schema = build_manual_entry_schema(30000)

    with pytest.raises(vol.Invalid):
        schema({CONF_HOST: "192.168.1.100", CONF_PORT: 0})

    with pytest.raises(vol.Invalid):
        schema({CONF_HOST: "192.168.1.100", CONF_PORT: 70000})


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
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
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
