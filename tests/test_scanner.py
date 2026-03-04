"""Tests for Marstek scanner."""

from __future__ import annotations

from datetime import datetime
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac

from custom_components.marstek import MarstekRuntimeData
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


async def test_scanner_async_setup_noop_when_initialized(hass: HomeAssistant):
    """Test scanner setup returns early when already initialized."""
    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.async_track_time_interval"
        ) as mock_track,
        patch.object(scanner, "async_scan") as mock_scan,
    ):
        mock_track.return_value = MagicMock()
        await scanner.async_setup()

        # Second call should no-op
        await scanner.async_setup()

        mock_track.assert_called_once()
        mock_scan.assert_called_once()


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


async def test_scanner_async_request_scan_debounced(hass: HomeAssistant) -> None:
    """Test async_request_scan debounces rapid scans."""
    scanner = MarstekScanner(hass)
    scanner._last_scan_monotonic = time.monotonic()

    assert scanner.async_request_scan() is False


async def test_scanner_async_request_scan_triggers(hass: HomeAssistant) -> None:
    """Test async_request_scan triggers a scan when not debounced."""
    scanner = MarstekScanner(hass)

    with patch.object(scanner, "async_scan") as mock_scan:
        assert scanner.async_request_scan() is True
        mock_scan.assert_called_once()


async def test_scanner_scan_impl_no_devices(hass: HomeAssistant):
    """Test _async_scan_impl when no devices are discovered."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=[]),
    ) as mock_discover:
        await scanner._async_scan_impl()

        mock_discover.assert_called_once()
        assert "ports" in mock_discover.call_args.kwargs


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
                        "version": 3,
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


async def test_scanner_scan_impl_discovers_devices_port_changed(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl triggers discovery flow when port changes at same IP."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, data={**mock_config_entry.data, "port": 30000})
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "1.2.3.4",  # same IP
                        "port": 30003,  # different port
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                        "device_type": "Venus",
                        "version": 3,
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

        mock_create_flow.assert_called_once()
        call_args = mock_create_flow.call_args
        assert call_args[1]["data"]["ip"] == "1.2.3.4"
        assert call_args[1]["data"]["port"] == 30003


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
                        "version": 3,
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


async def test_scanner_updates_device_metadata_and_registry(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner updates metadata, runtime data, and device registry."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    coordinator = MagicMock()
    coordinator.data = {"battery_soc": 50}
    coordinator.async_set_updated_data = MagicMock()
    mock_config_entry.runtime_data = MarstekRuntimeData(
        coordinator=coordinator,
        device_info=dict(mock_config_entry.data),
    )

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, format_mac("AA:BB:CC:DD:EE:FF"))},
        manufacturer="Marstek",
        model="Venus",
        sw_version="3",
        name="Marstek Venus",
    )

    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(
            return_value=[
                {
                    "ip": "1.2.3.4",
                    "ble_mac": "AA:BB:CC:DD:EE:FF",
                    "device_type": "VenusE 3.0",
                    "version": 147,
                    "wifi_name": "AirPort-38",
                    "wifi_mac": "11:22:33:44:55:66",
                    "model": "VenusE 3.0",
                    "firmware": "147",
                }
            ]
        ),
    ):
        await scanner._async_scan_impl()

    assert mock_config_entry.data["version"] == 147
    assert mock_config_entry.runtime_data.device_info["version"] == 147
    assert mock_config_entry.runtime_data.device_info["wifi_name"] == "AirPort-38"
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)

    device = device_registry.async_get_device(
        identifiers={(DOMAIN, format_mac("AA:BB:CC:DD:EE:FF"))}
    )
    assert device is not None
    assert device.sw_version == "147"
    assert device.model == "VenusE 3.0"


async def test_scanner_updates_metadata_in_setup_retry(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner updates metadata for SETUP_RETRY entries."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(
            return_value=[
                {
                    "ip": "1.2.3.4",
                    "ble_mac": "AA:BB:CC:DD:EE:FF",
                    "device_type": "VenusE 3.0",
                    "version": 147,
                    "wifi_name": "AirPort-38",
                }
            ]
        ),
    ):
        await scanner._async_scan_impl()

    assert mock_config_entry.data["version"] == 147
    assert mock_config_entry.data["wifi_name"] == "AirPort-38"


async def test_scanner_skips_blank_metadata_updates(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner ignores blank metadata values."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)
    updates_device = {
        "ip": "1.2.3.4",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "",
        "version": None,
        "wifi_name": "   ",
        "wifi_mac": "",
        "model": None,
        "firmware": "",
    }

    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        scanner._maybe_update_entry_metadata(mock_config_entry, updates_device)

    mock_update.assert_not_called()


async def test_scanner_skips_metadata_when_unchanged(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner does not update metadata when values are unchanged."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)
    updates_device = {
        "device_type": mock_config_entry.data.get("device_type"),
        "version": mock_config_entry.data.get("version"),
        "wifi_name": mock_config_entry.data.get("wifi_name"),
        "wifi_mac": mock_config_entry.data.get("wifi_mac"),
        "model": mock_config_entry.data.get("model"),
        "firmware": mock_config_entry.data.get("firmware"),
    }

    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        scanner._maybe_update_entry_metadata(mock_config_entry, updates_device)

    mock_update.assert_not_called()


async def test_scanner_invalid_mac_skips_registry_update(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner handles invalid MAC addresses safely."""
    bad_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            **mock_config_entry.data,
            "ble_mac": "not-a-mac",
        },
    )
    bad_entry.add_to_hass(hass)
    bad_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)
    updates_device = {
        "device_type": "VenusE 3.0",
        "version": 147,
        "wifi_name": "AirPort-38",
    }

    with (
        patch.object(hass.config_entries, "async_update_entry") as mock_update,
        patch("custom_components.marstek.scanner.format_mac", side_effect=ValueError),
        patch("custom_components.marstek.scanner.dr.async_get") as mock_dr_get,
    ):
        scanner._maybe_update_entry_metadata(bad_entry, updates_device)

    mock_update.assert_called_once()
    mock_dr_get.assert_not_called()


