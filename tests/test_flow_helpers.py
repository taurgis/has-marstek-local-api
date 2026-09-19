"""Tests for config-flow identity helpers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.flow_helpers import (
    collect_configured_macs,
    formatted_mac_or_none,
    identity_macs_from_entry,
    identity_macs_from_mapping,
    identities_overlap,
    split_devices_by_configured,
)


def test_formatted_mac_or_none_rejects_invalid() -> None:
    """Non-MAC strings must not be treated as device identity."""
    assert formatted_mac_or_none("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert formatted_mac_or_none("test-no-ble-mac") is None
    assert formatted_mac_or_none(123) is None
    assert formatted_mac_or_none("") is None


def test_identity_macs_from_mapping_collects_all_fields() -> None:
    """BLE, Wi-Fi, and legacy MAC fields are all stable identities."""
    macs = identity_macs_from_mapping(
        {
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "wifi_mac": "11:22:33:44:55:66",
            "mac": "AA:AA:AA:AA:AA:AA",
        },
        include_unique_id="02:de:ad:be:ef:03",
    )
    assert macs == {
        "aa:bb:cc:dd:ee:ff",
        "11:22:33:44:55:66",
        "aa:aa:aa:aa:aa:aa",
        "02:de:ad:be:ef:03",
    }


def test_identities_overlap_wifi_matches_ble_entry() -> None:
    """A Wi-Fi-only discovery still matches an entry that stored that Wi-Fi MAC."""
    entry_macs = {"aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"}
    discovered = {"11:22:33:44:55:66"}
    assert identities_overlap(entry_macs, discovered)
    assert not identities_overlap(entry_macs, {"02:de:ad:be:ef:09"})


async def test_collect_configured_macs_includes_wifi_and_unique_id(
    hass: HomeAssistant,
) -> None:
    """Configured-device filtering must not drop the Wi-Fi identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    configured = collect_configured_macs(hass.config_entries.async_entries(DOMAIN))
    assert "aa:bb:cc:dd:ee:ff" in configured
    assert "11:22:33:44:55:66" in configured
    assert identity_macs_from_entry(entry) == configured


def test_split_devices_marks_wifi_only_as_configured() -> None:
    """Picker hides a device whose Wi-Fi MAC is already on an entry."""
    options, already = split_devices_by_configured(
        [
            {
                "device_type": "Venus C",
                "version": 153,
                "wifi_name": "x",
                "ip": "1.2.3.4",
                "wifi_mac": "11:22:33:44:55:66",
            }
        ],
        {"11:22:33:44:55:66"},
    )
    assert options == {}
    assert already
