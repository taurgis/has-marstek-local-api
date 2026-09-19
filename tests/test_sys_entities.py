"""Tests for firmware-gated SYS number and switch entities."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    PERCENTAGE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.marstek.const import DOMAIN, PLATFORMS
from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.helpers.sys_write import sys_write_target
from custom_components.marstek.number import async_setup_entry as async_setup_number
from custom_components.marstek.pymarstek.const import (
    BLE_ADV_DISABLED,
    BLE_ADV_ENABLED,
    CMD_BLE_ADV,
    CMD_DOD_SET,
    CMD_LED_CTRL,
    DOD_DEFAULT_VALUE,
    DOD_MAX_VALUE,
    DOD_MIN_VALUE,
    LED_OFF,
    LED_ON,
)
from custom_components.marstek.pymarstek.validators import VALID_METHODS, ValidationError
from custom_components.marstek.switch import async_setup_entry as async_setup_switch
from tests.conftest import create_mock_client, patch_marstek_integration

SYS_NUMBER_KEY = "depth_of_discharge"
SYS_SWITCH_KEYS = ("bluetooth_advertising", "panel_led")
SYS_ENTITY_KEYS = (SYS_NUMBER_KEY, *SYS_SWITCH_KEYS)
BLE_MAC = "AA:BB:CC:DD:EE:FF"


def _unique_id(key: str, ble_mac: str = BLE_MAC) -> str:
    return f"{format_mac(ble_mac)}_{key}"


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, _unique_id(key))


def _sys_client() -> MagicMock:
    client = create_mock_client(
        status={
            "battery_soc": 55,
            "device_mode": "auto",
            "battery_power": -250,
            "battery_status": "discharging",
        }
    )
    client.send_request = AsyncMock(return_value={"result": {"set_result": True}})
    return client


def _config_entry(
    *,
    device_type: str = "VenusE 3.0",
    version: Any = 150,
    ble_mac: str = BLE_MAC,
) -> MockConfigEntry:
    data: dict[str, Any] = {
        "host": "1.2.3.4",
        "ble_mac": ble_mac,
        "mac": ble_mac,
        "device_type": device_type,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }
    if version is not None:
        data["version"] = version
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=format_mac(ble_mac),
        data=data,
    )


def _platform_for_key(key: str) -> str:
    return "number" if key == SYS_NUMBER_KEY else "switch"


async def _setup_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: MagicMock | None = None,
) -> MagicMock:
    entry.add_to_hass(hass)
    client = client or _sys_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return client


def _disable_coordinator_refresh(
    entry: MockConfigEntry, client: MagicMock
) -> AsyncMock:
    """Replace coordinator refresh so SYS writes can prove they skip it."""
    client.get_device_status.reset_mock()
    refresh = AsyncMock()
    entry.runtime_data.coordinator.async_request_refresh = refresh
    return refresh


def _assert_no_status_or_refresh(client: MagicMock, refresh: AsyncMock) -> None:
    """SYS writes must not poll status or request a coordinator refresh."""
    client.get_device_status.assert_not_called()
    refresh.assert_not_called()


def _command_after_setup(client: MagicMock, setup_calls: int) -> dict[str, Any]:
    sent = [
        json.loads(call.args[0])
        for call in client.send_request.call_args_list[setup_calls:]
        if call.args
    ]
    assert sent, "expected a SYS write after setup"
    return sent[-1]


# ---------------------------------------------------------------------------
# Firmware profile matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("device_type", "version", "expect_sys"),
    [
        ("VenusA", 150, True),
        ("VenusC", 150, True),
        ("VenusD", 200, True),
        ("VenusE 3.0", 150, True),
        ("VenusA", 149, False),
        ("VenusC", 145, False),
        ("VenusE 3.0", 0, False),
        ("VenusA", None, False),
        ("VenusE 3.0", "not-a-version", False),
        ("Venus E mini", 1, True),
        ("Venus E mini", 0, True),
        ("VenusE-mini 3.0", 12, True),
        ("Venus E mini", None, False),
        ("Venus E mini", "unknown", False),
        ("Marstek Energy Storage", 150, False),
        ("Venus", 150, False),
    ],
)
async def test_sys_entities_follow_firmware_profile(
    hass: HomeAssistant,
    device_type: str,
    version: Any,
    expect_sys: bool,
) -> None:
    """Create SYS entities only when the firmware profile authorizes them."""
    profile = resolve_firmware_profile(device_type, version)
    assert profile.supports_sys_dod is expect_sys
    assert profile.supports_sys_ble_advertising is expect_sys
    assert profile.supports_sys_led is expect_sys

    entry = _config_entry(device_type=device_type, version=version)
    await _setup_entry(hass, entry)
    assert entry.state == ConfigEntryState.LOADED

    registry = er.async_get(hass)
    for key in SYS_ENTITY_KEYS:
        platform = _platform_for_key(key)
        entity_id = registry.async_get_entity_id(platform, DOMAIN, _unique_id(key))
        if expect_sys:
            assert entity_id is not None
            assert hass.states.get(entity_id) is not None
        else:
            assert entity_id is None
            for existing in hass.states.async_entity_ids():
                assert key not in existing


async def test_unsupported_sys_controls_leave_no_registry_entries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Legacy/unknown profiles must not register SYS entities at all."""
    await _setup_entry(hass, mock_config_entry)
    registry = er.async_get(hass)
    for key in SYS_ENTITY_KEYS:
        assert (
            registry.async_get_entity_id(_platform_for_key(key), DOMAIN, _unique_id(key))
            is None
        )
        for existing in hass.states.async_entity_ids():
            assert key not in existing


