"""Scanner reloads config entries only when firmware setup capabilities change."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN, MODE_UPS, SELECTABLE_BASE_MODES
from custom_components.marstek.diagnostics import async_get_config_entry_diagnostics
from custom_components.marstek.helpers.device_lookup import (
    async_lookup_device_by_identifier,
    iter_device_config_entry_ids,
)
from custom_components.marstek.scanner import MarstekScanner
from tests.conftest import create_mock_client, patch_marstek_integration

BLE_MAC = "AA:BB:CC:DD:EE:FF"
SYS_NUMBER_KEY = "depth_of_discharge"
SYS_SWITCH_KEYS = ("bluetooth_advertising", "panel_led")
SYS_ENTITY_KEYS = (SYS_NUMBER_KEY, *SYS_SWITCH_KEYS)
OPERATING_MODE_KEY = "operating_mode"


@pytest.fixture(autouse=True)
def reset_scanner_singleton() -> Any:
    """Reset the scanner singleton before each test."""
    MarstekScanner._scanner = None
    yield
    MarstekScanner._scanner = None


def _unique_id(key: str, ble_mac: str = BLE_MAC) -> str:
    return f"{format_mac(ble_mac)}_{key}"


def _platform_for_key(key: str) -> str:
    if key == SYS_NUMBER_KEY:
        return "number"
    if key == OPERATING_MODE_KEY:
        return "select"
    return "switch"


def _config_entry(*, version: Any = 149, device_type: str = "VenusE 3.0") -> MockConfigEntry:
    data: dict[str, Any] = {
        "host": "1.2.3.4",
        "ble_mac": BLE_MAC,
        "mac": BLE_MAC,
        "device_type": device_type,
        "wifi_name": "AirPort-38",
        "wifi_mac": "11:22:33:44:55:66",
        "model": device_type,
        "firmware": str(version) if version is not None else "",
    }
    if version is not None:
        data["version"] = version
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=format_mac(BLE_MAC),
        data=data,
        title="Marstek Venus E",
    )


def _discovered_device(**overrides: Any) -> dict[str, Any]:
    device: dict[str, Any] = {
        "ip": "1.2.3.4",
        "ble_mac": BLE_MAC,
        "device_type": "VenusE 3.0",
        "version": 150,
        "wifi_name": "AirPort-38",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "VenusE 3.0",
        "firmware": "150",
    }
    device.update(overrides)
    return device


def _sys_client() -> Any:
    return create_mock_client(
        status={
            "battery_soc": 55,
            "device_mode": "auto",
            "battery_power": -250,
            "battery_status": "discharging",
        }
    )


@asynccontextmanager
async def _loaded_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> AsyncIterator[MockConfigEntry]:
    """Set up a config entry under the standard UDP/scanner patches."""
    entry.add_to_hass(hass)
    with patch_marstek_integration(client=_sys_client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        yield entry


async def _scan_devices(hass: HomeAssistant, devices: list[dict[str, Any]]) -> None:
    """Run one scanner pass with the given discovery results."""
    scanner = MarstekScanner(hass)
    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=devices),
    ):
        await scanner._async_scan_impl()
    await hass.async_block_till_done()


@asynccontextmanager
async def _count_reloads(hass: HomeAssistant) -> AsyncIterator[list[str]]:
    """Count config-entry reloads while a scanner pass runs."""
    original_reload = hass.config_entries.async_reload
    reload_ids: list[str] = []

    async def _reload(entry_id: str) -> bool:
        reload_ids.append(entry_id)
        return await original_reload(entry_id)

    with patch.object(hass.config_entries, "async_reload", side_effect=_reload):
        yield reload_ids


def _entity_id(hass: HomeAssistant, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(_platform_for_key(key), DOMAIN, _unique_id(key))


def _assert_sys_entities(hass: HomeAssistant, *, present: bool) -> None:
    registry = er.async_get(hass)
    for key in SYS_ENTITY_KEYS:
        entity_id = _entity_id(hass, key)
        if present:
            assert entity_id is not None
            assert hass.states.get(entity_id) is not None
            registry_entry = registry.async_get(entity_id)
            assert registry_entry is not None
            assert registry_entry.unique_id == _unique_id(key)
        else:
            assert entity_id is None
            for existing in hass.states.async_entity_ids():
                assert key not in existing


def _operating_mode_options(hass: HomeAssistant) -> list[str]:
    entity_id = _entity_id(hass, OPERATING_MODE_KEY)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    options = state.attributes.get("options")
    assert isinstance(options, list)
    return options


def _device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    device = async_lookup_device_by_identifier(
        dr.async_get(hass),
        (DOMAIN, format_mac(BLE_MAC)),
        config_entry_id=entry.entry_id,
    )
    assert device is not None
    assert entry.entry_id in iter_device_config_entry_ids(device)
    return device


def _assert_sw_version(hass: HomeAssistant, entry: MockConfigEntry, version: Any) -> None:
    device = _device(hass, entry)
    assert device.sw_version == str(version)
    assert entry.data.get("version") == version


async def test_scanner_firmware_149_to_150_reloads_once_and_unlocks_capabilities(
    hass: HomeAssistant,
) -> None:
    """Venus E 149 → 150 persists metadata, reloads once, and unlocks UPS/SYS."""
    entry = _config_entry(version=149)
    async with _loaded_entry(hass, entry):
        _assert_sys_entities(hass, present=False)
        assert MODE_UPS not in _operating_mode_options(hass)
        operating_mode_id = _entity_id(hass, OPERATING_MODE_KEY)

        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(hass, [_discovered_device(version=150, firmware="150")])

        assert reload_ids == [entry.entry_id]
        assert entry.state == ConfigEntryState.LOADED
        _assert_sw_version(hass, entry, 150)
        _assert_sys_entities(hass, present=True)
        options = _operating_mode_options(hass)
        for mode in SELECTABLE_BASE_MODES:
            assert mode in options
        assert MODE_UPS in options
        assert _entity_id(hass, OPERATING_MODE_KEY) == operating_mode_id


async def test_scanner_firmware_150_to_149_removes_unsupported_sys(
    hass: HomeAssistant,
) -> None:
    """A capability downgrade reloads once and drops SYS without removing the device."""
    entry = _config_entry(version=150)
    async with _loaded_entry(hass, entry):
        _assert_sys_entities(hass, present=True)
        device_before = _device(hass, entry)
        operating_mode_id = _entity_id(hass, OPERATING_MODE_KEY)

        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(hass, [_discovered_device(version=149, firmware="149")])

        assert reload_ids == [entry.entry_id]
        assert entry.state == ConfigEntryState.LOADED
        _assert_sw_version(hass, entry, 149)
        _assert_sys_entities(hass, present=False)
        assert MODE_UPS not in _operating_mode_options(hass)
        assert _entity_id(hass, OPERATING_MODE_KEY) == operating_mode_id
        device_after = _device(hass, entry)
        assert device_after.id == device_before.id
        assert hass.states.get(operating_mode_id) is not None


async def test_scanner_firmware_150_to_151_updates_without_reload(
    hass: HomeAssistant,
) -> None:
    """Capability-equivalent firmware updates metadata and sw_version only."""
    entry = _config_entry(version=150)
    async with _loaded_entry(hass, entry):
        _assert_sys_entities(hass, present=True)

        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(hass, [_discovered_device(version=151, firmware="151")])

        assert reload_ids == []
        _assert_sw_version(hass, entry, 151)
        _assert_sys_entities(hass, present=True)
        assert MODE_UPS in _operating_mode_options(hass)
        assert entry.runtime_data.device_info["version"] == 151


async def test_scanner_wifi_metadata_does_not_reload(hass: HomeAssistant) -> None:
    """Wi-Fi metadata changes keep the current firmware capabilities loaded."""
    entry = _config_entry(version=150)
    async with _loaded_entry(hass, entry):
        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(
                hass,
                [
                    _discovered_device(
                        version=150,
                        wifi_name="NewNetwork",
                        wifi_mac="DE:AD:BE:EF:00:01",
                    )
                ],
            )

        assert reload_ids == []
        assert entry.data["wifi_name"] == "NewNetwork"
        assert entry.data["wifi_mac"] == "DE:AD:BE:EF:00:01"
        _assert_sw_version(hass, entry, 150)
        _assert_sys_entities(hass, present=True)


async def test_scanner_equivalent_model_does_not_reload(hass: HomeAssistant) -> None:
    """Equivalent model strings do not reload when capabilities stay the same."""
    entry = _config_entry(version=150, device_type="VenusE 3.0")
    async with _loaded_entry(hass, entry):
        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(
                hass,
                [
                    _discovered_device(
                        device_type="Venus E 3.0",
                        model="Venus E 3.0",
                        version=150,
                    )
                ],
            )

        assert reload_ids == []
        assert entry.data["device_type"] == "Venus E 3.0"
        _assert_sw_version(hass, entry, 150)
        _assert_sys_entities(hass, present=True)


async def test_scanner_repeated_identical_discovery_is_idempotent(
    hass: HomeAssistant,
) -> None:
    """Repeating the same discovery result does not update or reload again."""
    entry = _config_entry(version=150)
    async with _loaded_entry(hass, entry):
        await _scan_devices(hass, [_discovered_device(version=150, firmware="150")])

        with patch.object(hass.config_entries, "async_update_entry") as mock_update:
            async with _count_reloads(hass) as reload_ids:
                await _scan_devices(hass, [_discovered_device(version=150, firmware="150")])

        mock_update.assert_not_called()
        assert reload_ids == []
        _assert_sys_entities(hass, present=True)


async def test_scanner_unknown_firmware_removes_unlocked_capabilities(
    hass: HomeAssistant,
) -> None:
    """Crossing from a capable profile to unparseable firmware uses the safe profile."""
    entry = _config_entry(version=150)
    async with _loaded_entry(hass, entry):
        _assert_sys_entities(hass, present=True)

        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(
                hass,
                [_discovered_device(version="not-a-version", firmware="not-a-version")],
            )

        assert reload_ids == [entry.entry_id]
        assert entry.data["version"] == "not-a-version"
        _assert_sw_version(hass, entry, "not-a-version")
        _assert_sys_entities(hass, present=False)
        assert MODE_UPS not in _operating_mode_options(hass)


async def test_diagnostics_follow_live_firmware_transition(hass: HomeAssistant) -> None:
    """Diagnostics switch from the legacy profile to firmware-150 features."""
    entry = _config_entry(version=149)
    async with _loaded_entry(hass, entry):
        before = await async_get_config_entry_diagnostics(hass, entry)
        assert before["firmware_profile"] == {
            "family": "Venus E",
            "firmware_version": 149,
            "firmware_known": True,
            "supports_pv": False,
            "supports_sys_dod": False,
            "supports_sys_ble_advertising": False,
            "supports_sys_led": False,
            "supports_ups": False,
            "supports_em_status": True,
            "max_manual_schedule_slot": 9,
            "control_generation": 149,
            "openapi_reset_prone": True,
            "parallel_requests_safe": False,
            "openapi_wifi_retransmit_safe": False,
        }

        await _scan_devices(hass, [_discovered_device(version=150, firmware="150")])

        after = await async_get_config_entry_diagnostics(hass, entry)
        assert after["firmware_profile"] == {
            "family": "Venus E",
            "firmware_version": 150,
            "firmware_known": True,
            "supports_pv": False,
            "supports_sys_dod": True,
            "supports_sys_ble_advertising": True,
            "supports_sys_led": True,
            "supports_ups": True,
            "supports_em_status": True,
            "max_manual_schedule_slot": 9,
            "control_generation": 150,
            "openapi_reset_prone": False,
            "parallel_requests_safe": True,
            "openapi_wifi_retransmit_safe": True,
        }


async def test_unique_ids_survive_upgrade_downgrade_upgrade(hass: HomeAssistant) -> None:
    """BLE-MAC unique IDs stay stable and do not duplicate across firmware cycles."""
    entry = _config_entry(version=149)
    async with _loaded_entry(hass, entry):
        operating_mode_id = _entity_id(hass, OPERATING_MODE_KEY)
        assert operating_mode_id is not None

        await _scan_devices(hass, [_discovered_device(version=150, firmware="150")])
        first_sys_ids = {key: _entity_id(hass, key) for key in SYS_ENTITY_KEYS}
        assert all(entity_id is not None for entity_id in first_sys_ids.values())
        assert _entity_id(hass, OPERATING_MODE_KEY) == operating_mode_id

        await _scan_devices(hass, [_discovered_device(version=149, firmware="149")])
        _assert_sys_entities(hass, present=False)
        assert _entity_id(hass, OPERATING_MODE_KEY) == operating_mode_id

        await _scan_devices(hass, [_discovered_device(version=150, firmware="150")])
        registry = er.async_get(hass)
        for key in SYS_ENTITY_KEYS:
            entity_id = _entity_id(hass, key)
            assert entity_id == first_sys_ids[key]
            matches = [
                item
                for item in registry.entities.values()
                if item.unique_id == _unique_id(key) and item.platform == DOMAIN
            ]
            assert len(matches) == 1
        assert _entity_id(hass, OPERATING_MODE_KEY) == operating_mode_id
        mode_matches = [
            item
            for item in registry.entities.values()
            if item.unique_id == _unique_id(OPERATING_MODE_KEY) and item.platform == DOMAIN
        ]
        assert len(mode_matches) == 1


async def test_capability_removal_keeps_unrelated_entities(hass: HomeAssistant) -> None:
    """Downgrade removes SYS controls but keeps the device and core sensors."""
    entry = _config_entry(version=150)
    async with _loaded_entry(hass, entry):
        battery_matches = [
            entity_id for entity_id in hass.states.async_entity_ids() if "battery" in entity_id
        ]
        assert battery_matches

        await _scan_devices(hass, [_discovered_device(version=149, firmware="149")])

        _assert_sys_entities(hass, present=False)
        _device(hass, entry)
        for entity_id in battery_matches:
            assert hass.states.get(entity_id) is not None
        assert _entity_id(hass, OPERATING_MODE_KEY) is not None


async def test_scanner_reloads_when_reset_prone_clears_without_capability_change(
    hass: HomeAssistant,
) -> None:
    """Unknown-family 149 to 150 does not change SYS/UPS but must reload safety."""
    entry = _config_entry(version=149, device_type="Marstek Energy Storage")
    async with _loaded_entry(hass, entry):
        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(DOMAIN, f"openapi_reset_prone_{entry.entry_id}")
            is not None
        )

        async with _count_reloads(hass) as reload_ids:
            await _scan_devices(
                hass,
                [
                    _discovered_device(
                        device_type="Marstek Energy Storage",
                        version=150,
                        firmware="150",
                    )
                ],
            )

        assert reload_ids == [entry.entry_id]
        assert entry.data["version"] == 150
        assert (
            issue_registry.async_get_issue(DOMAIN, f"openapi_reset_prone_{entry.entry_id}") is None
        )
