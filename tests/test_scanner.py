"""Tests for Marstek scanner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.scanner import MarstekScanner


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the scanner singleton before each test."""
    MarstekScanner._scanner = None
    yield
    MarstekScanner._scanner = None


async def test_scanner_singleton(hass: HomeAssistant):
    """Test that async_get returns singleton instance."""
    scanner1 = MarstekScanner.async_get(hass)
    scanner2 = MarstekScanner.async_get(hass)

    assert scanner1 is scanner2


async def test_scanner_init(hass: HomeAssistant):
    """Test scanner initialization."""
    scanner = MarstekScanner(hass)

    assert scanner._hass is hass
    assert scanner._track_interval is None


async def test_scanner_async_setup(hass: HomeAssistant):
    """Test scanner setup starts interval tracking."""
    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.async_track_time_interval"
        ) as mock_track,
        patch.object(scanner, "async_scan") as mock_scan,
    ):
        mock_track.return_value = MagicMock()

        await scanner.async_setup()

        mock_track.assert_called_once()
        mock_scan.assert_called_once()
        assert scanner._track_interval is not None


async def test_scanner_async_scan_creates_background_task(hass: HomeAssistant):
    """Test async_scan creates background task."""
    scanner = MarstekScanner(hass)
    captured_coro = None

    def capture_task(coro, **kwargs):
        nonlocal captured_coro
        captured_coro = coro

    with patch.object(hass, "async_create_task", side_effect=capture_task):
        scanner.async_scan()

    # Verify task was created and clean up the coroutine
    assert captured_coro is not None
    # Close the coroutine to prevent warning (we don't need to run it)
    captured_coro.close()


async def test_scanner_scan_impl_no_devices(hass: HomeAssistant):
    """Test _async_scan_impl when no devices are discovered."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=[]),
    ) as mock_discover:
        await scanner._async_scan_impl()

        mock_discover.assert_called_once()


async def test_scanner_scan_impl_discovers_devices_no_ip_change(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl discovers devices with no IP change."""
    mock_config_entry.add_to_hass(hass)
    # Set state to LOADED by mocking the property
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "1.2.3.4",  # Same IP as stored
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                        "device_type": "Venus",
                        "version": "3",
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        # No IP change, so discovery flow should not be created
        mock_create_flow.assert_not_called()


