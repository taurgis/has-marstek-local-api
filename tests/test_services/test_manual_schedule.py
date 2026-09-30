"""Manual schedule services: setting, batching and clearing slots."""

from __future__ import annotations

import json
from datetime import time
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
from custom_components.marstek.helpers.service_helpers import calculate_week_set
from custom_components.marstek.pymarstek.validators import (
    ValidationError,
    normalize_time_value,
)
from custom_components.marstek.services import (
    ATTR_DAYS,
    ATTR_DEVICE_ID,
    ATTR_ENABLE,
    ATTR_END_TIME,
    ATTR_POWER,
    ATTR_SCHEDULE_SLOT,
    ATTR_SCHEDULES,
    ATTR_START_TIME,
    SERVICE_CLEAR_MANUAL_SCHEDULES,
    SERVICE_SET_MANUAL_SCHEDULE,
    SERVICE_SET_MANUAL_SCHEDULES,
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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
async def test_e_mini_single_schedule_rejects_slot_six_before_udp(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Single-schedule validation applies the current profile before transmission."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E mini",
            "version": 145,
        },
    )
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = async_lookup_device_by_identifier(dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None
        client.send_request.reset_mock()

        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_MANUAL_SCHEDULE,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_SCHEDULE_SLOT: 6,
                    ATTR_START_TIME: "08:00",
                    ATTR_END_TIME: "16:00",
                    ATTR_POWER: 100,
                    ATTR_DAYS: ["mon"],
                    ATTR_ENABLE: True,
                },
                blocking=True,
            )

        assert err.value.translation_key == "schedule_slot_out_of_range"
        assert err.value.translation_placeholders == {
            "requested": "6",
            "min": "0",
            "max": "5",
        }
        client.send_request.assert_not_called()


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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
async def test_e_mini_clear_sends_six_commands_with_single_pause(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Clearing E mini schedules uses slots 0 through 5."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E mini",
            "version": 145,
        },
    )
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = async_lookup_device_by_identifier(dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None
        client.send_request.reset_mock()
        client.pause_polling.reset_mock()
        client.resume_polling.reset_mock()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_MANUAL_SCHEDULES,
            {ATTR_DEVICE_ID: device.id},
            blocking=True,
        )

        assert client.send_request.call_count == 6
        client.pause_polling.assert_awaited_once()
        client.resume_polling.assert_awaited_once()


async def _clear_with_failing_slots(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    failing_slots: set[int],
) -> tuple[list[int], HomeAssistantError | None]:
    """Clear schedules while *failing_slots* time out; return the slots sent."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = async_lookup_device_by_identifier(dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        sent: list[int] = []

        async def send_request(command: str, *_args: object, **_kwargs: object) -> dict:
            slot = json.loads(command)["params"]["config"]["manual_cfg"]["time_num"]
            sent.append(slot)
            if slot in failing_slots:
                raise TimeoutError("timeout")
            return {"id": 1, "result": {"set_result": True}}

        client.send_request = AsyncMock(side_effect=send_request)
        error: HomeAssistantError | None = None
        with patch("custom_components.marstek.helpers.command_retry.asyncio.sleep"):
            try:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_CLEAR_MANUAL_SCHEDULES,
                    {ATTR_DEVICE_ID: device.id},
                    blocking=True,
                )
            except HomeAssistantError as err:
                error = err
        client.pause_polling.assert_awaited_once()
        client.resume_polling.assert_awaited_once()
    return sent, error


@pytest.mark.asyncio
async def test_clear_manual_schedules_continues_past_failed_slot(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A slot that fails every retry does not leave the later slots set."""
    sent, error = await _clear_with_failing_slots(hass, mock_config_entry, {3, 7})

    # Slots 3 and 7 are tried three times each; every other slot once.
    assert sorted(set(sent)) == list(range(10))
    assert sent.count(3) == 3
    assert sent.count(7) == 3
    assert sent.count(9) == 1
    assert error is not None
    assert error.translation_key == "clear_schedules_failed"
    assert error.translation_placeholders == {"slots": "3, 7", "error": "timeout"}


@pytest.mark.asyncio
async def test_clear_manual_schedules_stops_when_device_is_gone(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Two failed slots in a row end the batch and report the rest as failed."""
    sent, error = await _clear_with_failing_slots(hass, mock_config_entry, set(range(10)))

    assert sent == [0, 0, 0, 1, 1, 1]
    assert error is not None
    assert error.translation_key == "clear_schedules_failed"
    assert error.translation_placeholders == {
        "slots": "0, 1, 2, 3, 4, 5, 6, 7, 8, 9",
        "error": "timeout",
    }


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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
async def test_e_mini_batch_rejects_before_pause_or_udp(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Batch validation rejects any out-of-profile slot before the batch starts."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E mini",
            "version": 145,
        },
    )
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = async_lookup_device_by_identifier(dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None
        client.send_request.reset_mock()
        client.pause_polling.reset_mock()

        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_MANUAL_SCHEDULES,
                {
                    ATTR_DEVICE_ID: device.id,
                    ATTR_SCHEDULES: [
                        {
                            ATTR_SCHEDULE_SLOT: 5,
                            ATTR_START_TIME: "08:00",
                            ATTR_END_TIME: "12:00",
                        },
                        {
                            ATTR_SCHEDULE_SLOT: 6,
                            ATTR_START_TIME: "12:00",
                            ATTR_END_TIME: "16:00",
                        },
                    ],
                },
                blocking=True,
            )

        assert err.value.translation_key == "schedule_slot_out_of_range"
        assert err.value.translation_placeholders["max"] == "5"
        client.send_request.assert_not_called()
        client.pause_polling.assert_not_awaited()


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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        with pytest.raises(ServiceValidationError, match="Requested power"):
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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        with pytest.raises(ServiceValidationError, match="Requested power"):
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