async def test_scanner_skips_registry_update_when_device_missing(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner handles missing device registry entries."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)
    updates_device = {
        "device_type": "VenusE 3.0",
        "version": 147,
        "wifi_name": "AirPort-38",
    }

    registry = MagicMock()
    registry.async_get_device.return_value = None

    with (
        patch.object(hass.config_entries, "async_update_entry") as mock_update,
        patch("custom_components.marstek.scanner.dr.async_get", return_value=registry) as mock_dr_get,
    ):
        scanner._maybe_update_entry_metadata(mock_config_entry, updates_device)

    mock_update.assert_called_once()
    mock_dr_get.assert_called_once_with(hass)


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
    """Test _async_scan_impl still discovers unconfigured devices without entry BLE-MAC."""
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

        mock_create_flow.assert_called_once()


async def test_scanner_scan_impl_no_matching_device(
    hass: HomeAssistant, mock_config_entry
):
    """Test _async_scan_impl triggers discovery for unconfigured devices."""
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

        mock_create_flow.assert_called_once()
        call_args = mock_create_flow.call_args
        assert call_args[1]["data"]["ip"] == "5.6.7.8"
        assert call_args[1]["data"]["ble_mac"] == "XX:XX:XX:XX:XX:XX"


async def test_scanner_scan_impl_unconfigured_debounce(hass: HomeAssistant):
    """Test unconfigured discovery is debounced across scans."""
    scanner = MarstekScanner(hass)

    devices = [
        {
            "ip": "5.6.7.8",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
        }
    ]

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(return_value=devices),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()
        await scanner._async_scan_impl()

        assert mock_create_flow.call_count == 1


async def test_scanner_scan_impl_skips_pending_flow(hass: HomeAssistant):
    """Test unconfigured discovery skips when a pending flow exists."""
    scanner = MarstekScanner(hass)

    devices = [
        {
            "ip": "5.6.7.8",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
        }
    ]

    pending = [
        {
            "context": {
                "source": "integration_discovery",
                "unique_id": "aa:bb:cc:dd:ee:ff",
            },
            "data": {"ble_mac": "AA:BB:CC:DD:EE:FF"},
        }
    ]

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(return_value=devices),
        ),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
        patch.object(
            hass.config_entries.flow,
            "async_progress_by_handler",
            return_value=pending,
        ),
    ):
        await scanner._async_scan_impl()

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


