"""Setup, pacing and the pause/resume handshake."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.marstek.const import (
    CONF_PARALLEL_API_REQUESTS,
    CONF_REQUEST_DELAY,
    DEFAULT_REQUEST_DELAY,
    INITIAL_SETUP_REQUEST_DELAY,
)
from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator


@pytest.mark.asyncio
async def test_coordinator_init(hass: HomeAssistant, mock_config_entry, mock_udp_client):
    """Test coordinator initialization."""
    mock_config_entry.add_to_hass(hass)

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    assert coordinator.device_ip == "1.2.3.4"
    assert coordinator.udp_client is mock_udp_client
    assert coordinator.config_entry is mock_config_entry
    assert coordinator.name == "Marstek 1.2.3.4"


@pytest.mark.asyncio
async def test_coordinator_device_ip_from_config_entry(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test device_ip reads from config entry dynamically."""
    mock_config_entry.add_to_hass(hass)

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    # Initial IP should match
    assert coordinator.device_ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_coordinator_successful_update(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test successful data update."""
    mock_config_entry.add_to_hass(hass)

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    data = await coordinator._async_update_data()

    assert data["battery_soc"] == 55
    assert data["battery_power"] == -250
    assert data["device_mode"] == "auto"
    mock_udp_client.get_device_status.assert_called_once()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_em"] is coordinator.profile.supports_em_status


@pytest.mark.asyncio
async def test_reset_prone_skips_initial_fast_request_delay(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """Older firmware keeps the configured request delay during first fetch."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
    )
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
        is_initial_setup=True,
    )
    assert coordinator._get_request_delay() == DEFAULT_REQUEST_DELAY
    coordinator.finish_initial_setup()
    assert coordinator._get_request_delay() == DEFAULT_REQUEST_DELAY


@pytest.mark.asyncio
async def test_capable_firmware_uses_initial_fast_request_delay(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """Firmware 150+ may use the shorter first-fetch delay."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 150,
        },
    )
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
        is_initial_setup=True,
    )
    assert coordinator._get_request_delay() == INITIAL_SETUP_REQUEST_DELAY


@pytest.mark.asyncio
async def test_coordinator_parallel_requests_option(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test coordinator enables parallel requests and forces zero delay."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_PARALLEL_API_REQUESTS: True,
            CONF_REQUEST_DELAY: 5.0,
        },
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["parallel_requests"] is True
    assert kwargs["delay_between_requests"] == 0.0


@pytest.mark.asyncio
async def test_coordinator_ignores_parallel_on_reset_prone_firmware(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Firmware below Control 150 ignores the parallel-requests option."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
        options={
            CONF_PARALLEL_API_REQUESTS: True,
            CONF_REQUEST_DELAY: 5.0,
        },
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["parallel_requests"] is False
    assert kwargs["delay_between_requests"] == 5.0


@pytest.mark.asyncio
async def test_coordinator_opts_in_wifi_retransmit_on_firmware_150(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """Known-safe Control may receive extra read-only Wi-Fi copies."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 150,
        },
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    mock_udp_client.set_openapi_reset_prone.assert_called_with(
        "1.2.3.4", False, owner=mock_config_entry.entry_id
    )
    mock_udp_client.set_openapi_retransmit_safe.assert_called_with("1.2.3.4", True)


@pytest.mark.asyncio
async def test_coordinator_clears_retransmit_safe_on_ip_change(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """A later IP must not inherit Wi-Fi copies from the previous address."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 150,
        },
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )
    await coordinator._async_update_data()
    mock_udp_client.set_openapi_retransmit_safe.reset_mock()

    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "host": "5.6.7.8"},
    )
    await coordinator._async_update_data()

    mock_udp_client.set_openapi_retransmit_safe.assert_any_call("1.2.3.4", False)
    mock_udp_client.set_openapi_retransmit_safe.assert_called_with("5.6.7.8", True)


@pytest.mark.asyncio
async def test_coordinator_polling_paused_returns_cached_data(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that polling paused returns cached data."""
    mock_config_entry.add_to_hass(hass)
    mock_udp_client.begin_poll_cycle = AsyncMock(return_value=False)

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )
    # Set some cached data
    coordinator.data = {"cached": "data"}

    data = await coordinator._async_update_data()

    # Should return cached data without calling get_device_status
    assert data == {"cached": "data"}
    mock_udp_client.get_device_status.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_polling_paused_returns_empty_dict_when_no_cache(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that polling paused returns empty dict when no cached data."""
    mock_config_entry.add_to_hass(hass)
    mock_udp_client.begin_poll_cycle = AsyncMock(return_value=False)

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )
    # No cached data
    coordinator.data = None

    data = await coordinator._async_update_data()

    assert data == {}
    mock_udp_client.get_device_status.assert_not_called()