@pytest.mark.parametrize("version", [150, 200])
async def test_sys_unique_ids_stay_ble_mac_based_across_firmware(
    hass: HomeAssistant, version: int
) -> None:
    """SYS unique IDs stay on the BLE MAC and do not include firmware."""
    entry = _config_entry(device_type="VenusA", version=version)
    await _setup_entry(hass, entry)
    registry = er.async_get(hass)
    for key in SYS_ENTITY_KEYS:
        entity_id = registry.async_get_entity_id(
            _platform_for_key(key), DOMAIN, _unique_id(key)
        )
        assert entity_id is not None
        registry_entry = registry.async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.unique_id == _unique_id(key)
        assert str(version) not in registry_entry.unique_id


async def test_destructive_sys_methods_are_not_exposed(hass: HomeAssistant) -> None:
    """Set.Ver and Reset.Factory stay out of validators, platforms, and entities."""
    assert "Set.Ver" not in VALID_METHODS
    assert "Reset.Factory" not in VALID_METHODS
    assert "number" in {platform.value for platform in PLATFORMS}
    assert "switch" in {platform.value for platform in PLATFORMS}

    entry = _config_entry()
    await _setup_entry(hass, entry)
    for entity_id in hass.states.async_entity_ids():
        assert "set_ver" not in entity_id
        assert "reset_factory" not in entity_id
        assert "factory" not in entity_id


# ---------------------------------------------------------------------------
# DOD number
# ---------------------------------------------------------------------------


async def test_dod_entity_defaults_and_attributes(hass: HomeAssistant) -> None:
    """DOD is a config number from 30-88% with step 1 and default 88."""
    entry = _config_entry()
    await _setup_entry(hass, entry)
    entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == DOD_DEFAULT_VALUE
    assert state.attributes["min"] == DOD_MIN_VALUE
    assert state.attributes["max"] == DOD_MAX_VALUE
    assert state.attributes["step"] == 1
    assert state.attributes["unit_of_measurement"] == PERCENTAGE
    assert state.attributes.get(ATTR_ASSUMED_STATE) is True
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.entity_category == EntityCategory.CONFIG


