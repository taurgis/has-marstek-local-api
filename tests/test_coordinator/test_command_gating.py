"""Which Open API reads a poll cycle issues, by firmware and entity state."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.marstek.const import (
    BAT_STATUS_KEYS,
    CONF_POLL_INTERVAL_SLOW,
    DOMAIN,
    WIFI_STATUS_KEYS,
)
from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek.helpers.binary_sensor_descriptions import (
    BINARY_SENSORS,
)
from custom_components.marstek.helpers.sensor_descriptions import SENSORS
from custom_components.marstek.pymarstek.data_parser import (
    parse_bat_status_response,
    parse_wifi_status_response,
)


def test_entity_key_from_unique_id() -> None:
    """MAC-based unique IDs must yield the entity key, including underscored keys."""
    extract = MarstekDataUpdateCoordinator._entity_key_from_unique_id
    assert extract("aa:bb:cc:dd:ee:ff_bat_temp") == "bat_temp"
    assert extract("aa:bb:cc:dd:ee:ff_wifi_sta_ip") == "wifi_sta_ip"
    assert extract("no-separator") is None
    assert extract("trailing_") is None
    assert extract("") is None


def test_gated_status_keys_match_entity_descriptions() -> None:
    """Guard against drift between the coordinator key sets and entity keys.

    The coordinator only sends Wifi.GetStatus/Bat.GetStatus while an entity
    with one of these keys is enabled. The gated key sets must equal exactly
    the parser outputs that are exposed as entities: a new parser field that
    becomes an entity must be added to the key set (or it can never open the
    gate), and a renamed/removed entity key must be removed from it.
    """
    description_keys = {description.key for description in SENSORS} | {
        description.key for description in BINARY_SENSORS
    }
    bat_parser_keys = set(parse_bat_status_response({}))
    wifi_parser_keys = set(parse_wifi_status_response({}))
    assert bat_parser_keys & description_keys == BAT_STATUS_KEYS
    assert wifi_parser_keys & description_keys == WIFI_STATUS_KEYS


@pytest.mark.asyncio
async def test_coordinator_uses_current_profile_for_pv_polling(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """Coordinator polling passes the current non-PV profile to the parser seam."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E mini",
            "version": 150,
        },
    )
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_pv"] is False
    assert kwargs["include_em"] is True
    assert kwargs["profile"].family == "Venus E mini"
    assert kwargs["profile"].firmware_version == 150


