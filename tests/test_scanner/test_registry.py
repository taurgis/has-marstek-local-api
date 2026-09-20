"""Device registry metadata and identity lookups."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek import MarstekRuntimeData
from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from custom_components.marstek.helpers.flow_helpers import identity_macs_from_mapping
from custom_components.marstek.scanner import MarstekScanner


async def test_scanner_updates_device_metadata_and_registry(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test scanner updates metadata, runtime data, and device registry."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 145,
            "model": "VenusE 3.0",
            "firmware": "145",
        },
    )
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
        model="VenusE 3.0",
        sw_version="145",
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

    device = async_lookup_device_by_identifier(
        device_registry, (DOMAIN, format_mac("AA:BB:CC:DD:EE:FF"))
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


async def test_scanner_skips_blank_metadata_updates(hass: HomeAssistant, mock_config_entry) -> None:
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
    """Test scanner handles invalid MAC addresses safely.

    Entry metadata is still refreshed, but with no field holding a valid
    6-octet MAC there is no stable identifier to look the device up by, so
    the registry is left alone rather than touched under a made-up id.
    """
    bad_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            **mock_config_entry.data,
            "ble_mac": "not-a-mac",
            "mac": "",
            "wifi_mac": "still-not-a-mac",
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
        patch("custom_components.marstek.scanner.dr.async_get") as mock_dr_get,
    ):
        scanner._maybe_update_entry_metadata(bad_entry, updates_device)

    mock_update.assert_called_once()
    mock_dr_get.assert_not_called()


async def test_scanner_registry_update_falls_back_to_next_mac_field(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """A junk ble_mac does not lose the device; the next identity field wins."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={**mock_config_entry.data, "ble_mac": "not-a-mac"},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)

    with (
        patch.object(hass.config_entries, "async_update_entry"),
        patch("custom_components.marstek.scanner.dr.async_get") as mock_dr_get,
    ):
        scanner._maybe_update_entry_metadata(entry, {"version": 150})

    mock_dr_get.assert_called_once()


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
        patch(
            "custom_components.marstek.scanner.dr.async_get", return_value=registry
        ) as mock_dr_get,
    ):
        scanner._maybe_update_entry_metadata(mock_config_entry, updates_device)

    mock_update.assert_called_once()
    mock_dr_get.assert_called_once_with(hass)


async def test_scanner_find_device_by_identity_found(hass: HomeAssistant):
    """Test _find_device_by_identity finds matching device."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"},
        {"ip": "5.6.7.8", "ble_mac": "11:22:33:44:55:66"},
    ]

    result = scanner._find_device_by_identity(
        devices,
        identity_macs_from_mapping({"ble_mac": "AA:BB:CC:DD:EE:FF"}),
        "Test Entry",
    )

    assert result is not None
    assert result["ip"] == "1.2.3.4"


async def test_scanner_find_device_by_identity_case_insensitive(hass: HomeAssistant):
    """Test _find_device_by_identity is case insensitive."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4", "ble_mac": "aa:bb:cc:dd:ee:ff"},  # lowercase
    ]

    # Search with uppercase
    result = scanner._find_device_by_identity(
        devices,
        identity_macs_from_mapping({"ble_mac": "AA:BB:CC:DD:EE:FF"}),
        "Test Entry",
    )

    assert result is not None
    assert result["ip"] == "1.2.3.4"


async def test_scanner_find_device_by_identity_not_found(hass: HomeAssistant):
    """Test _find_device_by_identity returns None when not found."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4", "ble_mac": "11:22:33:44:55:66"},
    ]

    result = scanner._find_device_by_identity(
        devices,
        identity_macs_from_mapping({"ble_mac": "AA:BB:CC:DD:EE:FF"}),
        "Test Entry",
    )

    assert result is None


async def test_scanner_find_device_by_identity_device_without_ble_mac(
    hass: HomeAssistant,
):
    """Test _find_device_by_identity skips devices without ble_mac."""
    scanner = MarstekScanner(hass)

    devices = [
        {"ip": "1.2.3.4"},  # No ble_mac
        {"ip": "5.6.7.8", "ble_mac": None},  # ble_mac is None
    ]

    result = scanner._find_device_by_identity(
        devices,
        identity_macs_from_mapping({"ble_mac": "AA:BB:CC:DD:EE:FF"}),
        "Test Entry",
    )

    assert result is None


async def test_scanner_ignores_malformed_discovered_mac(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """One bad BLE MAC must not abort matching the rest of the scan."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 145,
        },
    )
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)
    mock_config_entry.runtime_data = MarstekRuntimeData(
        coordinator=MagicMock(data={"battery_soc": 50}, async_set_updated_data=MagicMock()),
        device_info=dict(mock_config_entry.data),
    )

    scanner = MarstekScanner(hass)
    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(
            return_value=[
                {
                    "ip": "9.9.9.9",
                    "ble_mac": "not-a-mac",
                    "device_type": "VenusE 3.0",
                    "version": 145,
                },
                {
                    "ip": "1.2.3.4",
                    "ble_mac": "AA:BB:CC:DD:EE:FF",
                    "device_type": "VenusE 3.0",
                    "version": 147,
                    "wifi_name": "AirPort-38",
                },
            ]
        ),
    ):
        await scanner._async_scan_impl()

    assert mock_config_entry.data["version"] == 147
    assert mock_config_entry.data["wifi_name"] == "AirPort-38"
