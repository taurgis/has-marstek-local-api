"""Battery low and charging sensors, and the Home Assistant battery conditions."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.condition import (
    async_from_config,
    async_validate_condition_config,
)
from homeassistant.helpers.entity_platform import async_get_platforms
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_BATTERY_LOW_FOLLOW_CUTOFF,
    CONF_BATTERY_LOW_SOC,
    DEFAULT_BATTERY_LOW_SOC,
)
from custom_components.marstek.helpers.battery_automation import (
    BATTERY_CHARGING_DEADBAND_W,
    BATTERY_LOW_HYSTERESIS,
    BATTERY_LOW_MARGIN,
    auto_low_threshold,
    battery_is_charging,
    battery_low_state,
    parse_dod_percent,
    parse_dod_state,
    resolve_battery_low_threshold,
)
from custom_components.marstek.pymarstek.const import (
    DOD_DEFAULT_VALUE,
    DOD_MAX_VALUE,
    DOD_MIN_VALUE,
)
from tests.conftest import create_mock_client, patch_marstek_integration


def test_auto_threshold_uses_discharge_floor_plus_margin() -> None:
    """Depth of discharge 88 stops at 12% and warns 5 points earlier."""
    assert auto_low_threshold(DOD_DEFAULT_VALUE) == 100 - DOD_DEFAULT_VALUE + BATTERY_LOW_MARGIN
    assert auto_low_threshold(DOD_MIN_VALUE) == 100 - DOD_MIN_VALUE + BATTERY_LOW_MARGIN
    assert auto_low_threshold(None) == DEFAULT_BATTERY_LOW_SOC


def test_resolve_threshold_follows_cutoff_only_when_the_device_has_one() -> None:
    """Unknown firmware keeps the 15% default even if a DOD value is passed."""
    options: dict[str, Any] = {}
    assert (
        resolve_battery_low_threshold(options, supports_dod=False, dod=80)
        == DEFAULT_BATTERY_LOW_SOC
    )
    assert resolve_battery_low_threshold(options, supports_dod=True, dod=80) == 25
    assert (
        resolve_battery_low_threshold(options, supports_dod=True, dod=None)
        == DEFAULT_BATTERY_LOW_SOC
    )


def test_resolve_threshold_manual_level_overrides_the_cutoff() -> None:
    """The options number is the trip level when follow-cutoff is off."""
    options = {CONF_BATTERY_LOW_FOLLOW_CUTOFF: False, CONF_BATTERY_LOW_SOC: 12.0}
    assert resolve_battery_low_threshold(options, supports_dod=True, dod=88) == 12
    broken = {CONF_BATTERY_LOW_FOLLOW_CUTOFF: False, CONF_BATTERY_LOW_SOC: "nope"}
    assert (
        resolve_battery_low_threshold(broken, supports_dod=False, dod=None)
        == DEFAULT_BATTERY_LOW_SOC
    )
    low = {CONF_BATTERY_LOW_FOLLOW_CUTOFF: False, CONF_BATTERY_LOW_SOC: 1}
    high = {CONF_BATTERY_LOW_FOLLOW_CUTOFF: False, CONF_BATTERY_LOW_SOC: 99}
    assert resolve_battery_low_threshold(low, supports_dod=False, dod=None) == 5
    assert resolve_battery_low_threshold(high, supports_dod=False, dod=None) == 80


def test_battery_low_hysteresis_holds_inside_the_band() -> None:
    """Once low, the sensor stays low until the state of charge clears the band."""
    threshold = 15
    clear = threshold + BATTERY_LOW_HYSTERESIS
    assert battery_low_state(None, threshold, threshold, BATTERY_LOW_HYSTERESIS) is True
    assert battery_low_state(None, threshold + 1, threshold, BATTERY_LOW_HYSTERESIS) is False
    assert battery_low_state(True, clear - 0.1, threshold, BATTERY_LOW_HYSTERESIS) is True
    assert battery_low_state(True, clear, threshold, BATTERY_LOW_HYSTERESIS) is False
    assert battery_low_state(False, threshold + 1, threshold, BATTERY_LOW_HYSTERESIS) is False


def test_charging_deadband_uses_the_charge_sign() -> None:
    """Negative power at or beyond the deadband is charging. Idle noise is not."""
    assert battery_is_charging(-BATTERY_CHARGING_DEADBAND_W) is True
    assert battery_is_charging(-BATTERY_CHARGING_DEADBAND_W + 0.1) is False
    assert battery_is_charging(0) is False
    assert battery_is_charging(400) is False
    assert battery_is_charging(None) is None
    assert battery_is_charging(True) is None
    assert battery_is_charging(float("nan")) is None


def test_dod_parser_rejects_values_outside_the_open_api_range() -> None:
    """DOD is an integer from 30 to 88. States that are not numbers are ignored."""
    assert parse_dod_percent(DOD_MIN_VALUE) == DOD_MIN_VALUE
    assert parse_dod_percent(DOD_MAX_VALUE) == DOD_MAX_VALUE
    assert parse_dod_percent(DOD_MIN_VALUE - 1) is None
    assert parse_dod_percent(88.5) is None
    assert parse_dod_percent(True) is None
    assert parse_dod_state("88") == 88
    assert parse_dod_state("unknown") is None
    assert parse_dod_state(None) is None


def _entity_id(hass: HomeAssistant, entry_id: str, suffix: str) -> str:
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in er.async_entries_for_config_entry(registry, entry_id)
        if (entry.unique_id or "").endswith(suffix)
    ]
    assert len(matches) == 1, suffix
    return matches[0]


def _push(entry: MockConfigEntry, **changes: Any) -> None:
    coordinator = entry.runtime_data.coordinator
    coordinator.async_set_updated_data({**coordinator.data, **changes})


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, status: dict[str, Any] | None = None
) -> None:
    if entry.entry_id not in hass.config_entries.async_entry_ids():
        entry.add_to_hass(hass)
    with patch_marstek_integration(client=create_mock_client(status=status)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_battery_sensors_are_enabled_and_keep_existing_ids(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """New sensors are on by default and do not replace the CT connection sensor."""
    await _setup(hass, mock_config_entry)
    registry = er.async_get(hass)
    low = registry.async_get(_entity_id(hass, mock_config_entry.entry_id, "_battery_low"))
    charging = registry.async_get(_entity_id(hass, mock_config_entry.entry_id, "_battery_charging"))
    ct = registry.async_get(_entity_id(hass, mock_config_entry.entry_id, "_ct_connection"))
    assert low is not None and low.disabled_by is None
    assert charging is not None and charging.disabled_by is None
    assert ct is not None and ct.unique_id.endswith("_ct_connection")
    assert low.original_device_class == "battery"
    assert charging.original_device_class == "battery_charging"


async def test_low_and_charging_states_hysteresis_and_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Trip, hold, clear, and go unavailable from coordinator readings alone."""
    await _setup(
        hass,
        mock_config_entry,
        status={"battery_soc": 40, "battery_power": 0, "device_mode": "auto"},
    )
    low_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_low")
    charging_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_charging")
    assert hass.states.get(low_id).state == STATE_OFF
    assert hass.states.get(charging_id).state == STATE_OFF

    _push(mock_config_entry, battery_soc=15, battery_power=-49)
    await hass.async_block_till_done()
    assert hass.states.get(low_id).state == STATE_ON
    assert hass.states.get(charging_id).state == STATE_OFF

    _push(mock_config_entry, battery_soc=19, battery_power=-50)
    await hass.async_block_till_done()
    assert hass.states.get(low_id).state == STATE_ON
    assert hass.states.get(charging_id).state == STATE_ON

    _push(mock_config_entry, battery_soc=None, battery_power=None)
    await hass.async_block_till_done()
    assert hass.states.get(low_id).state == STATE_UNAVAILABLE
    assert hass.states.get(charging_id).state == STATE_UNAVAILABLE

    # The low latch survives the gap, so 18% is still low (clear level is 20%).
    _push(mock_config_entry, battery_soc=18, battery_power=10)
    await hass.async_block_till_done()
    assert hass.states.get(low_id).state == STATE_ON
    assert hass.states.get(charging_id).state == STATE_OFF

    _push(mock_config_entry, battery_soc=20, battery_power=10)
    await hass.async_block_till_done()
    assert hass.states.get(low_id).state == STATE_OFF


