"""Tests for Marstek device lookup helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import (
    async_find_marstek_device,
    async_get_loaded_marstek_entry,
    async_resolve_marstek_device,
    iter_device_config_entry_ids,
)
from custom_components.marstek.helpers.service_helpers import coerce_device_id
from tests.conftest import create_mock_client, patch_marstek_integration

DEVICE_IDENTIFIER = format_mac("AA:BB:CC:DD:EE:FF")


def test_coerce_device_id_unwraps_single_item_list() -> None:
    """Device selectors may wrap a single ID in a list."""
    assert coerce_device_id(["abc123"]) == "abc123"


def test_coerce_device_id_rejects_multiple_ids() -> None:
    """Multiple selected devices are invalid for these services."""
    with pytest.raises(vol.Invalid, match="exactly one device"):
        coerce_device_id(["abc", "def"])


def test_iter_device_config_entry_ids_prefers_singular_id() -> None:
    """HA 2026.8 devices expose config_entry_id instead of config_entries."""
    device = SimpleNamespace(
        is_composite_device=False,
        config_entry_id="entry-1",
        config_entries=set(),
    )
    assert iter_device_config_entry_ids(device) == ["entry-1"]  # type: ignore[arg-type]


def test_iter_device_config_entry_ids_composite_uses_all_entries() -> None:
    """Restored composite devices still report every split config entry."""
    device = SimpleNamespace(
        is_composite_device=True,
        config_entry_id="primary",
        config_entries={"marstek-entry", "helper-entry"},
    )
    assert set(iter_device_config_entry_ids(device)) == {  # type: ignore[arg-type]
        "marstek-entry",
        "helper-entry",
    }


def test_iter_device_config_entry_ids_legacy_config_entries() -> None:
    """HA 2024.x/2025.x DeviceEntry only has config_entries."""
    device = SimpleNamespace(
        config_entries={"legacy-entry"},
    )
    assert iter_device_config_entry_ids(device) == ["legacy-entry"]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_loaded_entry_from_config_entry_id_when_config_entries_empty(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Find the Marstek entry when only config_entry_id is populated."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)
    device = SimpleNamespace(
        is_composite_device=False,
        config_entry_id=mock_config_entry.entry_id,
        config_entries=set(),
    )

    entry = async_get_loaded_marstek_entry(hass, device)  # type: ignore[arg-type]

    assert entry is mock_config_entry


@pytest.mark.asyncio
async def test_resolve_truncated_registry_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Issue #34: a truncated 31-character device ID still resolves."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None
        truncated = device.id[:-1]
        assert len(truncated) == len(device.id) - 1

        resolved = async_resolve_marstek_device(hass, truncated)
        assert resolved.id == device.id


@pytest.mark.asyncio
async def test_resolve_mac_and_config_entry_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """MAC and config-entry ID are accepted as service targets."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None

        found_mac = async_find_marstek_device(hass, DEVICE_IDENTIFIER)
        found_raw = async_find_marstek_device(hass, "AABBCCDDEEFF")
        found_entry = async_find_marstek_device(hass, mock_config_entry.entry_id)
        assert found_mac is not None and found_mac.id == device.id
        assert found_raw is not None and found_raw.id == device.id
        assert found_entry is not None and found_entry.id == device.id


@pytest.mark.asyncio
async def test_resolve_entity_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Battery entity IDs resolve to the same device as device_id()."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, DEVICE_IDENTIFIER)}
        )
        assert device is not None
        resolved = async_resolve_marstek_device(hass, "sensor.venus_battery_level")
        assert resolved.id == device.id


@pytest.mark.asyncio
async def test_resolve_unknown_id_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unknown identifiers still raise invalid_device."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(ServiceValidationError) as err:
            async_resolve_marstek_device(hass, "174daf443b5e28edded880163c74d24")

        assert err.value.translation_key == "invalid_device"
