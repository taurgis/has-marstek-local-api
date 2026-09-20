"""Resolving service targets, retries and the data sync service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from custom_components.marstek.services import (
    ATTR_DEVICE_ID,
    ATTR_DURATION,
    ATTR_POWER,
    SERVICE_REQUEST_DATA_SYNC,
    SERVICE_SET_PASSIVE_MODE,
)
from tests.conftest import create_mock_client, patch_marstek_integration

DEVICE_IDENTIFIER = format_mac("AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_service_invalid_device_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test service with invalid device ID raises error."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Call service with invalid device ID
        with pytest.raises(ServiceValidationError, match="invalid_device"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_PASSIVE_MODE,
                {
                    ATTR_DEVICE_ID: "invalid_device_id",
                    ATTR_POWER: 1000,
                },
                blocking=True,
            )


@pytest.mark.asyncio
async def test_service_command_failure_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test service retries on command failure."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    # Setup succeeds (first call), then 2 failures + 1 success for retries
    client.send_request = AsyncMock(
        side_effect=[
            TimeoutError("timeout"),  # First service attempt fails
            TimeoutError("timeout"),  # Second attempt fails
            {"result": {}},  # Third attempt succeeds
        ]
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        # Should succeed after retries
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_POWER: 1000,
            },
            blocking=True,
        )

        # Should have called 3 retries (setup uses fetch_es_mode)
        assert client.send_request.call_count == 3


@pytest.mark.asyncio
async def test_service_command_all_retries_fail(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test service raises error when all retries fail."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    # Setup succeeds, then all service attempts fail
    call_count = 0

    async def send_request_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("timeout")  # All service calls fail

    client.send_request = AsyncMock(side_effect=send_request_side_effect)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        with pytest.raises(HomeAssistantError, match=r"command_failed|Failed to send"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_PASSIVE_MODE,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_POWER: 1000,
                },
                blocking=True,
            )


@pytest.mark.asyncio
async def test_request_data_sync_service_single_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test request_data_sync service for a single device."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Get device ID
        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        # Mock the coordinator's async_request_refresh to verify it's called
        coordinator = mock_config_entry.runtime_data.coordinator
        with patch.object(
            coordinator, "async_request_refresh", new_callable=AsyncMock
        ) as mock_refresh:
            # Call service
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REQUEST_DATA_SYNC,
                {
                    ATTR_DEVICE_ID: device.id,
                },
                blocking=True,
            )

            # Verify coordinator refresh was requested
            mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_request_data_sync_service_all_devices(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test request_data_sync service for all devices (no device_id)."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Mock the coordinator's async_request_refresh to verify it's called
        coordinator = mock_config_entry.runtime_data.coordinator
        with patch.object(
            coordinator, "async_request_refresh", new_callable=AsyncMock
        ) as mock_refresh:
            # Call service without device_id to refresh all
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REQUEST_DATA_SYNC,
                {},
                blocking=True,
            )

            # Verify coordinator refresh was requested
            mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_request_data_sync_skips_unloaded_entries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test request_data_sync only refreshes loaded entries with runtime_data."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Add a second loaded entry with runtime_data
        second_entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="bb:cc:dd:ee:ff:00",
            data={
                "host": "5.6.7.8",
                "ble_mac": "BB:CC:DD:EE:FF:00",
            },
        )
        second_entry.add_to_hass(hass)
        second_entry.runtime_data = SimpleNamespace(
            coordinator=AsyncMock(),
        )
        second_entry.mock_state(hass, ConfigEntryState.LOADED)

        # Add an unloaded entry with runtime_data to ensure it is skipped
        third_entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="cc:dd:ee:ff:00:11",
            data={
                "host": "9.9.9.9",
                "ble_mac": "CC:DD:EE:FF:00:11",
            },
        )
        third_entry.add_to_hass(hass)
        third_entry.runtime_data = SimpleNamespace(
            coordinator=AsyncMock(),
        )
        third_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

        coordinator = mock_config_entry.runtime_data.coordinator
        with patch.object(
            coordinator, "async_request_refresh", new_callable=AsyncMock
        ) as mock_refresh:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REQUEST_DATA_SYNC,
                {},
                blocking=True,
            )

            mock_refresh.assert_called_once()
            second_entry.runtime_data.coordinator.async_request_refresh.assert_called_once()
            third_entry.runtime_data.coordinator.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_set_passive_mode_accepts_truncated_device_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Issue #34: truncated registry IDs from YAML still control the device."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None
        client.send_request.reset_mock()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id[:-1],
                ATTR_POWER: -500,
                ATTR_DURATION: 300,
            },
            blocking=True,
        )

        assert client.send_request.call_count >= 1


@pytest.mark.asyncio
async def test_set_passive_mode_accepts_entity_id_and_selector_list(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Entity IDs and single-item selector lists resolve to the battery."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        client.send_request.reset_mock()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: ["sensor.venus_battery_level"],
                ATTR_POWER: -500,
                ATTR_DURATION: 300,
            },
            blocking=True,
        )

        assert client.send_request.call_count >= 1
