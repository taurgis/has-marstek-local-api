"""Tests for config-flow identity helpers."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.flow_helpers import (
    async_apply_entry_update,
    collect_configured_macs,
    formatted_mac_or_none,
    get_unique_id_from_device_info,
    identities_overlap,
    identity_macs_from_entry,
    identity_macs_from_mapping,
    split_devices_by_configured,
)


def test_formatted_mac_or_none_rejects_invalid() -> None:
    """Non-MAC strings must not be treated as device identity."""
    assert formatted_mac_or_none("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert formatted_mac_or_none("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"
    assert formatted_mac_or_none("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert formatted_mac_or_none("test-no-ble-mac") is None
    assert formatted_mac_or_none("not-a-mac") is None
    assert formatted_mac_or_none(123) is None
    assert formatted_mac_or_none("") is None


def test_get_unique_id_prefers_valid_ble_mac() -> None:
    """Garbage BLE values must not become unique IDs when Wi-Fi is valid."""
    assert (
        get_unique_id_from_device_info(
            {"ble_mac": "test-no-ble-mac", "wifi_mac": "11:22:33:44:55:66"}
        )
        == "11:22:33:44:55:66"
    )
    assert get_unique_id_from_device_info({"ble_mac": "not-a-mac"}) is None


def test_identity_macs_from_mapping_prefers_ble_over_bssid_fields() -> None:
    """With a BLE MAC, the Wi-Fi and legacy fields (the AP BSSID) are not identities."""
    data = {
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "wifi_mac": "11:22:33:44:55:66",
        "mac": "AA:AA:AA:AA:AA:AA",
    }
    macs = identity_macs_from_mapping(data, include_unique_id="02:de:ad:be:ef:03")
    assert macs == {"aa:bb:cc:dd:ee:ff", "02:de:ad:be:ef:03"}

    assert identity_macs_from_mapping(data, include_fallback=True) == {
        "aa:bb:cc:dd:ee:ff",
        "11:22:33:44:55:66",
        "aa:aa:aa:aa:aa:aa",
    }


def test_identity_macs_from_mapping_falls_back_without_ble_mac() -> None:
    """Without a BLE MAC the Wi-Fi and legacy MACs are the only identity left."""
    macs = identity_macs_from_mapping(
        {"ble_mac": "", "wifi_mac": "11:22:33:44:55:66", "mac": "11:22:33:44:55:66"}
    )
    assert macs == {"11:22:33:44:55:66"}


def test_batteries_on_one_access_point_do_not_share_an_identity() -> None:
    """Two batteries report the same BSSID as ``wifi_mac``; only BLE tells them apart."""
    battery_a = {"ble_mac": "02:ee:00:00:00:01", "wifi_mac": "74:83:c2:31:5c:f8"}
    battery_b = {"ble_mac": "02:ee:00:00:00:02", "wifi_mac": "74:83:c2:31:5c:f8"}
    battery_b["mac"] = battery_b["wifi_mac"]

    assert not identities_overlap(
        identity_macs_from_mapping(battery_a), identity_macs_from_mapping(battery_b)
    )


def test_identities_overlap_wifi_matches_ble_entry() -> None:
    """A Wi-Fi-only discovery still matches an entry that stored that Wi-Fi MAC."""
    entry_macs = {"aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"}
    discovered = {"11:22:33:44:55:66"}
    assert identities_overlap(entry_macs, discovered)
    assert not identities_overlap(entry_macs, {"02:de:ad:be:ef:09"})


async def test_collect_configured_macs_uses_ble_and_unique_id(
    hass: HomeAssistant,
) -> None:
    """Configured-device filtering keys on BLE, not on the shared AP BSSID."""
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
    assert configured == {"aa:bb:cc:dd:ee:ff"}
    assert identity_macs_from_entry(entry) == configured
    assert "11:22:33:44:55:66" in identity_macs_from_entry(entry, include_fallback=True)


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


def _entry_with_host(hass: HomeAssistant, host: str) -> MockConfigEntry:
    """Add a minimal entry whose host an update can change."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={"host": host, "port": 30000, "ble_mac": "AA:BB:CC:DD:EE:FF"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_loaded_entry_leaves_the_reload_to_its_listener(
    hass: HomeAssistant,
) -> None:
    """A changed, loaded entry is reloaded by its own update listener.

    Scheduling a second reload here would set the device up twice, which is
    also what Home Assistant reports as deprecated.
    """
    entry = _entry_with_host(hass, "192.168.1.200")
    entry.mock_state(hass, ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
        async_apply_entry_update(hass, entry, {**entry.data, "host": "192.168.1.201"})

    assert entry.data["host"] == "192.168.1.201"
    schedule.assert_not_called()


async def test_unchanged_loaded_entry_still_reloads(hass: HomeAssistant) -> None:
    """Nothing changed, so no listener runs, but the user asked for a retry."""
    entry = _entry_with_host(hass, "192.168.1.200")
    entry.mock_state(hass, ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
        async_apply_entry_update(hass, entry, dict(entry.data))

    schedule.assert_called_once_with(entry.entry_id)


async def test_retrying_entry_reloads_on_a_corrected_host(
    hass: HomeAssistant,
) -> None:
    """A retrying entry carries no listener, so nothing else would reload it.

    The corrected host would sit unused until Home Assistant's own
    setup-retry backoff came round, which grows to ten minutes.
    """
    entry = _entry_with_host(hass, "192.168.1.200")
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
        async_apply_entry_update(hass, entry, {**entry.data, "host": "192.168.1.201"})

    assert entry.data["host"] == "192.168.1.201"
    schedule.assert_called_once_with(entry.entry_id)