@pytest.mark.asyncio
async def test_coordinator_skips_em_status_on_venus_c_153(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
) -> None:
    """HMG-50 Control 153 has no Open API EM.GetStatus server method."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusC",
            "version": 153,
        },
    )
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_em"] is False
    assert kwargs["profile"].hmg50_control is True
    assert kwargs["profile"].supports_em_status is False


@pytest.mark.asyncio
async def test_coordinator_skips_wifi_status_when_disabled(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that Wifi.GetStatus is skipped when WiFi diagnostics are disabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 0})

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_wifi_rssi",
        config_entry=mock_config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is False


@pytest.mark.asyncio
async def test_coordinator_skips_bat_status_when_disabled(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that Bat.GetStatus is skipped when battery detail entities are disabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 0})

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is False


@pytest.mark.asyncio
async def test_coordinator_includes_bat_status_when_entity_enabled(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that Bat.GetStatus resumes when a battery detail entity is enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 0})

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is True


@pytest.mark.asyncio
async def test_coordinator_skips_bat_status_on_reset_prone_firmware(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Reset-prone firmware never sends Bat.GetStatus, even if details are enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
        options={CONF_POLL_INTERVAL_SLOW: 0},
    )

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is False
    mock_udp_client.set_openapi_reset_prone.assert_called_with(
        "1.2.3.4", True, owner=mock_config_entry.entry_id
    )
    mock_udp_client.set_openapi_retransmit_safe.assert_called_with("1.2.3.4", False)


@pytest.mark.asyncio
async def test_coordinator_includes_bat_status_for_binary_sensor_entity(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that an enabled permission binary sensor also opens the gate."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 0})

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_charg_flag",
        config_entry=mock_config_entry,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is True


@pytest.mark.asyncio
async def test_coordinator_non_gated_entities_do_not_open_bat_gate(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that enabled entities outside the gated key set keep the gate shut.

    bat_cap (ES.GetStatus-backed) and battery_soc are close cousins of the
    gated keys; a unique-id key-parsing regression must not let them
    re-enable the reset-triggering Bat.GetStatus call.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 0})

    entity_registry = er.async_get(hass)
    for key in ("battery_soc", "bat_cap", "em_total_power"):
        entity_registry.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=f"aa:bb:cc:dd:ee:ff_{key}",
            config_entry=mock_config_entry,
        )
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is False


@pytest.mark.asyncio
async def test_coordinator_bat_status_respects_slow_interval(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that Bat.GetStatus is not repeated before the slow interval elapses."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 300}
    )

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    # First cycle fetches battery details (never fetched before)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is True

    # Second cycle right after must skip it (300s interval not elapsed)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is False


@pytest.mark.asyncio
async def test_coordinator_wifi_status_respects_slow_interval(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that Wifi.GetStatus is not repeated before the slow interval elapses."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 300}
    )

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_wifi_rssi",
        config_entry=mock_config_entry,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    # First cycle fetches WiFi status (never fetched before)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is True

    # Second cycle right after must skip it (300s interval not elapsed)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is False


@pytest.mark.asyncio
async def test_coordinator_fetches_bat_status_promptly_after_enable(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that enabling a battery detail entity triggers Bat.GetStatus
    on the next update instead of waiting out a slow-tier cycle that
    skipped the request."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 300}
    )

    entity_registry = er.async_get(hass)
    registry_entry = entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    # First cycle: entity disabled, Bat.GetStatus skipped
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is False

    # User enables the entity; the next cycle must fetch immediately
    # (battery details were never actually fetched)
    entity_registry.async_update_entity(registry_entry.entity_id, disabled_by=None)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is True


@pytest.mark.asyncio
async def test_coordinator_bat_and_wifi_slow_timers_are_independent(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """Test that a WiFi fetch does not delay the first battery detail fetch."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 300}
    )

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_wifi_rssi",
        config_entry=mock_config_entry,
    )
    bat_entry = entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    # First cycle fetches WiFi but skips battery details (entity disabled)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is True
    assert kwargs["include_bat"] is False

    # Enabling the battery entity fetches it on the next cycle while the
    # recently-fetched WiFi status keeps respecting its own interval
    entity_registry.async_update_entity(bat_entry.entity_id, disabled_by=None)
    await coordinator._async_update_data()
    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_bat"] is True
    assert kwargs["include_wifi"] is False


@pytest.mark.asyncio
async def test_coordinator_first_slow_fetch_when_monotonic_below_interval(
    hass: HomeAssistant, mock_config_entry, mock_udp_client
):
    """First WiFi/Bat fetch must run even if monotonic time is below the interval.

    `_last_*_fetch` used to start at 0.0, so a host whose monotonic clock was
    still under the 300s slow interval (fresh CI runners) skipped the first
    fetch even with an enabled entity.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL_SLOW: 300}
    )

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_wifi_rssi",
        config_entry=mock_config_entry,
    )
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff_bat_temp",
        config_entry=mock_config_entry,
    )

    coordinator = MarstekDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_udp_client,
        "1.2.3.4",
    )

    with patch("custom_components.marstek.coordinator.time.monotonic", return_value=10.0):
        await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is True
    assert kwargs["include_bat"] is True

    with patch("custom_components.marstek.coordinator.time.monotonic", return_value=11.0):
        await coordinator._async_update_data()

    kwargs = mock_udp_client.get_device_status.call_args.kwargs
    assert kwargs["include_wifi"] is False
    assert kwargs["include_bat"] is False
