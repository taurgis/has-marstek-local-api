"""Tests for Marstek device actions."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac

from custom_components.marstek.const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_SOCKET_LIMIT,
    DOMAIN,
)
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
        patch("custom_components.marstek.device_action.asyncio.sleep", new_callable=AsyncMock),
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


async def test_device_action_power_out_of_range_socket_limit_default(
    hass, mock_config_entry
):
    """Test device action rejects power above socket limit by default for Venus E."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
        options={
            CONF_ACTION_DISCHARGE_POWER: 2500,
        },
    )

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
        assert device

        config = {
            CONF_DEVICE_ID: device.id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_DISCHARGE,
        }

        from homeassistant.components.device_automation import InvalidDeviceAutomationConfig
        with pytest.raises(InvalidDeviceAutomationConfig, match="Requested power"):
            await async_call_action_from_config(hass, config, {}, None)


async def test_device_action_power_out_of_range_model_limit(hass, mock_config_entry):
    """Test device action rejects power above model max for Venus A."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus A",
        },
        options={
            CONF_ACTION_CHARGE_POWER: -1500,
        },
    )

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
        assert device

        config = {
            CONF_DEVICE_ID: device.id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_CHARGE,
        }

        from homeassistant.components.device_automation import InvalidDeviceAutomationConfig
        with pytest.raises(InvalidDeviceAutomationConfig, match="Requested power"):
            await async_call_action_from_config(hass, config, {}, None)


async def test_device_action_charge_enforces_socket_limit_default(
    hass, mock_config_entry
):
    """Test charge action enforces socket limit when it's on by default (Venus E)."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
        options={
            CONF_ACTION_CHARGE_POWER: -2000,  # Above 800W socket limit
        },
    )

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
        assert device

        config = {
            CONF_DEVICE_ID: device.id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_CHARGE,
        }

        from homeassistant.components.device_automation import InvalidDeviceAutomationConfig
        with pytest.raises(InvalidDeviceAutomationConfig, match="Requested power"):
            await async_call_action_from_config(hass, config, {}, None)


async def test_device_action_charge_enforces_socket_limit_explicit_true(
    hass, mock_config_entry
):
    """Test charge action enforces socket limit when explicitly enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
        options={
            CONF_ACTION_CHARGE_POWER: -2000,  # Above 800W socket limit
            CONF_SOCKET_LIMIT: True,
        },
    )

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
        assert device

        config = {
            CONF_DEVICE_ID: device.id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_CHARGE,
        }

        from homeassistant.components.device_automation import InvalidDeviceAutomationConfig
        with pytest.raises(InvalidDeviceAutomationConfig, match="Requested power"):
            await async_call_action_from_config(hass, config, {}, None)


async def test_device_action_charge_allows_high_power_without_socket_limit(
    hass, mock_config_entry
):
    """Test charge action allows high power when socket limit is disabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
        options={
            CONF_ACTION_CHARGE_POWER: -2000,  # Above 800W but allowed without socket limit
            CONF_SOCKET_LIMIT: False,
        },
    )

    client = _mock_client()
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
        assert device

        config = {
            CONF_DEVICE_ID: device.id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_CHARGE,
        }

        await async_call_action_from_config(hass, config, {}, None)
        client.pause_polling.assert_called()
        client.resume_polling.assert_called()


async def test_device_action_polling_active_during_verification_delay(
    hass, mock_config_entry
):
    """Test polling is resumed BEFORE verification delay sleep (not blocked during wait)."""
    mock_config_entry.add_to_hass(hass)

    call_order: list[str] = []

    client = _mock_client(mode_response={"result": {"mode": "Manual", "bat_power": -500}})
    
    # Track call order
    original_pause = client.pause_polling
    original_resume = client.resume_polling
    
    async def track_pause(host: str) -> None:
        call_order.append("pause")
        return await original_pause(host)
    
    async def track_resume(host: str) -> None:
        call_order.append("resume")
        return await original_resume(host)
    
    client.pause_polling = AsyncMock(side_effect=track_pause)
    client.resume_polling = AsyncMock(side_effect=track_resume)

    with (
        patch("custom_components.marstek.MarstekUDPClient", return_value=client),
        patch("custom_components.marstek.scanner.MarstekScanner.async_get", return_value=_mock_scanner()),
        patch("custom_components.marstek.device_action.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        # Track sleep calls in order
        async def track_sleep(delay: float) -> None:
            call_order.append(f"sleep_{delay}")
        mock_sleep.side_effect = track_sleep

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_IDENTIFIER)})
        assert device

        config = {
            CONF_DEVICE_ID: device.id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_CHARGE,
        }

        await async_call_action_from_config(hass, config, {}, None)

        # Key assertion: Resume must come BEFORE the 75s verification delay sleep
        # Expected order: pause -> resume -> sleep_75.0 -> pause -> resume
        # Find the first big sleep (verification delay)
        verification_sleep_idx = None
        for i, call in enumerate(call_order):
            if call.startswith("sleep_") and float(call.split("_")[1]) > 30:
                verification_sleep_idx = i
                break
        
        assert verification_sleep_idx is not None, f"No verification delay sleep found in {call_order}"
        
        # Before verification sleep, we must have: pause, resume (at least one complete cycle)
        pre_sleep_calls = call_order[:verification_sleep_idx]
        assert "pause" in pre_sleep_calls, f"No pause before verification delay: {pre_sleep_calls}"
        assert "resume" in pre_sleep_calls, f"No resume before verification delay: {pre_sleep_calls}"
        
        # The resume for send must come before the verification delay sleep
        last_resume_before_sleep = max(
            i for i, c in enumerate(pre_sleep_calls) if c == "resume"
        )
        assert last_resume_before_sleep < verification_sleep_idx, \
            f"Polling should be active during verification delay. Order: {call_order}"
