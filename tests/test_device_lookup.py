"""Tests for Marstek device lookup helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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
    _as_main_device_entry,
    _unique_device,
    async_find_marstek_device,
    async_get_loaded_marstek_entry,
    async_get_marstek_entry,
    async_lookup_device_by_identifier,
    async_resolve_marstek_device,
    iter_device_config_entry_ids,
    require_loaded_marstek_entry,
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


def test_as_main_device_entry_skips_child_devices() -> None:
    """HA 2026.9 child devices are not Marstek battery devices."""
    assert _as_main_device_entry(None) is None
    child = SimpleNamespace(id="child-id", parent_device_id="parent-id")
    assert _as_main_device_entry(child) is None
    main = SimpleNamespace(id="main-id", parent_device_id=None)
    assert _as_main_device_entry(main) is main
    legacy = SimpleNamespace(id="legacy-id")
    assert _as_main_device_entry(legacy) is legacy


def test_lookup_device_by_identifier_falls_back_to_legacy_api() -> None:
    """HA 2025.10 DeviceRegistry only exposes async_get_device."""
    registry = MagicMock()
    expected = object()
    registry.async_get_device.return_value = expected
    found = async_lookup_device_by_identifier(registry, (DOMAIN, "aa:bb:cc:dd:ee:ff"))
    assert found is expected
    registry.async_get_device.assert_called_once_with(
        identifiers={(DOMAIN, "aa:bb:cc:dd:ee:ff")}
    )
    registry.async_get_devices.assert_not_called()


def test_lookup_device_by_identifier_uses_async_get_devices() -> None:
    """HA 2026.9 DeviceRegistry.async_get_devices is preferred."""

    class Registry20269:
        def __init__(self) -> None:
            self.async_get_devices_calls: list[tuple[object, object]] = []
            self.async_get_device = MagicMock()

        def async_get_devices(
            self,
            identifiers: set[tuple[str, str]] | None = None,
            *,
            config_entry_id: str | None = None,
        ) -> list[SimpleNamespace]:
            self.async_get_devices_calls.append((identifiers, config_entry_id))
            return [SimpleNamespace(id="dev-1", parent_device_id=None)]

    registry = Registry20269()
    found = async_lookup_device_by_identifier(
        registry,  # type: ignore[arg-type]
        (DOMAIN, "aa:bb:cc:dd:ee:ff"),
        config_entry_id="entry-1",
    )
    assert found is not None
    assert found.id == "dev-1"
    assert registry.async_get_devices_calls == [
        ({(DOMAIN, "aa:bb:cc:dd:ee:ff")}, "entry-1")
    ]
    registry.async_get_device.assert_not_called()


def test_lookup_device_by_identifier_skips_child_devices() -> None:
    """ChildDeviceEntry rows from HA 2026.9 are not Marstek batteries."""

    class Registry20269:
        def async_get_devices(
            self,
            identifiers: set[tuple[str, str]] | None = None,
            *,
            config_entry_id: str | None = None,
        ) -> list[SimpleNamespace]:
            return [SimpleNamespace(id="child", parent_device_id="parent")]

    found = async_lookup_device_by_identifier(
        Registry20269(),  # type: ignore[arg-type]
        (DOMAIN, "aa:bb:cc:dd:ee:ff"),
    )
    assert found is None


def test_lookup_device_by_identifier_uses_by_identifier_when_no_get_devices() -> None:
    """HA 2026.8 exposes async_get_device_by_identifier without async_get_devices."""

    class Registry20268:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, str], str]] = []
            self.async_get_device = MagicMock()

        def async_get_device_by_identifier(
            self, identifier: tuple[str, str], config_entry_id: str
        ) -> SimpleNamespace:
            self.calls.append((identifier, config_entry_id))
            return SimpleNamespace(id="dev-2", parent_device_id=None)

    registry = Registry20268()
    found = async_lookup_device_by_identifier(
        registry,  # type: ignore[arg-type]
        (DOMAIN, "aa:bb:cc:dd:ee:ff"),
        config_entry_id="entry-1",
    )
    assert found is not None
    assert found.id == "dev-2"
    assert registry.calls == [((DOMAIN, "aa:bb:cc:dd:ee:ff"), "entry-1")]
    registry.async_get_device.assert_not_called()


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

        device = async_lookup_device_by_identifier(
            dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER)
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

        device = async_lookup_device_by_identifier(
            dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER)
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

        device = async_lookup_device_by_identifier(
            dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER)
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


def test_iter_device_config_entry_ids_empty() -> None:
    """A device with neither config_entry_id nor config_entries yields no IDs."""
    device = SimpleNamespace()
    assert iter_device_config_entry_ids(device) == []  # type: ignore[arg-type]


def test_unique_device_rejects_two_distinct_ids() -> None:
    """Ambiguous matches must not pick an arbitrary device."""
    first = SimpleNamespace(id="aaa")
    second = SimpleNamespace(id="bbb")
    assert _unique_device([first, first]) is first  # type: ignore[list-item]
    assert _unique_device([first, second]) is None  # type: ignore[list-item]


def test_resolve_blank_id_raises() -> None:
    """Whitespace-only targets are treated as missing."""
    hass = MagicMock()
    assert async_find_marstek_device(hass, "   ") is None
    with pytest.raises(ServiceValidationError) as err:
        async_resolve_marstek_device(hass, "")
    assert err.value.translation_key == "no_device_specified"


@pytest.mark.asyncio
async def test_require_loaded_entry_skips_unloaded_and_foreign_entries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unloaded Marstek entries and other domains do not satisfy lookup."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    other = MockConfigEntry(domain="not_marstek", unique_id="other")
    other.add_to_hass(hass)
    other.mock_state(hass, ConfigEntryState.LOADED)

    unloaded = SimpleNamespace(
        is_composite_device=False,
        config_entry_id=mock_config_entry.entry_id,
        config_entries=set(),
    )
    foreign = SimpleNamespace(
        is_composite_device=False,
        config_entry_id=other.entry_id,
        config_entries=set(),
    )
    missing = SimpleNamespace(
        is_composite_device=False,
        config_entry_id="does-not-exist",
        config_entries=set(),
    )

    assert async_get_marstek_entry(hass, unloaded) is None  # type: ignore[arg-type]
    assert (
        async_get_marstek_entry(hass, unloaded, require_loaded=False)  # type: ignore[arg-type]
        is mock_config_entry
    )
    assert async_get_loaded_marstek_entry(hass, foreign) is None  # type: ignore[arg-type]
    assert async_get_loaded_marstek_entry(hass, missing) is None  # type: ignore[arg-type]
    with pytest.raises(ServiceValidationError) as err:
        require_loaded_marstek_entry(hass, missing, "missing")  # type: ignore[arg-type]
    assert err.value.translation_key == "no_config_entry"


@pytest.mark.asyncio
async def test_find_ignores_non_hex_mac_and_foreign_entity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """MAC-shaped non-hex strings and other platforms do not match."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert async_find_marstek_device(hass, "zzzzzzzzzzzz") is None
        assert async_find_marstek_device(hass, "sun.sun") is None
