"""The set_passive_mode service and its power limits."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from custom_components.marstek.services import (
    ATTR_DEVICE_ID,
    ATTR_DURATION,
    ATTR_POWER,
    SERVICE_SET_PASSIVE_MODE,
)
from tests.conftest import create_mock_client, patch_marstek_integration

DEVICE_IDENTIFIER = format_mac("AA:BB:CC:DD:EE:FF")


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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
            "device_type": "Venus E 3.0",
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
            "device_type": "Venus E 3.0",
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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
            "device_type": "Venus E 3.0",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
            "device_type": "Venus E 3.0",
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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
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
async def test_set_passive_mode_venus_a_allows_1500w_charge(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test Venus A accepts its full 1500 W charge limit."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus A",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: device.id,
                ATTR_POWER: -1500,
                ATTR_DURATION: 3600,
            },
            blocking=True,
        )

        assert client.send_request.call_count >= 1


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
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None

        with pytest.raises(ServiceValidationError, match="Requested power"):
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
