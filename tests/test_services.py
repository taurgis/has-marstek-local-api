"""Tests for Marstek services."""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers.device_registry import format_mac
from custom_components.marstek.const import DOMAIN
from custom_components.marstek.services import (
    ATTR_DEVICE_ID,
    ATTR_DAYS,
    ATTR_DURATION,
    ATTR_ENABLE,
    ATTR_END_TIME,
    ATTR_POWER,
    ATTR_SCHEDULE_SLOT,
    ATTR_SCHEDULES,
    ATTR_START_TIME,
    SERVICE_CLEAR_MANUAL_SCHEDULES,
    SERVICE_REQUEST_DATA_SYNC,
    SERVICE_SET_MANUAL_SCHEDULE,
    SERVICE_SET_MANUAL_SCHEDULES,
    SERVICE_SET_PASSIVE_MODE,
)
from custom_components.marstek.helpers.service_helpers import calculate_week_set
from custom_components.marstek.pymarstek.validators import (
    ValidationError,
    normalize_time_value,
)

from tests.conftest import create_mock_client, patch_marstek_integration
DEVICE_IDENTIFIER = format_mac("AA:BB:CC:DD:EE:FF")


def test_calculate_week_set() -> None:
    """Test calculate_week_set helper function."""
    # Test individual days (mon=1, tue=2, wed=4, thu=8, fri=16, sat=32, sun=64)
    assert calculate_week_set(["mon"]) == 1
    assert calculate_week_set(["sun"]) == 64
    assert calculate_week_set(["sat"]) == 32

    # Test all days (1+2+4+8+16+32+64 = 127)
    all_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert calculate_week_set(all_days) == 127

    # Test mixed case (1+2+4 = 7)
    assert calculate_week_set(["Mon", "TUE", "wed"]) == 7

    # Test empty and invalid
    assert calculate_week_set([]) == 0
    assert calculate_week_set(["invalid"]) == 0


def test_normalize_time_value() -> None:
    """Test normalize_time_value helper function."""
    # Standard HH:MM format
    assert normalize_time_value("08:00") == "08:00"
    assert normalize_time_value("23:59") == "23:59"
    assert normalize_time_value("00:00") == "00:00"

    # HH:MM:SS format (seconds ignored)
    assert normalize_time_value("08:00:00") == "08:00"
    assert normalize_time_value("14:30:45") == "14:30"

    # Padding for single digits
    assert normalize_time_value("8:5") == "08:05"

    # Invalid format should raise
    with pytest.raises(ValidationError, match="time must be in HH:MM or HH:MM:SS"):
        normalize_time_value("invalid")


@pytest.mark.asyncio
async def test_set_passive_mode_service(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test set_passive_mode service."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Get device ID from registry
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        # Call service
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_POWER: 2500,
                ATTR_DURATION: 7200,
            },
            blocking=True,
        )

        # Verify command was sent
        assert client.pause_polling.call_count >= 1
        assert client.send_request.call_count >= 1
        assert client.resume_polling.call_count >= 1


@pytest.mark.asyncio
async def test_set_passive_mode_power_out_of_range_socket_limit_default(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test passive mode rejects power above socket limit by default for Venus E."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError, match="Requested power"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_PASSIVE_MODE,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_POWER: 1200,
                    ATTR_DURATION: 3600,
                },
                blocking=True,
            )


@pytest.mark.asyncio
async def test_set_passive_mode_power_allowed_when_socket_limit_disabled(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test passive mode allows model max when socket limit is disabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
        options={
            "socket_limit": False,
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_POWER: 2500,
                ATTR_DURATION: 3600,
            },
            blocking=True,
        )

        assert client.send_request.call_count >= 1


@pytest.mark.asyncio
async def test_set_passive_mode_charge_ignores_socket_limit_default(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test passive mode allows charge above 800 W when socket limit is on by default."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_POWER: -2000,
                ATTR_DURATION: 3600,
            },
            blocking=True,
        )

        assert client.send_request.call_count >= 1


@pytest.mark.asyncio
async def test_set_passive_mode_charge_ignores_socket_limit_explicit_true(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test passive mode allows charge above 800 W when socket limit is explicitly enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E",
        },
        options={
            "socket_limit": True,
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_POWER: -2000,
                ATTR_DURATION: 3600,
            },
            blocking=True,
        )

        assert client.send_request.call_count >= 1


@pytest.mark.asyncio
async def test_set_manual_schedule_service(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test set_manual_schedule service."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Get device ID
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        # Call service
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MANUAL_SCHEDULE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_SCHEDULE_SLOT: 0,
                ATTR_START_TIME: time(7, 0),
                ATTR_END_TIME: time(22, 0),
                ATTR_POWER: 3000,
                ATTR_DAYS: ["mon", "tue", "wed"],
                ATTR_ENABLE: True,
            },
            blocking=True,
        )

        # Verify command was sent
        assert client.send_request.call_count >= 1


