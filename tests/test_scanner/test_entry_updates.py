"""Applying a scan result to an existing config entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.scanner import MarstekScanner, _build_discovery_flow_data


def test_build_discovery_flow_data_includes_valid_port() -> None:
    """Test discovery flow data includes a valid discovered port."""
    flow_data = _build_discovery_flow_data(
        {
            "ip": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "port": 30030,
        }
    )

    assert flow_data["port"] == 30030


def test_build_discovery_flow_data_omits_invalid_port() -> None:
    """Test discovery flow data omits invalid port values."""
    flow_data = _build_discovery_flow_data(
        {
            "ip": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "port": "invalid",
        }
    )

    assert "port" not in flow_data


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
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, "port": 30000}
    )
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
                        "ble_mac": "02:02:02:02:02:02",  # Different BLE-MAC
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
        assert call_args[1]["data"]["ble_mac"] == "02:02:02:02:02:02"


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


async def test_scanner_scan_impl_wifi_only_identity_updates_ip(
    hass: HomeAssistant,
) -> None:
    """Wi-Fi-identified entries recover IP changes without a BLE MAC."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={
            "host": "1.2.3.4",
            "wifi_mac": "11:22:33:44:55:66",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "5.6.7.8",
                        "wifi_mac": "11:22:33:44:55:66",
                        "device_type": "Venus C",
                        "version": 153,
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
    assert mock_create_flow.call_args[1]["data"]["ip"] == "5.6.7.8"
    assert mock_create_flow.call_args[1]["data"]["wifi_mac"] == "11:22:33:44:55:66"