async def test_scanner_get_configured_macs_ignores_invalid(hass: HomeAssistant) -> None:
    """Test _get_configured_macs ignores invalid MAC values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": 123,
        },
    )
    entry.add_to_hass(hass)

    scanner = MarstekScanner(hass)

    configured = scanner._get_configured_macs()
    assert "aa:bb:cc:dd:ee:ff" in configured


async def test_scanner_prune_unconfigured_cache(hass: HomeAssistant) -> None:
    """Test pruning unconfigured cache when devices become configured."""
    scanner = MarstekScanner(hass)
    scanner._unconfigured_seen = {
        "aa:bb:cc:dd:ee:ff": datetime.now(),
        "11:22:33:44:55:66": datetime.now(),
    }

    scanner._prune_unconfigured_cache({"aa:bb:cc:dd:ee:ff"})

    assert "aa:bb:cc:dd:ee:ff" not in scanner._unconfigured_seen
    assert "11:22:33:44:55:66" in scanner._unconfigured_seen


async def test_scanner_has_pending_discovery_invalid_flow_mac(
    hass: HomeAssistant,
) -> None:
    """Test pending discovery ignores invalid MACs in flow data."""
    scanner = MarstekScanner(hass)

    pending = [
        {
            "context": {
                "source": "integration_discovery",
                "unique_id": None,
            },
            "data": {"ble_mac": 123},
        }
    ]

    with patch.object(
        hass.config_entries.flow,
        "async_progress_by_handler",
        return_value=pending,
    ):
        assert scanner._has_pending_discovery("AA:BB:CC:DD:EE:FF") is False


async def test_scanner_has_pending_discovery_non_integration_source(
    hass: HomeAssistant,
) -> None:
    """Test pending discovery ignores non-integration sources."""
    scanner = MarstekScanner(hass)

    pending = [
        {
            "context": {
                "source": "user",
                "unique_id": "aa:bb:cc:dd:ee:ff",
            },
            "data": {"ble_mac": "AA:BB:CC:DD:EE:FF"},
        }
    ]

    with patch.object(
        hass.config_entries.flow,
        "async_progress_by_handler",
        return_value=pending,
    ):
        assert scanner._has_pending_discovery("AA:BB:CC:DD:EE:FF") is False


async def test_scanner_has_pending_discovery_unique_id_match(
    hass: HomeAssistant,
) -> None:
    """Test pending discovery matches on unique_id."""
    scanner = MarstekScanner(hass)

    pending = [
        {
            "context": {
                "source": "integration_discovery",
                "unique_id": "aa:bb:cc:dd:ee:ff",
            },
            "data": {"ble_mac": "11:22:33:44:55:66"},
        }
    ]

    with patch.object(
        hass.config_entries.flow,
        "async_progress_by_handler",
        return_value=pending,
    ):
        assert scanner._has_pending_discovery("AA:BB:CC:DD:EE:FF") is True


async def test_scanner_has_pending_discovery_data_match(
    hass: HomeAssistant,
) -> None:
    """Test pending discovery matches on flow data MAC."""
    scanner = MarstekScanner(hass)

    pending = [
        {
            "context": {
                "source": "integration_discovery",
                "unique_id": None,
            },
            "data": {"ble_mac": "AA:BB:CC:DD:EE:FF"},
        }
    ]

    with patch.object(
        hass.config_entries.flow,
        "async_progress_by_handler",
        return_value=pending,
    ):
        assert scanner._has_pending_discovery("AA:BB:CC:DD:EE:FF") is True


async def test_scanner_should_trigger_unconfigured_invalid(hass: HomeAssistant) -> None:
    """Test invalid MACs do not trigger unconfigured discovery."""
    scanner = MarstekScanner(hass)

    assert scanner._should_trigger_unconfigured("") is False


async def test_scanner_should_trigger_unconfigured_debounce(hass: HomeAssistant) -> None:
    """Test unconfigured discovery debounces repeated triggers."""
    scanner = MarstekScanner(hass)

    assert scanner._should_trigger_unconfigured("AA:BB:CC:DD:EE:FF") is True
    assert scanner._should_trigger_unconfigured("AA:BB:CC:DD:EE:FF") is False


async def test_scanner_trigger_unconfigured_skips_missing_data(
    hass: HomeAssistant,
) -> None:
    """Test unconfigured discovery skips devices without IP or BLE MAC."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": None, "ble_mac": "AA:BB:CC:DD:EE:FF"},
        {"ip": "5.6.7.8", "ble_mac": None},
    ]

    with patch(
        "custom_components.marstek.scanner.discovery_flow.async_create_flow"
    ) as mock_create_flow:
        scanner._trigger_unconfigured_discovery(devices, set())

    mock_create_flow.assert_not_called()


async def test_scanner_trigger_unconfigured_skips_configured(
    hass: HomeAssistant,
) -> None:
    """Test unconfigured discovery skips already configured devices."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "5.6.7.8", "ble_mac": "AA:BB:CC:DD:EE:FF"},
    ]

    with patch(
        "custom_components.marstek.scanner.discovery_flow.async_create_flow"
    ) as mock_create_flow:
        scanner._trigger_unconfigured_discovery(devices, {"aa:bb:cc:dd:ee:ff"})

    mock_create_flow.assert_not_called()


async def test_scanner_trigger_unconfigured_invalid_mac_type(
    hass: HomeAssistant,
) -> None:
    """Test unconfigured discovery skips non-string MAC values."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "5.6.7.8", "ble_mac": 123},
    ]

    with patch(
        "custom_components.marstek.scanner.discovery_flow.async_create_flow"
    ) as mock_create_flow:
        scanner._trigger_unconfigured_discovery(devices, set())

    mock_create_flow.assert_not_called()


async def test_scanner_scan_impl_matched_device_missing_ip(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test _async_scan_impl skips IP change when discovered IP is missing."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": None,
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

        mock_create_flow.assert_not_called()


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
