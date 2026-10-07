"""Unicast firmware refresh when broadcast discovery misses the device."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek import MarstekRuntimeData
from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from custom_components.marstek.helpers.domain_data import domain_data
from custom_components.marstek.scanner import MarstekScanner

_GETDEVICE_150: dict[str, Any] = {
    "ip": "1.2.3.4",
    "port": 30000,
    "ble_mac": "AA:BB:CC:DD:EE:FF",
    "mac": "11:22:33:44:55:66",
    "wifi_mac": "11:22:33:44:55:66",
    "wifi_name": "marstek",
    "device_type": "Venus A",
    "model": "Venus A",
    "version": 150,
    "firmware": "150",
}


def _venus_a_148_entry() -> MockConfigEntry:
    """Config entry stored at Venus A firmware 148."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        title="Venus A",
        data={
            "host": "1.2.3.4",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus A",
            "firmware": "148",
            "version": 148,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
            "model": "Venus A",
        },
    )


async def test_scanner_unicast_refreshes_firmware_when_broadcast_misses(
    hass: HomeAssistant,
) -> None:
    """No broadcast reply still updates 148 → 150 via unicast GetDevice."""
    entry = _venus_a_148_entry()
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    coordinator = MagicMock()
    coordinator.data = {"battery_soc": 22}
    coordinator.async_set_updated_data = MagicMock()
    coordinator.udp_client = MagicMock()
    entry.runtime_data = MarstekRuntimeData(
        coordinator=coordinator,
        device_info=dict(entry.data),
    )
    domain_data(hass).udp_clients[30000] = coordinator.udp_client
    domain_data(hass).entry_bind_ports[entry.entry_id] = 30000

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, format_mac("AA:BB:CC:DD:EE:FF"))},
        manufacturer="Marstek",
        model="Venus A",
        sw_version="148",
        name="Venus A",
    )

    scanner = MarstekScanner(hass)
    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.marstek.helpers.entry_metadata.get_device_info",
            AsyncMock(return_value=_GETDEVICE_150),
        ) as mock_get_device,
    ):
        await scanner._async_scan_impl()

    mock_get_device.assert_awaited_once()
    assert mock_get_device.await_args is not None
    assert mock_get_device.await_args.kwargs["bypass_rate_limit"] is False
    assert mock_get_device.await_args.kwargs["quiet"] is True
    assert mock_get_device.await_args.kwargs["udp_client"] is coordinator.udp_client

    assert entry.data["version"] == 150
    assert entry.data["firmware"] == "150"
    device = async_lookup_device_by_identifier(
        device_registry, (DOMAIN, format_mac("AA:BB:CC:DD:EE:FF"))
    )
    assert device is not None
    assert device.sw_version == "150"

    diagnostics = scanner.diagnostics()
    assert diagnostics["broadcast_device_count"] == 0
    assert diagnostics["unicast_refreshes"][0]["status"] == "updated"
    assert diagnostics["unicast_refreshes"][0]["version"] == 150


async def test_scanner_skips_unicast_when_broadcast_matched(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A broadcast hit already refreshed metadata; do not send another GetDevice."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)
    mock_config_entry.runtime_data = MarstekRuntimeData(
        coordinator=MagicMock(data={"battery_soc": 50}, async_set_updated_data=MagicMock()),
        device_info=dict(mock_config_entry.data),
    )
    domain_data(hass).udp_clients[30000] = MagicMock()
    domain_data(hass).entry_bind_ports[mock_config_entry.entry_id] = 30000

    scanner = MarstekScanner(hass)
    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "1.2.3.4",
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                        "device_type": "Venus",
                        "version": 3,
                    }
                ]
            ),
        ),
        patch(
            "custom_components.marstek.helpers.entry_metadata.get_device_info",
            AsyncMock(),
        ) as mock_get_device,
    ):
        await scanner._async_scan_impl()

    mock_get_device.assert_not_awaited()
    assert scanner.diagnostics()["unicast_refreshes"] == []


async def test_scanner_unicast_cooldown_skips_second_query(
    hass: HomeAssistant,
) -> None:
    """Setup's GetDevice counts as the recent query; the initial scan must not repeat it."""
    entry = _venus_a_148_entry()
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    client = MagicMock()
    domain_data(hass).udp_clients[30000] = client
    domain_data(hass).entry_bind_ports[entry.entry_id] = 30000

    scanner = MarstekScanner(hass)
    scanner.note_firmware_query(entry.entry_id)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.marstek.helpers.entry_metadata.get_device_info",
            AsyncMock(return_value=_GETDEVICE_150),
        ) as mock_get_device,
    ):
        await scanner._async_scan_impl()

    mock_get_device.assert_not_awaited()
    assert entry.data["version"] == 148


async def test_scanner_unicast_error_does_not_end_scan(
    hass: HomeAssistant,
) -> None:
    """A failing metadata write is recorded and the scan still finishes."""
    entry = _venus_a_148_entry()
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    domain_data(hass).udp_clients[30000] = MagicMock()
    domain_data(hass).entry_bind_ports[entry.entry_id] = 30000

    scanner = MarstekScanner(hass)
    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.marstek.scanner.async_refresh_entry_from_unicast",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        await scanner._async_scan_impl()

    diagnostics = scanner.diagnostics()
    assert diagnostics["last_scan_at"] is not None
    assert diagnostics["unicast_refreshes"] == [{"entry_id": entry.entry_id, "status": "error"}]
    # The attempt still counts toward the cooldown, so a broken entry is not
    # retried on every scan trigger.
    assert not scanner._unicast_cooldown_elapsed(entry.entry_id)
