"""Round trip and standby draw measured by a linked energy meter."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.capacity_estimator import (
    CAPACITY_ATTR_KEY,
    CAPACITY_DATA_KEY,
    ES_STATUS_FRESH_KEY,
    SOC_CORRECTED_KEY,
    SOC_UNCONFIRMED_KEY,
)
from custom_components.marstek.const import CONF_PLUG_ENERGY_IN, CONF_PLUG_ENERGY_OUT
from custom_components.marstek.helpers.boot_pulse import entity_power_w
from custom_components.marstek.helpers.plug_round_trip import (
    HORIZON_SECONDS,
    PLUG_STORE_KEY,
    REFRESH_SECONDS,
    STANDBY_VERSION,
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
    power: float | None = 1_000.0,
    bypass: float = 0.0,
    capacity: float | None = _CAPACITY_WH,
    corrected: bool = False,
    plug_w: float | None = None,
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
        plug_w=plug_w,
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
    # A percentage the estimator holds for confirmation is not a level to anchor.
    held = {**status, "battery_soc": 90, SOC_UNCONFIRMED_KEY: True}
    apply_plug_round_trip(plug, hass, held, _T0 + 360)
    assert list(plug.to_dict()["anchors"]) == ["40"]
    assert status[CAPACITY_ATTR_KEY] == {"plug_energy_entities": list(_METER)}


_STEP_H = 5 / 60
_BASELINE_W = 9.0
# One 5-minute step every 30 minutes. Over an hour that is 9 Wh, so the
# counter average is 18 W while the plug still shows 9 W almost all the time.
_PULSE_WH = 4.5
# One short top-up each hour, small enough that the battery can report it as
# idle, large enough to add another 9 W to the counter average.
_CHARGE_WH = 9.0


def _idle_night(
    plug: PlugRoundTrip,
    *,
    pulses: bool = False,
    charges: bool = False,
    quantize: bool = False,
    plug_power: bool = True,
    skip_every: int = 0,
    unknown_power_every: int = 0,
    hours: float = 6.0,
) -> float:
    """Feed an idle night. Return the counter average in watts, which is the old standby."""
    steps = int(hours / _STEP_H)
    true = 0.0
    reported = 0.0
    for index in range(steps + 1):
        shown = _BASELINE_W
        if index:
            extra = 0.0
            if pulses and index % 6 == 0:
                extra += _PULSE_WH
                shown = 2_500.0
            if charges and index % 12 == 3:
                extra += _CHARGE_WH
                shown = 400.0
            true += _BASELINE_W * _STEP_H + extra
            reported = round(true) if quantize else true
        stamp_hours = index * _STEP_H
        if skip_every and index and index % skip_every == 0:
            plug.observe(
                timestamp=_T0 + stamp_hours * _HOUR,
                soc=50,
                energy_in_wh=None,
                energy_out_wh=None,
                capacity_wh=_CAPACITY_WH,
                power_w=0,
                bypass_w=0,
            )
            continue
        power: float | None = 0.0
        if unknown_power_every and index and index % unknown_power_every == 0:
            power = None
        plug.observe(
            timestamp=_T0 + stamp_hours * _HOUR,
            soc=50,
            energy_in_wh=reported,
            energy_out_wh=0,
            capacity_wh=_CAPACITY_WH,
            power_w=power,
            bypass_w=0,
            plug_w=shown if plug_power else None,
        )
    return reported / (steps * _STEP_H)


def test_wake_pulses_do_not_raise_the_usual_standby() -> None:
    """A 9 W night with a pulse every half hour used to publish the 18 W average."""
    plug = PlugRoundTrip(_METER)
    mean = _idle_night(plug, pulses=True, quantize=True)
    assert mean == pytest.approx(18)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_a_short_charge_inside_an_idle_stretch_stays_out_of_standby() -> None:
    """Top-ups the battery reports as idle used to be averaged in, about 18 W."""
    plug = PlugRoundTrip(_METER)
    mean = _idle_night(plug, charges=True, quantize=True)
    assert mean == pytest.approx(18)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_the_counter_alone_still_finds_the_baseline_under_the_pulses() -> None:
    """No power sensor: ten-minute blocks drop the pulses. The old average is 18 W."""
    plug = PlugRoundTrip(_METER)
    mean = _idle_night(plug, pulses=True, quantize=True, plug_power=False)
    assert mean == pytest.approx(18)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_one_watt_hour_steps_do_not_double_a_steady_nine_watts() -> None:
    """Rounding a steady 9 W counter to 1 Wh stays near 9 W, so it is not the bug."""
    plug = PlugRoundTrip(_METER)
    mean = _idle_night(plug, quantize=True, plug_power=False)
    assert mean == pytest.approx(9, abs=0.2)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_missing_plug_readings_do_not_change_the_usual_draw() -> None:
    plug = PlugRoundTrip(_METER)
    mean = _idle_night(plug, pulses=True, quantize=True, skip_every=5)
    assert mean == pytest.approx(18)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_a_poll_with_no_battery_power_does_not_end_the_idle_stretch() -> None:
    plug = PlugRoundTrip(_METER)
    mean = _idle_night(plug, pulses=True, quantize=True, unknown_power_every=10)
    assert mean == pytest.approx(18)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_a_stored_average_is_replaced_by_the_usual_draw() -> None:
    """An 18 W average saved before this rule is dropped, then the night publishes 9 W."""
    raw = {"meter": list(_METER), "anchors": {}, "standby": [18.0, _T0]}
    plug = PlugRoundTrip.from_dict(raw, _METER)
    assert "plug_standby_w" not in plug.attributes()
    assert plug.consume_dirty() is True
    assert plug.to_dict()["standby"] is None
    assert plug.to_dict()["standby_version"] == STANDBY_VERSION
    mean = _idle_night(plug, pulses=True, quantize=True)
    assert mean == pytest.approx(18)
    assert plug.attributes()["plug_standby_w"] == 9.0
    assert plug.to_dict()["standby_version"] == STANDBY_VERSION


def test_a_long_hole_does_not_fold_a_missed_charge_into_standby() -> None:
    """Bridging the hole would publish about 16.6 W. The stretch starts again instead."""
    plug = PlugRoundTrip(_METER)
    energy = 0.0
    for index in range(4 * 12 + 1):
        if index:
            energy += _BASELINE_W * _STEP_H
        _feed(plug, index * _STEP_H, 50, energy, 0, power=0, plug_w=9)
    assert plug.attributes()["plug_standby_w"] == 9.0
    _feed(plug, 7, 50, energy + 80, 0, power=0, plug_w=9)
    _feed(plug, 7.1, 52, energy + 80, 10, power=1_500)
    assert (energy + 80) / 7 == pytest.approx(16.57, abs=0.01)
    assert plug.attributes()["plug_standby_w"] == 9.0


def test_a_reported_charge_ends_the_stretch_without_raising_standby() -> None:
    plug = PlugRoundTrip(_METER)
    _idle_night(plug, hours=4)
    assert plug.attributes()["plug_standby_w"] == 9.0
    _feed(plug, 5, 70, 2_000, 0, power=-1_500, plug_w=1_500)
    assert plug.attributes()["plug_standby_w"] == 9.0


async def test_apply_reads_the_plug_power_sensor(hass: HomeAssistant) -> None:
    """HomeWizard-style kWh counters plus a power sensor: the card's number is 9 W."""
    entry = MockConfigEntry(domain="homewizard")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("homewizard", "plug")}
    )
    registry = er.async_get(hass)

    def sensor(key: str, device_class: str) -> str:
        return registry.async_get_or_create(
            "sensor",
            "homewizard",
            key,
            device_id=device.id,
            suggested_object_id=key,
            original_device_class=device_class,
        ).entity_id

    incoming = sensor("plug_energy_import", "energy")
    outgoing = sensor("plug_energy_export", "energy")
    power = sensor("plug_power", "power")
    plug = PlugRoundTrip((incoming, outgoing))
    status: dict[str, object] = {"battery_soc": 50, "battery_power": 0, CAPACITY_DATA_KEY: 5_120}
    true = 0.0
    steps = 6 * 12
    for index in range(steps + 1):
        shown = 9.0
        if index:
            extra = 4.5 if index % 6 == 0 else 0.0
            if extra:
                shown = 2_500.0
            true += 9.0 * _STEP_H + extra
        hass.states.async_set(incoming, f"{round(true) / 1000:.3f}", {"unit_of_measurement": "kWh"})
        hass.states.async_set(outgoing, "0.000", {"unit_of_measurement": "kWh"})
        hass.states.async_set(power, str(shown), {"unit_of_measurement": "W"})
        apply_plug_round_trip(plug, hass, status, _T0 + index * _STEP_H * _HOUR)
    assert true / (steps * _STEP_H) == pytest.approx(18)
    assert entity_power_w(hass, power) == shown
    attrs = status[CAPACITY_ATTR_KEY]
    assert isinstance(attrs, dict)
    assert attrs["plug_standby_w"] == 9.0
    assert attrs["plug_energy_entities"] == [incoming, outgoing]
