"""Tests for Marstek device actions."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.device_action import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_STOP,
    async_call_action_from_config,
    async_get_actions,
)

DEVICE_IDENTIFIER = format_mac("AA:BB:CC:DD:EE:FF")


def _mock_client(status=None, mode_response=None):
    """Create a mock MarstekUDPClient."""
    client = MagicMock()
    client.async_setup = AsyncMock(return_value=None)
    client.async_cleanup = AsyncMock(return_value=None)
    # Use bat_power for ES.GetStatus verification (not ongrid_power)
    client.send_request = AsyncMock(return_value=mode_response or {"result": {"mode": "Manual", "bat_power": -500}})
    client.get_device_status = AsyncMock(return_value=status or {
        "device_mode": "SelfUse",
        "battery_soc": 55,
        "battery_power": 120,
    })
    client.pause_polling = AsyncMock(return_value=None)
    client.resume_polling = AsyncMock(return_value=None)
    return client


def _mock_scanner():
    """Create a mock MarstekScanner."""
    scanner = MagicMock()
    scanner.async_setup = AsyncMock(return_value=None)
    return scanner


@contextmanager
def _patch_all(client=None, scanner=None):
    """Patch MarstekUDPClient and MarstekScanner for tests."""
    client = client or _mock_client()
    scanner = scanner or _mock_scanner()
    with (
        patch("custom_components.marstek.MarstekUDPClient", return_value=client),
        patch("custom_components.marstek.scanner.MarstekScanner.async_get", return_value=scanner),
    ):
        yield client, scanner


async def test_async_get_actions(hass, mock_config_entry):
    """Test getting available actions for a Marstek device."""
    mock_config_entry.add_to_hass(hass)

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
    assert device

    actions = await async_get_actions(hass, device.id)
    action_types = {action[CONF_TYPE] for action in actions}

    assert ACTION_CHARGE in action_types
    assert ACTION_DISCHARGE in action_types
    assert ACTION_STOP in action_types


@pytest.mark.parametrize("action_type,expected_power_negative", [
    (ACTION_CHARGE, True),
    (ACTION_DISCHARGE, False),
    (ACTION_STOP, None),
])
async def test_device_actions_pause_and_resume(
    hass, mock_config_entry, action_type, expected_power_negative
):
    """Test device actions pause polling during execution and resume after."""
    mock_config_entry.add_to_hass(hass)

    # Mock response based on action type (using bat_power for ES.GetStatus verification)
    if expected_power_negative is True:
        mode_response = {"result": {"mode": "Manual", "bat_power": -500}}
    elif expected_power_negative is False:
        mode_response = {"result": {"mode": "Manual", "bat_power": 500}}
    else:
        mode_response = {"result": {"mode": "Manual", "bat_power": 0}}

    client = _mock_client(mode_response=mode_response)
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
    assert device

    config = {
        CONF_DEVICE_ID: device.id,
        CONF_DOMAIN: DOMAIN,
        CONF_TYPE: action_type,
    }

    await async_call_action_from_config(hass, config, {}, None)

    # Verify polling was paused and resumed
    client.pause_polling.assert_called()
    client.resume_polling.assert_called()


async def test_device_action_invalid_device(hass, mock_config_entry):
    """Test device action with invalid device ID."""
    mock_config_entry.add_to_hass(hass)

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    config = {
        CONF_DEVICE_ID: "invalid_device_id",
        CONF_DOMAIN: DOMAIN,
        CONF_TYPE: ACTION_CHARGE,
    }

    from homeassistant.components.device_automation import InvalidDeviceAutomationConfig
    with pytest.raises(InvalidDeviceAutomationConfig):
        await async_call_action_from_config(hass, config, {}, None)