async def test_scanner_scan_impl_discovers_devices_ip_changed(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl discovers devices with IP change."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "5.6.7.8",  # Different IP!
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                        "device_type": "Venus",
                        "version": "3",
                        "wifi_name": "TestWifi",
                        "wifi_mac": "11:22:33:44:55:66",
                        "mac": "AA:AA:AA:AA:AA:AA",
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        # IP changed, so discovery flow should be created
        mock_create_flow.assert_called_once()
        call_args = mock_create_flow.call_args
        assert call_args[0][0] is hass
        assert call_args[0][1] == DOMAIN
        assert call_args[1]["data"]["ip"] == "5.6.7.8"
        assert call_args[1]["data"]["ble_mac"] == "AA:BB:CC:DD:EE:FF"


async def test_scanner_scan_impl_entry_in_setup_retry(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl handles entries in SETUP_RETRY state."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "5.6.7.8",  # Different IP - device came back on new IP
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                        "device_type": "Venus",
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        # Should still detect IP change for SETUP_RETRY entries
        mock_create_flow.assert_called_once()


async def test_scanner_scan_impl_skips_not_loaded_entry(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl skips entries not in LOADED/SETUP_RETRY state."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "5.6.7.8",
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        # Entry not loaded, should not trigger discovery flow
        mock_create_flow.assert_not_called()


async def test_scanner_scan_impl_entry_missing_ble_mac(hass: HomeAssistant):
    """Test _async_scan_impl skips entries without BLE-MAC."""
    # Entry without ble_mac
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-no-ble-mac",
        data={
            "host": "1.2.3.4",
            # No ble_mac!
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(return_value=[{"ip": "5.6.7.8", "ble_mac": "AA:BB:CC:DD:EE:FF"}]),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        # Entry has no ble_mac, should skip
        mock_create_flow.assert_not_called()


async def test_scanner_scan_impl_no_matching_device(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl when discovered device doesn't match any entry."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "5.6.7.8",
                        "ble_mac": "XX:XX:XX:XX:XX:XX",  # Different BLE-MAC!
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        # No matching device, should not trigger discovery flow
        mock_create_flow.assert_not_called()


async def test_scanner_scan_impl_exception_handling(hass: HomeAssistant):
    """Test _async_scan_impl handles exceptions gracefully."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(side_effect=Exception("Network error")),
    ):
        # Should not raise - exceptions are caught
        await scanner._async_scan_impl()


async def test_scanner_find_device_by_ble_mac_found(hass: HomeAssistant):
    """Test _find_device_by_ble_mac finds matching device."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"},
        {"ip": "5.6.7.8", "ble_mac": "11:22:33:44:55:66"},
    ]

    result = scanner._find_device_by_ble_mac(devices, "AA:BB:CC:DD:EE:FF", "Test Entry")

    assert result is not None
    assert result["ip"] == "1.2.3.4"


async def test_scanner_find_device_by_ble_mac_case_insensitive(hass: HomeAssistant):
    """Test _find_device_by_ble_mac is case insensitive."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4", "ble_mac": "aa:bb:cc:dd:ee:ff"},  # lowercase
    ]

    # Search with uppercase
    result = scanner._find_device_by_ble_mac(devices, "AA:BB:CC:DD:EE:FF", "Test Entry")

    assert result is not None
    assert result["ip"] == "1.2.3.4"


async def test_scanner_find_device_by_ble_mac_not_found(hass: HomeAssistant):
    """Test _find_device_by_ble_mac returns None when not found."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4", "ble_mac": "11:22:33:44:55:66"},
    ]

    result = scanner._find_device_by_ble_mac(devices, "AA:BB:CC:DD:EE:FF", "Test Entry")

    assert result is None


async def test_scanner_find_device_by_ble_mac_device_without_ble_mac(
    hass: HomeAssistant,
):
    """Test _find_device_by_ble_mac skips devices without ble_mac."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4"},  # No ble_mac
        {"ip": "5.6.7.8", "ble_mac": None},  # ble_mac is None
    ]

    result = scanner._find_device_by_ble_mac(devices, "AA:BB:CC:DD:EE:FF", "Test Entry")

    assert result is None


async def test_scanner_scan_impl_none_devices(hass: HomeAssistant):
    """Test _async_scan_impl when discover_devices returns None."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=None),
    ):
        # Should not raise
        await scanner._async_scan_impl()


async def test_scanner_async_unload_cancels_task(hass: HomeAssistant):
    """Test async_unload cancels running scan task."""
    import asyncio

    scanner = MarstekScanner(hass)

    # Setup the scanner first
    with (
        patch(
            "custom_components.marstek.scanner.async_track_time_interval"
        ) as mock_track,
        patch.object(scanner, "async_scan"),
    ):
        mock_cancel = MagicMock()
        mock_track.return_value = mock_cancel

        await scanner.async_setup()

        # Simulate a long-running scan task
        async def slow_scan():
            await asyncio.sleep(10)

        scanner._scan_task = asyncio.create_task(slow_scan())

        # Unload should cancel the task
        await scanner.async_unload()

        assert scanner._track_interval is None
        assert scanner._scan_task is None
        mock_cancel.assert_called_once()


async def test_scanner_async_scan_skips_if_previous_running(hass: HomeAssistant):
    """Test async_scan skips if previous scan task is still running."""
    import asyncio

    scanner = MarstekScanner(hass)

    # Create a task that hasn't completed
    async def slow_scan():
        await asyncio.sleep(10)

    scanner._scan_task = asyncio.create_task(slow_scan())

    # Try to start a new scan - should skip
    scanner.async_scan()

    # Should still be the original task (not replaced)
    assert not scanner._scan_task.done()

    # Cleanup
    scanner._scan_task.cancel()
    try:
        await scanner._scan_task
    except asyncio.CancelledError:
        pass
