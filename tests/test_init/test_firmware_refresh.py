"""Firmware version refresh on setup via unicast GetDevice (issue #90)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from tests.conftest import create_mock_client, patch_marstek_integration

_VENUS_A_148: dict[str, Any] = {
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
}

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
    """Config entry stored at the reset-prone Venus A 148 firmware."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        title="Venus A",
        data=dict(_VENUS_A_148),
    )


async def test_setup_refreshes_stale_firmware_via_unicast(
    hass: HomeAssistant,
) -> None:
    """Stored 148 is replaced by a unicast GetDevice of 150; the repair clears.

    Broadcast discovery is mocked away (VLAN / no reply). Only the direct
    query supplies the new version, matching issue #90.
    """
    entry = _venus_a_148_entry()
    entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )

    with (
        patch_marstek_integration(client=client),
        patch(
            "custom_components.marstek.helpers.entry_metadata.get_device_info",
            AsyncMock(return_value=_GETDEVICE_150),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.data["version"] == 150
    assert entry.data["firmware"] == "150"
    assert entry.runtime_data.device_info["version"] == 150

    device = async_lookup_device_by_identifier(
        dr.async_get(hass),
        (DOMAIN, format_mac("AA:BB:CC:DD:EE:FF")),
        config_entry_id=entry.entry_id,
    )
    assert device is not None
    assert device.sw_version == "150"

    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, f"openapi_reset_prone_{entry.entry_id}") is None


async def test_setup_keeps_stored_firmware_when_unicast_fails(
    hass: HomeAssistant,
) -> None:
    """A failed firmware query must not block setup or drop the stored version."""
    entry = _venus_a_148_entry()
    entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )

    with (
        patch_marstek_integration(client=client),
        patch(
            "custom_components.marstek.helpers.entry_metadata.get_device_info",
            AsyncMock(side_effect=TimeoutError("timeout")),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.data["version"] == 148
    issue_registry = ir.async_get(hass)
    assert (
        issue_registry.async_get_issue(DOMAIN, f"openapi_reset_prone_{entry.entry_id}") is not None
    )


async def test_setup_ignores_unicast_identity_mismatch(
    hass: HomeAssistant,
) -> None:
    """A GetDevice reply from a different MAC must not overwrite this entry."""
    entry = _venus_a_148_entry()
    entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    other_device = {
        **_GETDEVICE_150,
        "ble_mac": "02:02:02:02:02:02",
        "mac": "02:02:02:02:02:02",
    }

    with (
        patch_marstek_integration(client=client),
        patch(
            "custom_components.marstek.helpers.entry_metadata.get_device_info",
            AsyncMock(return_value=other_device),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.data["version"] == 148
