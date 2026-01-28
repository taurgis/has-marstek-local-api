"""Tests for Marstek coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.marstek.const import CONF_POLL_INTERVAL_SLOW, DOMAIN
from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator


@pytest.fixture
def mock_udp_client():
    """Create a mock UDP client."""
    client = MagicMock()
    client.is_polling_paused = MagicMock(return_value=False)
    client.get_device_status = AsyncMock(
        return_value={
            "battery_soc": 55,
            "battery_power": 100,
            "device_mode": "auto",
            "battery_status": "charging",
            "pv1_power": 200,
        }
    )
    return client


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
    assert data["battery_power"] == 100
    assert data["device_mode"] == "auto"
    mock_udp_client.get_device_status.assert_called_once()


async def test_coordinator_skips_wifi_status_when_disabled(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that Wifi.GetStatus is skipped when WiFi diagnostics are disabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 0}
    )

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_wifi_rssi",
        config_entry=mock_config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is False


async def test_coordinator_polling_paused_returns_cached_data(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that polling paused returns cached data."""
    mock_config_entry.add_to_hass(hass)
    mock_udp_client.is_polling_paused = MagicMock(return_value=True)

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


async def test_coordinator_polling_paused_returns_empty_dict_when_no_cache(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that polling paused returns empty dict when no cached data."""
    mock_config_entry.add_to_hass(hass)
    mock_udp_client.is_polling_paused = MagicMock(return_value=True)

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


async def test_coordinator_no_fresh_data_raises_update_failed(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that no fresh data raises UpdateFailed after threshold is reached."""
    mock_config_entry.add_to_hass(hass)
    # Set failure threshold to 1 (immediate failure)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )
    mock_udp_client.get_device_status = AsyncMock(
        return_value={
            "battery_soc": 0,
            "battery_power": 0,
            "device_mode": "Unknown",  # Default value indicates failure
            "has_fresh_data": False,
        }
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()


async def test_coordinator_timeout_error_raises_update_failed(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that TimeoutError raises UpdateFailed after threshold is reached."""
    mock_config_entry.add_to_hass(hass)
    # Set failure threshold to 1 (immediate failure)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )
    mock_udp_client.get_device_status = AsyncMock(side_effect=TimeoutError("timeout"))

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()


async def test_coordinator_os_error_raises_update_failed(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that OSError raises UpdateFailed after threshold is reached."""
    mock_config_entry.add_to_hass(hass)
    # Set failure threshold to 1 (immediate failure)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )
    mock_udp_client.get_device_status = AsyncMock(
        side_effect=OSError("Network unreachable")
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()


async def test_coordinator_value_error_raises_update_failed(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that ValueError raises UpdateFailed after threshold is reached."""
    mock_config_entry.add_to_hass(hass)
    # Set failure threshold to 1 (immediate failure)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )
    mock_udp_client.get_device_status = AsyncMock(side_effect=ValueError("Invalid data"))

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()


async def test_coordinator_failure_threshold_keeps_entities_available(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that failures below threshold keep entities available with cached data."""
    # Use default threshold of 3
    mock_config_entry.add_to_hass(hass)
    mock_udp_client.get_device_status = AsyncMock(side_effect=TimeoutError("timeout"))

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )
    # Set some cached data
    coordinator.data = {"battery_soc": 50, "battery_power": 100}

    # First failure - should return cached data, not raise
    result = await coordinator._async_update_data()
    assert result == {"battery_soc": 50, "battery_power": 100}
    assert coordinator.consecutive_failures == 1

    # Second failure - still below threshold
    result = await coordinator._async_update_data()
    assert result == {"battery_soc": 50, "battery_power": 100}
    assert coordinator.consecutive_failures == 2

    # Third failure - reaches threshold, should raise UpdateFailed
    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()
    assert coordinator.consecutive_failures == 3