@pytest.mark.asyncio
async def test_clear_manual_schedules_service(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test clear_manual_schedules service."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Get device ID
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        # Call service
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_MANUAL_SCHEDULES,
            {
                ATTR_DEVICE_ID: device.id,
            },
            blocking=True,
        )

        # Verify commands were sent (10 slots cleared)
        assert client.send_request.call_count >= 10
        # Polling should be paused/resumed once for the batch
        assert client.pause_polling.call_count == 1
        assert client.resume_polling.call_count == 1


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
        with pytest.raises(HomeAssistantError, match="invalid_device"):
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
            {"result": {}},  # Setup call succeeds
            TimeoutError("timeout"),  # First service attempt fails
            TimeoutError("timeout"),  # Second attempt fails
            {"result": {}},  # Third attempt succeeds
        ]
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
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

        # Should have called: 1 setup + 3 retries = 4 total
        assert client.send_request.call_count == 4


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
        if call_count == 1:  # First call is during setup
            return {"result": {}}
        raise TimeoutError("timeout")  # All service calls fail

    client.send_request = AsyncMock(side_effect=send_request_side_effect)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError, match="command_failed|Failed to send"):
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
async def test_set_manual_schedules_service(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test set_manual_schedules (batch) service."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Get device ID
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        # Call service with multiple schedules
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_MANUAL_SCHEDULES,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_SCHEDULES: [
                    {
                        ATTR_SCHEDULE_SLOT: 0,
                        ATTR_START_TIME: "08:00",
                        ATTR_END_TIME: "16:00",
                        ATTR_POWER: -2000,
                        ATTR_DAYS: ["mon", "tue", "wed", "thu", "fri"],
                        ATTR_ENABLE: True,
                    },
                    {
                        ATTR_SCHEDULE_SLOT: 1,
                        ATTR_START_TIME: "18:00",
                        ATTR_END_TIME: "22:00",
                        ATTR_POWER: 800,
                        ATTR_ENABLE: True,
                    },
                ],
            },
            blocking=True,
        )

        # Verify commands were sent (1 for setup + 2 for schedules)
        assert client.send_request.call_count >= 2
        # Polling should be paused/resumed once for the batch
        assert client.pause_polling.call_count == 1
        assert client.resume_polling.call_count == 1


@pytest.mark.asyncio
async def test_set_manual_schedules_invalid_time_format(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test batch schedules reject invalid time strings."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_MANUAL_SCHEDULES,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_SCHEDULES: [
                        {
                            ATTR_SCHEDULE_SLOT: 0,
                            ATTR_START_TIME: "invalid",
                            ATTR_END_TIME: "16:00",
                            ATTR_POWER: -2000,
                            ATTR_DAYS: ["mon", "tue"],
                            ATTR_ENABLE: True,
                        },
                    ],
                },
                blocking=True,
            )

        assert err.value.translation_key == "invalid_time_format"


@pytest.mark.asyncio
async def test_set_manual_schedules_invalid_time_range(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test batch schedules reject end times before start times."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_MANUAL_SCHEDULES,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_SCHEDULES: [
                        {
                            ATTR_SCHEDULE_SLOT: 0,
                            ATTR_START_TIME: "18:00",
                            ATTR_END_TIME: "08:00",
                            ATTR_POWER: -2000,
                            ATTR_DAYS: ["mon", "tue"],
                            ATTR_ENABLE: True,
                        },
                    ],
                },
                blocking=True,
            )

        assert err.value.translation_key == "invalid_time_range"


@pytest.mark.asyncio
async def test_set_manual_schedules_power_out_of_range(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test manual schedules reject power above socket limit by default for Venus D."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus D",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError, match="Requested power"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_MANUAL_SCHEDULES,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_SCHEDULES: [
                        {
                            ATTR_SCHEDULE_SLOT: 0,
                            ATTR_START_TIME: "08:00",
                            ATTR_END_TIME: "16:00",
                            ATTR_POWER: 1000,
                            ATTR_DAYS: ["mon", "tue"],
                            ATTR_ENABLE: True,
                        },
                    ],
                },
                blocking=True,
            )


@pytest.mark.asyncio
async def test_set_manual_schedules_mixed_invalid_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test batch schedules fail when any entry is out of range."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus D",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError, match="Requested power"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_MANUAL_SCHEDULES,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_SCHEDULES: [
                        {
                            ATTR_SCHEDULE_SLOT: 0,
                            ATTR_START_TIME: "08:00",
                            ATTR_END_TIME: "16:00",
                            ATTR_POWER: 600,
                            ATTR_DAYS: ["mon", "tue"],
                            ATTR_ENABLE: True,
                        },
                        {
                            ATTR_SCHEDULE_SLOT: 1,
                            ATTR_START_TIME: "18:00",
                            ATTR_END_TIME: "22:00",
                            ATTR_POWER: 1200,
                            ATTR_DAYS: ["wed"],
                            ATTR_ENABLE: True,
                        },
                    ],
                },
                blocking=True,
            )


@pytest.mark.asyncio
async def test_set_passive_mode_unknown_device_type_out_of_range(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test unknown device type falls back to socket limit when enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Unknown Model",
        },
        options={
            "socket_limit": True,
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        with pytest.raises(HomeAssistantError, match="Requested power"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_PASSIVE_MODE,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_POWER: 1200,
                    ATTR_DURATION: 3600,
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
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
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
