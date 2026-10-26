"""Round trip and standby draw measured by a linked energy meter."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.marstek.capacity_estimator import (
    CAPACITY_ATTR_KEY,
    CAPACITY_DATA_KEY,
    ES_STATUS_FRESH_KEY,
    SOC_CORRECTED_KEY,
)
from custom_components.marstek.const import CONF_PLUG_ENERGY_IN, CONF_PLUG_ENERGY_OUT
from custom_components.marstek.helpers.plug_round_trip import (
    HORIZON_SECONDS,
    PLUG_STORE_KEY,
    REFRESH_SECONDS,
    PlugRoundTrip,
    apply_plug_round_trip,
    energy_wh,
    plug_from_options,
)

_METER = ("sensor.plug_import", "sensor.plug_export")
_T0 = 1_700_000_000.0
_HOUR = 3_600.0
_CAPACITY_WH = 5_000.0


def _feed(
    plug: PlugRoundTrip,
    hours: float,
    soc: float,
    energy_in: float,
    energy_out: float,
    *,
    power: float = 1_000.0,
    bypass: float = 0.0,
    capacity: float | None = _CAPACITY_WH,
    corrected: bool = False,
) -> None:
    plug.observe(
        timestamp=_T0 + hours * _HOUR,
        soc=soc,
        energy_in_wh=energy_in,
        energy_out_wh=energy_out,
        capacity_wh=capacity,
        power_w=power,
        bypass_w=bypass,
        soc_corrected=corrected,
    )


def _cycle(plug: PlugRoundTrip, *, start: float = 0.0, energy_out: float = 5_100.0) -> None:
    """Anchor 50% and 80%, then return to 50% after 6 kWh in."""
    _feed(plug, start, 50, 0, 0)
    _feed(plug, start + 1, 80, 6_000, 4_000)
    _feed(plug, start + 2, 50, 6_000, energy_out)


def test_equal_state_of_charge_gives_out_over_in() -> None:
    plug = PlugRoundTrip(_METER)
    _cycle(plug)
    attrs = plug.attributes()
    assert attrs["plug_round_trip_efficiency"] == 0.85
    assert attrs["plug_round_trip_energy_in_wh"] == 6_000
    assert attrs["plug_round_trip_energy_out_wh"] == 5_100
    assert attrs["plug_round_trip_since"].startswith("2023-11-14T22:13:20")
    assert plug.consume_dirty() is True
    assert plug.consume_dirty() is False


@pytest.mark.parametrize("capacity", [None, 0.0, 6_500.0])
def test_less_than_one_capacity_in_publishes_nothing(capacity: float | None) -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 50, 0, 0, capacity=capacity)
    _feed(plug, 2, 50, 6_000, 5_100, capacity=capacity)
    assert plug.attributes() == {}
    assert plug.consume_dirty() is False


@pytest.mark.parametrize("energy_out", [2_000.0, 7_000.0])
def test_implausible_quotients_are_dropped(energy_out: float) -> None:
    plug = PlugRoundTrip(_METER)
    _cycle(plug, energy_out=energy_out)
    assert "plug_round_trip_efficiency" not in plug.attributes()


def test_the_measurement_refreshes_at_most_daily() -> None:
    plug = PlugRoundTrip(_METER)
    _cycle(plug)
    _feed(plug, 3, 50, 6_000, 5_200)
    assert plug.attributes()["plug_round_trip_energy_out_wh"] == 5_100
    _feed(plug, 2 + REFRESH_SECONDS / _HOUR, 50, 6_000, 5_400)
    assert plug.attributes()["plug_round_trip_efficiency"] == 0.9


def test_an_anchor_older_than_the_horizon_is_replaced() -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 50, 0, 0)
    _feed(plug, 1 + HORIZON_SECONDS / _HOUR, 50, 6_000, 5_100)
    assert plug.attributes() == {}
    assert plug.to_dict()["anchors"]["50"]["energy_in_wh"] == 6_000


def test_bypass_power_restarts_the_measurement() -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 50, 0, 0)
    _feed(plug, 1, 80, 6_000, 4_000, bypass=500.0)
    assert plug.to_dict()["anchors"] == {}
    _feed(plug, 2, 50, 6_000, 5_100)
    assert plug.attributes() == {}


def test_a_counter_that_went_down_restarts_the_measurement() -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 50, 1_000, 1_000)
    _feed(plug, 1, 50, 10, 1_000)
    assert plug.to_dict()["anchors"] == {
        "50": {"at": _T0 + _HOUR, "energy_in_wh": 10, "energy_out_wh": 1_000}
    }


def test_a_bms_correction_drops_the_level_readings_taken_before_it() -> None:
    """50% before the BMS moved its reading by 8 points is not 50% after it."""
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 50, 0, 0)
    _feed(plug, 1, 100, 6_000, 0, corrected=True)
    _feed(plug, 2, 50, 6_000, 3_000)
    assert plug.attributes() == {}
    assert list(plug.to_dict()["anchors"]) == ["100", "50"]


def test_a_correction_keeps_the_standby_stretch() -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 90, 100, 0, power=0.0)
    _feed(plug, 2, 100, 110, 0, power=0.0, corrected=True)
    _feed(plug, 4, 100, 120, 0, power=0.0)
    assert plug.attributes()["plug_standby_w"] == 5.0


def test_missing_values_are_skipped() -> None:
    plug = PlugRoundTrip(_METER)
    plug.observe(
        timestamp=_T0,
        soc=None,
        energy_in_wh=0,
        energy_out_wh=0,
        capacity_wh=_CAPACITY_WH,
        power_w=0,
        bypass_w=0,
    )
    assert plug.to_dict()["anchors"] == {}


def test_an_idle_stretch_gives_the_standby_draw_and_again_when_it_ends() -> None:
    plug = PlugRoundTrip(_METER)
    for hour in range(3):
        _feed(plug, hour, 60, 1_000 + 8 * hour, 500, power=0)
    assert "plug_standby_w" not in plug.attributes()
    _feed(plug, 3, 60, 1_024, 500, power=-2)
    assert plug.attributes()["plug_standby_w"] == 8.0
    assert plug.consume_dirty() is True
    _feed(plug, 4, 60, 1_034, 500, power=0)
    assert plug.consume_dirty() is False
    _feed(plug, 5, 62, 2_000, 500, power=-1_500)
    assert plug.attributes()["plug_standby_w"] == 8.5
    assert plug.consume_dirty() is True


def test_a_long_idle_stretch_publishes_daily_while_it_lasts() -> None:
    plug = PlugRoundTrip(_METER)
    for hour in range(0, 30):
        _feed(plug, hour, 60, 1_000 + 6 * hour, 500, power=0)
    assert plug.attributes()["plug_standby_w"] == 6.0
    assert plug.attributes()["plug_standby_at"].startswith("2023-11-16T01:13:20")


def test_energy_out_during_an_idle_poll_ends_the_stretch() -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 60, 1_000, 500, power=0)
    _feed(plug, 2, 60, 1_016, 500, power=0)
    _feed(plug, 4, 60, 1_032, 600, power=0)
    _feed(plug, 6.5, 60, 1_052, 600, power=1_000)
    assert "plug_standby_w" not in plug.attributes()


def test_stored_state_round_trips_for_the_same_meter() -> None:
    plug = PlugRoundTrip(_METER)
    _cycle(plug)
    for hour in range(3, 8):
        _feed(plug, hour, 50, 6_000 + 8 * (hour - 3), 5_100, power=0)
    _feed(plug, 8, 51, 7_000, 5_100, power=-800)
    restored = PlugRoundTrip.from_dict(plug.to_dict(), _METER)
    assert restored.attributes() == plug.attributes()
    assert restored.to_dict() == plug.to_dict()
    assert PlugRoundTrip.from_dict(plug.to_dict(), ("sensor.a", "sensor.b")).attributes() == {}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"meter": list(_METER), "anchors": "x", "measured": "x", "standby": "x"},
        {
            "meter": list(_METER),
            "anchors": {"x": {}, "101": {"at": 1, "energy_in_wh": 1, "energy_out_wh": 1}},
            "measured": {
                "efficiency": 1.5,
                "since": 1,
                "at": 2,
                "energy_in_wh": 1,
                "energy_out_wh": 1,
            },
            "standby": [500.0, 1.0],
        },
        {"meter": list(_METER), "anchors": {"5": {"at": True}}, "standby": [float("nan"), 1]},
    ],
)
def test_unreadable_stored_state_is_ignored(raw: object) -> None:
    plug = PlugRoundTrip.from_dict(raw, _METER)
    assert plug.attributes() == {}
    assert plug.to_dict()["anchors"] == {}


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [
        ("1.5", "kWh", 1_500.0),
        ("250", "Wh", 250.0),
        ("0.002", "MWh", 2_000.0),
        ("unavailable", "kWh", None),
        ("-1", "kWh", None),
        ("5", "W", None),
        ("5", None, None),
    ],
)
async def test_energy_wh_converts_or_rejects(
    hass: HomeAssistant, state: str, unit: str | None, expected: float | None
) -> None:
    attributes = {} if unit is None else {"unit_of_measurement": unit}
    hass.states.async_set("sensor.plug_import", state, attributes)
    assert energy_wh(hass, "sensor.plug_import") == expected
    assert energy_wh(hass, "sensor.missing") is None
    assert energy_wh(hass, None) is None


@pytest.mark.parametrize(
    "options",
    [
        {},
        {CONF_PLUG_ENERGY_IN: "sensor.plug_import"},
        {CONF_PLUG_ENERGY_IN: "", CONF_PLUG_ENERGY_OUT: "sensor.plug_export"},
        {CONF_PLUG_ENERGY_IN: None, CONF_PLUG_ENERGY_OUT: "sensor.plug_export"},
    ],
)
def test_no_tracker_without_both_meter_entities(options: dict[str, object]) -> None:
    assert plug_from_options(options, None) is None


def test_the_tracker_restores_from_the_capacity_store() -> None:
    stored_plug = PlugRoundTrip(_METER)
    _cycle(stored_plug)
    options = {CONF_PLUG_ENERGY_IN: _METER[0], CONF_PLUG_ENERGY_OUT: _METER[1]}
    plug = plug_from_options(options, {"windows": [], PLUG_STORE_KEY: stored_plug.to_dict()})
    assert plug is not None
    assert plug.meter == _METER
    assert plug.attributes() == stored_plug.attributes()


async def test_apply_reads_the_meter_and_adds_attributes(hass: HomeAssistant) -> None:
    plug = PlugRoundTrip(_METER)
    _feed(plug, 0, 50, 0, 0)
    _feed(plug, 1, 80, 6_000, 4_000)
    hass.states.async_set(_METER[0], "6.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(_METER[1], "5.1", {"unit_of_measurement": "kWh"})
    status: dict[str, object] = {
        "battery_soc": 50,
        "battery_power": 900,
        "pv_power": None,
        "offgrid_power": 10,
        CAPACITY_DATA_KEY: None,
        "bat_cap": 5_120,
        CAPACITY_ATTR_KEY: {"usable_wh": None},
    }
    apply_plug_round_trip(plug, hass, status, _T0 + 2 * _HOUR)
    assert status[CAPACITY_ATTR_KEY] == {
        "usable_wh": None,
        "plug_energy_entities": list(_METER),
        **plug.attributes(),
    }
    assert plug.attributes()["plug_round_trip_efficiency"] == 0.85


async def test_apply_restarts_on_solar_and_skips_stale_polls(hass: HomeAssistant) -> None:
    plug = PlugRoundTrip(_METER)
    hass.states.async_set(_METER[0], "1.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(_METER[1], "0.5", {"unit_of_measurement": "kWh"})
    status: dict[str, object] = {"battery_soc": 40, "battery_power": 0, CAPACITY_DATA_KEY: 4_600}
    apply_plug_round_trip(plug, hass, status, _T0)
    assert list(plug.to_dict()["anchors"]) == ["40"]
    apply_plug_round_trip(plug, hass, {**status, "pv_power": -300}, _T0 + 60)
    assert plug.to_dict()["anchors"] == {}
    apply_plug_round_trip(plug, hass, {**status, ES_STATUS_FRESH_KEY: False}, _T0 + 120)
    assert plug.to_dict()["anchors"] == {}
    apply_plug_round_trip(plug, hass, status, _T0 + 180)
    apply_plug_round_trip(plug, hass, {**status, "battery_soc": 48}, _T0 + 240)
    apply_plug_round_trip(plug, hass, {**status, SOC_CORRECTED_KEY: True}, _T0 + 300)
    assert list(plug.to_dict()["anchors"]) == ["40"]
    assert status[CAPACITY_ATTR_KEY] == {"plug_energy_entities": list(_METER)}
