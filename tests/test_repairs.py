"""Tests for the Marstek repairs module."""

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.repairs import (
    CannotConnectRepairFlow,
    async_create_fix_flow,
)


async def test_async_create_fix_flow(hass: HomeAssistant) -> None:
    """Test creating a fix flow."""
    data = {"entry_id": "test_entry_id"}

    flow = await async_create_fix_flow(hass, "cannot_connect_test", data)

    assert isinstance(flow, CannotConnectRepairFlow)


async def test_async_create_fix_flow_no_data(hass: HomeAssistant) -> None:
    """Test creating a fix flow without data returns flow (handles in step)."""
    flow = await async_create_fix_flow(hass, "cannot_connect_test", {})
    assert isinstance(flow, CannotConnectRepairFlow)


async def test_async_create_fix_flow_none_data(hass: HomeAssistant) -> None:
    """Test creating a fix flow with None data returns flow (handles in step)."""
    flow = await async_create_fix_flow(hass, "cannot_connect_test", None)
    assert isinstance(flow, CannotConnectRepairFlow)


async def test_repair_flow_abort_missing_config(
    hass: HomeAssistant,
) -> None:
    """Test repair flow aborts when data is missing."""
    flow = CannotConnectRepairFlow()
    flow.hass = hass
    flow.issue_id = "cannot_connect_test"
    flow.data = {}  # No entry_id

    result = await flow.async_step_init()

    assert result["type"] == "abort"
    assert result["reason"] == "missing_config"


async def test_repair_flow_abort_entry_not_found(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test repair flow aborts when entry not found."""
    flow = CannotConnectRepairFlow()
    flow.hass = hass
    flow.issue_id = "cannot_connect_nonexistent"
    flow.data = {"entry_id": "nonexistent_entry_id"}

    result = await flow.async_step_init()

    assert result["type"] == "abort"
    assert result["reason"] == "entry_not_found"


async def test_repair_flow_shows_form(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test repair flow shows form with entry data."""
    mock_config_entry.add_to_hass(hass)

    flow = CannotConnectRepairFlow()
    flow.hass = hass
    flow.issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    flow.data = {"entry_id": mock_config_entry.entry_id}

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["description_placeholders"]["host"] == mock_config_entry.data["host"]
    # Verify form has host and port fields
    assert "host" in result["data_schema"].schema
    assert "port" in result["data_schema"].schema


async def test_repair_flow_submit_updates_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test submitting repair flow updates config entry and clears issue."""
    mock_config_entry.add_to_hass(hass)
    
    # Create the issue first
    issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="cannot_connect",
        translation_placeholders={"host": "1.2.3.4", "error": "timeout"},
        data={"entry_id": mock_config_entry.entry_id},
    )
    
    # Verify issue exists
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    flow = CannotConnectRepairFlow()
    flow.hass = hass
    flow.issue_id = issue_id
    flow.data = {"entry_id": mock_config_entry.entry_id}

    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
    }

    with (
        patch(
            "custom_components.marstek.repairs.get_device_info",
            return_value=device_info,
        ),
        patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ) as mock_reload,
    ):
        result = await flow.async_step_init({"host": "192.168.1.100", "port": 30000})

    # Verify entry was updated
    assert mock_config_entry.data["host"] == "192.168.1.100"
    assert mock_config_entry.data["port"] == 30000
    
    # Verify reload was called
    mock_reload.assert_called_once_with(mock_config_entry.entry_id)
    
    # Verify flow completed
    assert result["type"] == "create_entry"
    assert result["data"] == {}

    # Verify issue was deleted
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_repair_flow_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test repair flow shows error when device cannot be reached."""
    mock_config_entry.add_to_hass(hass)

    flow = CannotConnectRepairFlow()
    flow.hass = hass
    flow.issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    flow.data = {"entry_id": mock_config_entry.entry_id}

    with patch(
        "custom_components.marstek.repairs.get_device_info",
        side_effect=TimeoutError("timeout"),
    ):
        result = await flow.async_step_init({"host": "192.168.1.100", "port": 30000})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


async def test_repair_flow_unique_id_mismatch(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test repair flow shows error when device is different."""
    mock_config_entry.add_to_hass(hass)

    flow = CannotConnectRepairFlow()
    flow.hass = hass
    flow.issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    flow.data = {"entry_id": mock_config_entry.entry_id}

    # Return a device with different MAC
    device_info = {
        "ip": "192.168.1.100",
        "ble_mac": "11:22:33:44:55:66",  # Different MAC
        "device_type": "Venus",
    }

    with patch(
        "custom_components.marstek.repairs.get_device_info",
        return_value=device_info,
    ):
        result = await flow.async_step_init({"host": "192.168.1.100", "port": 30000})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unique_id_mismatch"