@pytest.mark.parametrize("value", [DOD_MIN_VALUE, DOD_MAX_VALUE, 50])
async def test_dod_write_sends_integer_value(
    hass: HomeAssistant, value: int
) -> None:
    """Acknowledged DOD writes send DOD.SET with an integer value."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        setup_calls = client.send_request.call_count
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None
        refresh = _disable_coordinator_refresh(entry, client)

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": value},
            blocking=True,
        )

        payload = _command_after_setup(client, setup_calls)
        assert payload["method"] == CMD_DOD_SET
        assert payload["params"] == {"value": value}
        assert isinstance(payload["params"]["value"], int)
        assert float(hass.states.get(entity_id).state) == value
        _assert_no_status_or_refresh(client, refresh)


@pytest.mark.parametrize("value", [29, 89])
async def test_dod_rejects_out_of_range_values(
    hass: HomeAssistant, value: int
) -> None:
    """Home Assistant rejects DOD values outside 30-88 before a write."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None
        setup_calls = client.send_request.call_count

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )

        assert client.send_request.call_count == setup_calls
        assert float(hass.states.get(entity_id).state) == DOD_DEFAULT_VALUE


async def test_dod_restores_in_range_native_value(hass: HomeAssistant) -> None:
    """RestoreNumber keeps an in-range native DOD value across reload."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        mock_restore_cache_with_extra_data(
            hass,
            [
                (
                    State(entity_id, "50"),
                    {
                        "native_max_value": DOD_MAX_VALUE,
                        "native_min_value": DOD_MIN_VALUE,
                        "native_step": 1,
                        "native_unit_of_measurement": PERCENTAGE,
                        "native_value": 50,
                    },
                )
            ],
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert float(hass.states.get(entity_id).state) == 50


@pytest.mark.parametrize(
    "native_value",
    [None, 20, 100, "88", 50.5],
)
async def test_dod_invalid_restore_falls_back_to_default(
    hass: HomeAssistant, native_value: Any
) -> None:
    """Missing, malformed, or out-of-range restored DOD data falls back to 88."""
    extra: dict[str, Any] = {
        "native_max_value": DOD_MAX_VALUE,
        "native_min_value": DOD_MIN_VALUE,
        "native_step": 1,
        "native_unit_of_measurement": PERCENTAGE,
        "native_value": native_value,
    }
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        mock_restore_cache_with_extra_data(hass, [(State(entity_id, "88"), extra)])
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert float(hass.states.get(entity_id).state) == DOD_DEFAULT_VALUE


# ---------------------------------------------------------------------------
# Switches
# ---------------------------------------------------------------------------


async def test_sys_switches_start_unknown_without_history(
    hass: HomeAssistant,
) -> None:
    """Switches stay unknown until a successful write when nothing was restored."""
    entry = _config_entry()
    await _setup_entry(hass, entry)
    for key in SYS_SWITCH_KEYS:
        entity_id = _entity_id(hass, "switch", key)
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN
        assert state.attributes.get(ATTR_ASSUMED_STATE) is True
        registry_entry = er.async_get(hass).async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.entity_category == EntityCategory.CONFIG


@pytest.mark.parametrize(
    ("service", "enable"),
    [("turn_on", BLE_ADV_ENABLED), ("turn_off", BLE_ADV_DISABLED)],
)
async def test_bluetooth_advertising_wire_polarity(
    hass: HomeAssistant, service: str, enable: int
) -> None:
    """HA on maps to Ble.Adv enable 0; HA off maps to enable 1."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        setup_calls = client.send_request.call_count
        entity_id = _entity_id(hass, "switch", "bluetooth_advertising")
        assert entity_id is not None
        refresh = _disable_coordinator_refresh(entry, client)

        await hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=True
        )

        payload = _command_after_setup(client, setup_calls)
        assert payload["method"] == CMD_BLE_ADV
        assert payload["params"] == {"enable": enable}
        expected_state = STATE_ON if service == "turn_on" else STATE_OFF
        assert hass.states.get(entity_id).state == expected_state
        _assert_no_status_or_refresh(client, refresh)


