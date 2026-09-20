"""Update failures, caching and outage reporting."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek.pymarstek import MarstekUDPClient


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_coordinator_failure_without_cache_raises(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """A polling failure with no previous data must not keep empty entities available."""
    mock_config_entry.add_to_hass(hass)
    mock_udp_client.get_device_status = AsyncMock(side_effect=TimeoutError("timeout"))

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()
    assert coordinator.consecutive_failures == 1


@pytest.mark.asyncio
async def test_coordinator_recovers_after_failure(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test coordinator recovers after a failure threshold is hit."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )
    mock_udp_client.get_device_status = AsyncMock(
        side_effect=[
            TimeoutError("timeout"),
            {
                "battery_soc": 55,
                "battery_power": -250,
                "device_mode": "Auto",
            },
        ]
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with pytest.raises(UpdateFailed, match="Polling failed"):
        await coordinator._async_update_data()
    assert coordinator.consecutive_failures == 1

    data = await coordinator._async_update_data()
    assert data["battery_soc"] == 55
    assert coordinator.consecutive_failures == 0
    assert coordinator.last_update_success_time is not None


@pytest.mark.asyncio
async def test_coordinator_idle_fallback_overrides_cached_power(
    hass: HomeAssistant, mock_config_entry
):
    """Test missing bat_power with zero flows overwrites cached battery power."""
    mock_config_entry.add_to_hass(hass)
    client = MarstekUDPClient()

    async def mock_send_request(message: str, *args, **kwargs):
        method = json.loads(message).get("method")
        if method == "ES.GetMode":
            return {
                "id": 1,
                "result": {"mode": "Auto", "bat_soc": 10, "ongrid_power": 0},
            }
        if method == "ES.GetStatus":
            return {
                "id": 2,
                "result": {
                    "bat_soc": 10,
                    "pv_power": 0,
                    "ongrid_power": 0,
                    "offgrid_power": 0,
                },
            }
        if method == "EM.GetStatus":
            return {
                "id": 3,
                "result": {
                    "ct_state": 1,
                    "a_power": 0,
                    "b_power": 0,
                    "c_power": 0,
                    "total_power": 0,
                },
            }
        if method == "PV.GetStatus":
            return {"id": 4, "result": {"pv_power": 0}}
        if method == "Wifi.GetStatus":
            return {"id": 5, "result": {}}
        if method == "Bat.GetStatus":
            return {"id": 6, "result": {}}
        return {"id": 0, "result": {}}

    with patch.object(client, "send_request", side_effect=mock_send_request):
        with patch("asyncio.sleep", AsyncMock()):
            coordinator = MarstekDataUpdateCoordinator(
                hass,
                mock_config_entry,
                client,
                "1.2.3.4",
            )
            coordinator.data = {
                "battery_power": 500,
                "battery_status": "discharging",
            }
            result = await coordinator._async_update_data()

    assert result["battery_power"] == 0
    assert result["battery_status"] == "idle"


@pytest.mark.asyncio
async def test_unreachable_device_logs_the_transitions_only(
    hass: HomeAssistant, mock_config_entry, mock_udp_client, caplog
) -> None:
    """One line when a device goes quiet, one when it answers again.

    A device offline overnight at the default 30s interval would otherwise
    write thousands of warnings and bury whatever else went wrong.
    https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )
    healthy = mock_udp_client.get_device_status.return_value
    mock_udp_client.get_device_status.side_effect = [
        TimeoutError("no reply"),
        TimeoutError("no reply"),
        TimeoutError("no reply"),
        healthy,
        TimeoutError("no reply"),
    ]

    with (
        patch(
            "custom_components.marstek.coordinator.MarstekScanner.async_get",
            return_value=MagicMock(),
        ),
        caplog.at_level(logging.DEBUG, logger="custom_components.marstek.coordinator"),
    ):
        for _ in range(3):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

        warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "status request failed" in
            record.getMessage()
        ]
        assert len(warnings) == 1

        caplog.clear()
        assert (await coordinator._async_update_data())["battery_soc"] == 55
        recoveries = [
            record
            for record in caplog.records
            if record.levelno == logging.INFO and "answering again" in
            record.getMessage()
        ]
        assert len(recoveries) == 1
        assert coordinator.consecutive_failures == 0

        # Recovery re-arms the warning, so the next outage is reported too.
        caplog.clear()
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "status request failed" in
            record.getMessage()
        ]


@pytest.mark.asyncio
async def test_connection_issue_is_raised_once_per_outage(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """Re-raising the same issue republishes it to the repairs panel."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )
    real_create = ir.async_create_issue

    with patch(
        "custom_components.marstek.coordinator.ir.async_create_issue",
        side_effect=real_create,
    ) as create_issue:
        coordinator._create_connection_issue("no reply")
        coordinator._create_connection_issue("still no reply")
        assert create_issue.call_count == 1

        # Recovery clears the issue, which re-arms it for the next outage.
        coordinator._clear_connection_issue()
        coordinator._create_connection_issue("quiet again")
        assert create_issue.call_count == 2
