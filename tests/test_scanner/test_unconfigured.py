"""Raising discovery flows for devices with no entry."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.scanner import MarstekScanner


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


async def test_scanner_get_configured_macs_includes_ignore_unique_id(
    hass: HomeAssistant,
) -> None:
    """Ignored entries store the BLE MAC as unique_id, not entry.data ble_mac."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="02:DE:AD:BE:EF:03",
        source="ignore",
        data={"unique_id": "02:DE:AD:BE:EF:03", "title": "Marstek VenusA"},
    )
    entry.add_to_hass(hass)

    scanner = MarstekScanner(hass)
    configured = scanner._get_configured_macs()
    assert "02:de:ad:be:ef:03" in configured

    scanner._unconfigured_seen = {"02:de:ad:be:ef:03": datetime.now()}
    scanner._prune_unconfigured_cache(configured)
    assert "02:de:ad:be:ef:03" not in scanner._unconfigured_seen


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


@pytest.mark.parametrize("device_type", ["VenusE", "HMG-50", "Venus E2.0"])
async def test_scanner_trigger_unconfigured_skips_venus_e2(
    hass: HomeAssistant, device_type: str
) -> None:
    """HMG-50 / Venus E2 must not create a discovery card as Venus E 3.x."""
    scanner = MarstekScanner(hass)

    devices = [
        {
            "ip": "172.28.0.29",
            "ble_mac": "02deadbeef09",
            "device_type": device_type,
            "version": 153,
        },
    ]

    with patch(
        "custom_components.marstek.scanner.discovery_flow.async_create_flow"
    ) as mock_create_flow:
        scanner._trigger_unconfigured_discovery(devices, set())

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


async def test_scanner_trigger_unconfigured_wifi_only(
    hass: HomeAssistant,
) -> None:
    """Devices that only report a Wi-Fi MAC still get a discovery card."""
    scanner = MarstekScanner(hass)

    devices = [
        {
            "ip": "5.6.7.8",
            "wifi_mac": "11:22:33:44:55:66",
            "device_type": "Venus C",
        }
    ]

    with patch(
        "custom_components.marstek.scanner.discovery_flow.async_create_flow"
    ) as mock_create_flow:
        scanner._trigger_unconfigured_discovery(devices, set())

    mock_create_flow.assert_called_once()
    assert mock_create_flow.call_args[1]["data"]["wifi_mac"] == "11:22:33:44:55:66"