@pytest.mark.parametrize(
    ("service", "wire_state"),
    [("turn_on", LED_ON), ("turn_off", LED_OFF)],
)
async def test_panel_led_wire_polarity(
    hass: HomeAssistant, service: str, wire_state: int
) -> None:
    """Panel LED on sends state 1 and off sends state 0."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        setup_calls = client.send_request.call_count
        entity_id = _entity_id(hass, "switch", "panel_led")
        assert entity_id is not None
        refresh = _disable_coordinator_refresh(entry, client)

        await hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=True
        )

        payload = _command_after_setup(client, setup_calls)
        assert payload["method"] == CMD_LED_CTRL
        assert payload["params"] == {"state": wire_state}
        expected_state = STATE_ON if service == "turn_on" else STATE_OFF
        assert hass.states.get(entity_id).state == expected_state
        _assert_no_status_or_refresh(client, refresh)


@pytest.mark.parametrize("restored", [STATE_ON, STATE_OFF])
async def test_sys_switches_restore_on_and_off(
    hass: HomeAssistant, restored: str
) -> None:
    """Valid prior on/off switch states are restored."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_ids = [
            _entity_id(hass, "switch", key) for key in SYS_SWITCH_KEYS
        ]
        assert all(entity_ids)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        mock_restore_cache(
            hass, [State(entity_id, restored) for entity_id in entity_ids]
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for entity_id in entity_ids:
        assert hass.states.get(entity_id).state == restored


async def test_sys_switches_ignore_invalid_restored_state(
    hass: HomeAssistant,
) -> None:
    """Unavailable or unknown history does not invent a physical switch state."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        led_id = _entity_id(hass, "switch", "panel_led")
        ble_id = _entity_id(hass, "switch", "bluetooth_advertising")
        assert led_id is not None
        assert ble_id is not None
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        mock_restore_cache(
            hass,
            [
                State(led_id, STATE_UNAVAILABLE),
                State(ble_id, "not-a-switch"),
            ],
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(led_id).state == STATE_UNKNOWN
    assert hass.states.get(ble_id).state == STATE_UNKNOWN


# ---------------------------------------------------------------------------
# Write acknowledgement, errors, pause/resume
# ---------------------------------------------------------------------------


async def test_sys_write_changes_state_only_after_ack(hass: HomeAssistant) -> None:
    """Optimistic state updates only after result.set_result is true."""
    entry = _config_entry()
    client = _sys_client()
    client.send_request = AsyncMock(return_value={"result": {"set_result": False}})
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": 40},
                blocking=True,
            )

        assert err.value.translation_key == "sys_write_rejected"
        assert float(hass.states.get(entity_id).state) == DOD_DEFAULT_VALUE


@pytest.mark.parametrize(
    ("response", "translation_key"),
    [
        ({"error": {"code": -32601, "message": "Method not found"}}, "sys_write_rpc_error"),
        ({"result": {}}, "sys_write_rejected"),
        ({"result": {"set_result": False}}, "sys_write_rejected"),
    ],
)
async def test_sys_write_device_failures_keep_prior_state(
    hass: HomeAssistant,
    response: dict[str, Any],
    translation_key: str,
) -> None:
    """JSON-RPC errors and missing/false set_result keep the previous value."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "switch", "panel_led")
        assert entity_id is not None
        client.send_request.return_value = {"result": {"set_result": True}}
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )
        assert hass.states.get(entity_id).state == STATE_ON

        client.send_request.return_value = response
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": entity_id}, blocking=True
            )
        assert err.value.translation_key == translation_key
        assert hass.states.get(entity_id).state == STATE_ON


async def test_sys_write_timeout_keeps_prior_state_and_resumes(
    hass: HomeAssistant,
) -> None:
    """Timeouts raise a translated error, keep state, and always resume polling."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None

        order: list[str] = []

        async def pause(_host: str) -> None:
            order.append("pause")

        async def send(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            order.append("send")
            raise TimeoutError("timeout")

        async def resume(_host: str) -> None:
            order.append("resume")

        client.pause_polling.side_effect = pause
        client.send_request.side_effect = send
        client.resume_polling.side_effect = resume

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": 40},
                blocking=True,
            )

        assert err.value.translation_key == "sys_write_timeout"
        assert float(hass.states.get(entity_id).state) == DOD_DEFAULT_VALUE
        assert order == ["pause", "send", "resume"]


async def test_sys_write_transport_error_keeps_prior_state(
    hass: HomeAssistant,
) -> None:
    """Transport errors raise a translated error and resume polling."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "switch", "panel_led")
        assert entity_id is not None
        client.send_request.side_effect = OSError("network down")

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": entity_id}, blocking=True
            )

        assert err.value.translation_key == "sys_write_failed"
        assert hass.states.get(entity_id).state == STATE_UNKNOWN
        assert client.resume_polling.call_count >= 1