async def test_manual_low_level_and_discharge_cutoff(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options set a fixed level. A SYS device uses 100 minus DOD, plus 5."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_BATTERY_LOW_FOLLOW_CUTOFF: False, CONF_BATTERY_LOW_SOC: 10},
    )
    await _setup(
        hass,
        mock_config_entry,
        status={"battery_soc": 12, "battery_power": 0, "device_mode": "auto"},
    )
    low_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_low")
    assert hass.states.get(low_id).state == STATE_OFF
    _push(mock_config_entry, battery_soc=10)
    await hass.async_block_till_done()
    assert hass.states.get(low_id).state == STATE_ON

    cutoff = MockConfigEntry(
        domain=mock_config_entry.domain,
        unique_id="aa:bb:cc:dd:ee:01",
        data={
            **mock_config_entry.data,
            "ble_mac": "AA:BB:CC:DD:EE:01",
            "mac": "AA:BB:CC:DD:EE:01",
            "port": 30001,
            "device_type": "VenusE 3.0",
            "version": 150,
        },
    )
    await _setup(
        hass,
        cutoff,
        status={"battery_soc": 50, "battery_power": 0, "device_mode": "auto"},
    )
    cutoff_low = _entity_id(hass, cutoff.entry_id, "_battery_low")
    assert hass.states.get(cutoff_low).state == STATE_OFF
    # 16% is below the 17% trip level from DOD 88, and above the 15% fallback.
    _push(cutoff, battery_soc=16)
    await hass.async_block_till_done()
    assert hass.states.get(cutoff_low).state == STATE_ON
    _push(cutoff, battery_soc=30)
    await hass.async_block_till_done()
    assert hass.states.get(cutoff_low).state == STATE_OFF
    dod = _entity_id(hass, cutoff.entry_id, "_depth_of_discharge")
    number = _platform_entity(hass, "number", dod)
    number._attr_native_value = 60.0
    number.async_write_ha_state()
    _push(cutoff, battery_soc=40)
    await hass.async_block_till_done()
    # DOD 60 stops discharge at 40%, so the trip level is 45%.
    assert hass.states.get(cutoff_low).state == STATE_ON