async def test_sys_write_validation_error_during_send_keeps_state(
    hass: HomeAssistant,
) -> None:
    """Validation failures during transmission keep prior state and resume."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None
        client.send_request.side_effect = ValidationError("value must be an integer", "value")

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": 40},
                blocking=True,
            )

        assert err.value.translation_key == "sys_write_invalid"
        assert float(hass.states.get(entity_id).state) == DOD_DEFAULT_VALUE
        assert client.resume_polling.call_count >= 1


async def test_sys_write_non_dict_response_is_rejected(
    hass: HomeAssistant,
) -> None:
    """A non-dict UDP payload is treated as an unacknowledged write."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "switch", "bluetooth_advertising")
        assert entity_id is not None
        client.send_request.return_value = "not-json-rpc"

        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": entity_id}, blocking=True
            )

        assert err.value.translation_key == "sys_write_rejected"
        assert hass.states.get(entity_id).state == STATE_UNKNOWN


def test_sys_write_target_requires_host() -> None:
    """SYS writes fail closed when the config entry has no host."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    with pytest.raises(HomeAssistantError) as err:
        sys_write_target(entry)
    assert err.value.translation_key == "no_host_configured"


async def test_sys_write_pauses_before_send_and_resumes(
    hass: HomeAssistant,
) -> None:
    """Successful writes pause polling before UDP and resume afterwards."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "switch", "bluetooth_advertising")
        assert entity_id is not None

        order: list[str] = []

        async def pause(_host: str) -> None:
            order.append("pause")

        async def send(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            order.append("send")
            return {"result": {"set_result": True}}

        async def resume(_host: str) -> None:
            order.append("resume")

        client.pause_polling.side_effect = pause
        client.send_request.side_effect = send
        client.resume_polling.side_effect = resume

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )

        assert order == ["pause", "send", "resume"]
        payload = json.loads(
            [
                call.args[0]
                for call in client.send_request.call_args_list
                if call.args
            ][-1]
        )
        assert payload["method"] == CMD_BLE_ADV
        assert payload["params"]["enable"] == BLE_ADV_ENABLED


async def test_sys_write_validation_failure_keeps_state(
    hass: HomeAssistant,
) -> None:
    """Validation failures raise a translated error and do not change state."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _entity_id(hass, "number", SYS_NUMBER_KEY)
        assert entity_id is not None
        setup_calls = client.send_request.call_count

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": 29},
                blocking=True,
            )

        assert client.send_request.call_count == setup_calls
        assert float(hass.states.get(entity_id).state) == DOD_DEFAULT_VALUE


async def test_sys_entity_properties_do_not_perform_io(
    hass: HomeAssistant,
) -> None:
    """Reading SYS entity state does not send UDP."""
    entry = _config_entry()
    client = _sys_client()
    with patch_marstek_integration(client=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        send_count = client.send_request.call_count
        status_count = client.get_device_status.call_count
        for key in SYS_ENTITY_KEYS:
            entity_id = _entity_id(hass, _platform_for_key(key), key)
            assert entity_id is not None
            assert hass.states.get(entity_id) is not None
        assert client.send_request.call_count == send_count
        assert client.get_device_status.call_count == status_count


async def test_number_and_switch_setup_missing_udp_client(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """SYS platforms skip setup when the UDP client is missing."""
    hass.data.pop(DOMAIN, None)
    entry = _config_entry()
    entry.add_to_hass(hass)
    coordinator = MagicMock(profile=resolve_firmware_profile("VenusE 3.0", 150))
    coordinator.udp_client = None
    entry.runtime_data = SimpleNamespace(
        coordinator=coordinator,
        device_info={**entry.data, "ip": entry.data["host"]},
    )
    caplog.set_level(logging.ERROR)
    async_add_entities = MagicMock()

    await async_setup_number(hass, entry, async_add_entities)
    await async_setup_switch(hass, entry, async_add_entities)

    assert "UDP client not found" in caplog.text
    async_add_entities.assert_not_called()