def _platform_entity(hass: HomeAssistant, domain: str, entity_id: str) -> Any:
    """Return the live entity object for one Marstek platform."""
    for platform in async_get_platforms(hass, "marstek"):
        if platform.domain == domain and entity_id in platform.entities:
            return platform.entities[entity_id]
    raise AssertionError(entity_id)


async def _condition(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    validated = await async_validate_condition_config(hass, config)
    checker = await async_from_config(hass, validated)
    result = checker(hass)
    assert result is not None
    return result


async def test_battery_conditions_accept_marstek_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """is_low, is_charging, and is_level follow the Marstek entities and reject others."""
    await _setup(
        hass,
        mock_config_entry,
        status={"battery_soc": 55, "battery_power": -250, "device_mode": "auto"},
    )
    low_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_low")
    charging_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_charging")
    level_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_soc")
    power_id = _entity_id(hass, mock_config_entry.entry_id, "_battery_power")

    assert hass.states.get(level_id).attributes["device_class"] == "battery"
    assert hass.states.get(level_id).attributes["unit_of_measurement"] == "%"
    assert (
        await _condition(hass, {"condition": "battery.is_low", "target": {"entity_id": low_id}})
        is False
    )
    assert (
        await _condition(
            hass, {"condition": "battery.is_charging", "target": {"entity_id": charging_id}}
        )
        is True
    )
    assert await _condition(hass, _level(level_id, "below", 60)) is True
    assert await _condition(hass, _level(level_id, "above", 60)) is False

    _push(mock_config_entry, battery_soc=10, battery_power=0)
    await hass.async_block_till_done()
    assert (
        await _condition(hass, {"condition": "battery.is_low", "target": {"entity_id": low_id}})
        is True
    )
    assert (
        await _condition(
            hass, {"condition": "battery.is_charging", "target": {"entity_id": charging_id}}
        )
        is False
    )
    # A charging sensor that happens to be on is not a low-battery sensor.
    assert (
        await _condition(
            hass, {"condition": "battery.is_low", "target": {"entity_id": charging_id}}
        )
        is False
    )
    # Battery power is watts, so it is not a battery-level target.
    assert await _condition(hass, _level(power_id, "below", 1000)) is False
    assert await _condition(hass, _level(level_id, "below", 20)) is True


def _level(entity_id: str, kind: str, number: float) -> dict[str, Any]:
    return {
        "condition": "battery.is_level",
        "target": {"entity_id": entity_id},
        "options": {
            "threshold": {
                "type": kind,
                "value": {"number": number, "unit_of_measurement": "%"},
            }
        },
    }
