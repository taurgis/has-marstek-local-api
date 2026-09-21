"""Tests for the physical realism of the mock device.

These cover the behaviour a Home Assistant install actually sees: what the
inverter port reports, how Auto mode tracks a P1 meter, and whether the pack
obeys efficiency, ramp and temperature limits rather than jumping between
arbitrary numbers.
"""

from __future__ import annotations

import datetime as datetime_module

import pytest
from mock_device import BatterySimulator, SolarSimulator
from mock_device.const import (
    _FAMILY_SPECS,
    AMBIENT_MEAN_C,
    BAT_TEMP_MIN_CHARGE_C,
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    MODE_PASSIVE,
    MODE_UPS,
    POWER_RAMP_W_PER_SECOND,
    device_spec,
)

from custom_components.marstek.const import get_device_power_limits

# How close to zero a settled unit holds the meter. The deadband, the CT
# noise and the inverter's own setpoint tracking error all add up here.
_SETTLED_METER_BAND_W = 120


def _quiet_house(sim: BatterySimulator, load: int = 0, pv: int = 0) -> None:
    """Pin both sides of the home so a test controls the meter exactly."""
    sim.set_house_load(load)
    sim.set_house_pv(pv)


class TestOnGridSemantics:
    """``ongrid_power`` is the inverter's AC port, not the house meter."""

    def test_discharging_reports_positive_ongrid(self) -> None:
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=1000)
        sim.set_mode(MODE_PASSIVE, {"power": 800, "cd_time": 3600})
        sim.settle(10.0)

        assert sim.ongrid_power > 0
        # The identity the integration relies on: bat_power = pv - ongrid.
        assert sim.ongrid_power == sim.pv_power + sim.actual_power

    def test_charging_reports_negative_ongrid(self) -> None:
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=200)
        sim.set_mode(MODE_PASSIVE, {"power": -800, "cd_time": 3600})
        sim.settle(10.0)

        assert sim.ongrid_power < 0
        assert sim.ongrid_power == sim.pv_power + sim.actual_power

    def test_meter_is_separate_from_the_inverter_port(self) -> None:
        """The P1 meter nets the house against everything behind it."""
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=1500, pv=0)
        sim.set_mode(MODE_PASSIVE, {"power": 500, "cd_time": 3600})
        sim.settle(10.0)

        expected = (
            sim.gross_household_consumption
            + sim.standby_power
            - sim.house_pv_power
            - sim.ongrid_power
        )
        assert sim.grid_power == expected
        assert sim.grid_power != sim.ongrid_power


class TestAutoModeTracksTheMeter:
    """Auto mode is a closed loop on the CT, like the real firmware."""

    @pytest.mark.parametrize("house_load", [200, 800, 1800])
    def test_converges_to_zero_at_the_meter(self, house_load: int) -> None:
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=house_load)
        sim.settle(90.0)

        # Not exactly zero, and it should not be: the controller ignores
        # errors inside its deadband and the inverter tracks its setpoint to
        # about a percent, so a settled unit hovers within a few tens of
        # watts of the meter's zero rather than pinning it.
        assert abs(sim.grid_power) < _SETTLED_METER_BAND_W

    def test_a_load_step_shows_on_the_meter_before_it_is_covered(self) -> None:
        """CT lag plus inverter ramp is why a kettle is visible on P1."""
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=300)
        sim.settle(90.0)

        sim.set_house_load(2300)
        sim.settle(1.0)
        transient = sim.grid_power

        sim.settle(90.0)
        assert transient > 500
        assert abs(sim.grid_power) < _SETTLED_METER_BAND_W

    def test_surplus_is_absorbed_rather_than_exported(self) -> None:
        sim = BatterySimulator(initial_soc=50)
        _quiet_house(sim, load=400, pv=2500)
        sim.settle(90.0)

        assert sim.actual_power < 0
        assert abs(sim.grid_power) < _SETTLED_METER_BAND_W

    def test_export_remains_when_the_surplus_exceeds_the_inverter(self) -> None:
        sim = BatterySimulator(initial_soc=50, max_charge_power=2500)
        _quiet_house(sim, load=200, pv=6000)
        sim.settle(90.0)

        assert sim.setpoint_power == -2500
        # Everything the unit cannot absorb still leaves through the meter.
        assert sim.grid_power < -3000


class TestRampAndTracking:
    """The inverter slews; it does not teleport."""

    def test_setpoint_is_reached_gradually(self) -> None:
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=0)
        # ``set_mode`` already advances one update interval, so this is the
        # state after a single second of ramping.
        sim.set_mode(MODE_PASSIVE, {"power": 2500, "cd_time": 3600})
        assert sim.setpoint_power == 2500
        assert abs(sim.actual_power) <= POWER_RAMP_W_PER_SECOND * 1.05
        assert sim.actual_power < 2400

        sim.settle(5.0)
        assert 2400 < sim.actual_power < 2600

    def test_tracking_error_stays_small(self) -> None:
        sim = BatterySimulator(initial_soc=60)
        _quiet_house(sim, load=0)
        sim.set_mode(MODE_PASSIVE, {"power": 1000, "cd_time": 3600})
        sim.settle(10.0)

        for _ in range(20):
            sim.settle(1.0)
            assert abs(sim.actual_power - 1000) < 60


class TestEnergyAccounting:
    """Charging costs more than it returns."""

    def test_round_trip_loses_energy(self) -> None:
        sim = BatterySimulator(initial_soc=50, capacity_wh=5120)
        _quiet_house(sim, load=0)

        start_soc = sim.soc
        sim.set_mode(MODE_PASSIVE, {"power": -2000, "cd_time": 7200})
        sim.settle(1800.0, step=1.0)
        charged = sim.soc - start_soc

        sim.set_mode(MODE_PASSIVE, {"power": 2000, "cd_time": 7200})
        sim.settle(1800.0, step=1.0)
        discharged = charged - (sim.soc - start_soc)

        # Same AC watts, same duration: the pack gains less than it gives up.
        assert discharged > charged
        round_trip = charged / discharged
        expected = CHARGE_EFFICIENCY * DISCHARGE_EFFICIENCY
        assert expected - 0.05 < round_trip < expected + 0.05

    def test_soc_never_leaves_its_bounds(self) -> None:
        sim = BatterySimulator(initial_soc=97, capacity_wh=2560)
        _quiet_house(sim, load=0)
        sim.set_mode(MODE_PASSIVE, {"power": -2500, "cd_time": 7200})
        sim.settle(3600.0, step=5.0)

        assert 0 <= sim.soc <= 100

    def test_charge_tapers_near_full(self) -> None:
        sim = BatterySimulator(initial_soc=95, capacity_wh=5120)
        _quiet_house(sim, load=0)
        sim.set_mode(MODE_PASSIVE, {"power": -2500, "cd_time": 7200})
        sim.settle(10.0)

        assert -2500 < sim.setpoint_power < 0


class TestThermalModel:
    """The pack warms with use and cools toward ambient."""

    def test_warms_under_load_at_a_plausible_rate(self) -> None:
        sim = BatterySimulator(initial_soc=60, capacity_wh=5120)
        _quiet_house(sim, load=0)
        start = sim.battery_temp

        sim.set_mode(MODE_PASSIVE, {"power": 2500, "cd_time": 7200})
        sim.settle(600.0, step=5.0)

        rise = sim.battery_temp - start
        # Minutes of full-power work move a 5 kWh pack by a couple of kelvin,
        # not by tens of degrees.
        assert 0.3 < rise < 6.0

    def test_settles_near_ambient_when_idle(self) -> None:
        sim = BatterySimulator(initial_soc=60, capacity_wh=5120)
        _quiet_house(sim, load=0)
        sim.ct_connected = False
        sim.battery_temp = 40.0
        sim.settle(6 * 3600.0, step=30.0)

        assert abs(sim.battery_temp - sim.ambient_temp) < 5.0
        assert 0.0 < sim.battery_temp < AMBIENT_MEAN_C + 25

    def test_bms_refuses_to_charge_a_frozen_pack(self) -> None:
        sim = BatterySimulator(initial_soc=50)
        _quiet_house(sim, load=0, pv=3000)
        sim.battery_temp = BAT_TEMP_MIN_CHARGE_C - 5
        sim.settle(10.0)

        assert sim.setpoint_power == 0
        assert sim.get_state()["charg_flag"] == 0


class TestUpsMode:
    """UPS keeps the pack full so it can carry an outage."""

    def test_charges_toward_full_then_holds(self) -> None:
        sim = BatterySimulator(initial_soc=50, capacity_wh=2560)
        _quiet_house(sim, load=0)
        sim.set_mode(MODE_UPS, {})
        sim.settle(10.0)
        assert sim.actual_power < 0

        sim.soc = 100
        sim.settle(10.0)
        assert sim.actual_power == 0


class TestSolarSimulator:
    """A clear-sky curve that follows the sun rather than a constant."""

    def test_no_production_at_night(self) -> None:
        solar = SolarSimulator(peak_power_w=3500)
        midnight = datetime_module.datetime(2026, 6, 21, 0, 30)
        assert solar.clear_sky_factor(midnight) == 0.0

    def test_noon_beats_early_morning(self) -> None:
        solar = SolarSimulator(peak_power_w=3500)
        noon = solar.clear_sky_factor(datetime_module.datetime(2026, 6, 21, 12))
        morning = solar.clear_sky_factor(datetime_module.datetime(2026, 6, 21, 7))
        assert noon > morning > 0

    def test_summer_beats_winter(self) -> None:
        solar = SolarSimulator(peak_power_w=3500)
        summer = solar.clear_sky_factor(datetime_module.datetime(2026, 6, 21, 12))
        winter = solar.clear_sky_factor(datetime_module.datetime(2026, 12, 21, 12))
        assert summer > winter > 0

    def test_never_exceeds_nameplate(self) -> None:
        solar = SolarSimulator(peak_power_w=3500)
        for hour in range(24):
            moment = datetime_module.datetime(2026, 6, 21, hour)
            assert 0 <= solar.get_power(moment) <= 3500

    def test_annual_yield_is_plausible(self) -> None:
        """PVGIS puts northwest Europe near 900-1100 kWh per kWp per year."""
        solar = SolarSimulator(peak_power_w=1000)
        total_wh = 0.0
        for day in range(0, 365, 5):
            moment = datetime_module.datetime(2026, 1, 1) + datetime_module.timedelta(days=day)
            for hour in range(24):
                total_wh += solar.peak_power_w * solar.clear_sky_factor(
                    moment.replace(hour=hour, minute=30)
                )
        clear_sky_kwh = total_wh / 1000 * 5
        # This is the cloudless ceiling; real sites reach roughly 70-90% of
        # it, which lands the simulated array in the 900-1100 kWh/kWp PVGIS
        # band for northwest Europe.
        assert 1100 < clear_sky_kwh < 1600


class TestDeviceSpecs:
    """Each family reports its own pack and its own ceiling."""

    @pytest.mark.parametrize(
        ("device_type", "capacity_wh"),
        [
            ("VenusE 3.0", 5120),
            ("VenusA", 2080),
            ("VenusC", 2560),
            ("VenusD", 2560),
        ],
    )
    def test_capacity_per_family(self, device_type: str, capacity_wh: int) -> None:
        assert device_spec(device_type).capacity_wh == capacity_wh

    def test_unknown_model_falls_back_to_venus_e_shape(self) -> None:
        spec = device_spec("Totally Made Up 9000")
        assert spec.capacity_wh == 5120
        assert spec.max_discharge_power == 2500

    def test_simulator_adopts_the_family_spec(self) -> None:
        sim = BatterySimulator(device_type="VenusA")
        assert sim.capacity_wh == 2080
        assert sim.max_discharge_power == 1500

    def test_explicit_arguments_still_win(self) -> None:
        sim = BatterySimulator(device_type="VenusA", capacity_wh=10240, max_discharge_power=800)
        assert sim.capacity_wh == 10240
        assert sim.max_discharge_power == 800

    def test_mock_never_exceeds_the_integration_ceiling(self) -> None:
        """The mock duplicates the power table; keep the copies honest.

        ``tools/mock_device`` runs in a container without Home Assistant, so
        it cannot import ``custom_components.marstek.const``. This test is
        what stops the duplicate drifting into claiming a capability the
        integration would refuse to command.
        """
        family_names = {
            "VENUS_A": "VenusA",
            "VENUS_C": "VenusC",
            "VENUS_D": "VenusD",
            "VENUS_E": "VenusE 3.0",
            "VENUS_E_MINI": "Venus E mini",
        }
        for family, (_capacity, rated_power) in _FAMILY_SPECS.items():
            device_type = family_names[family.name]
            min_charge_power, max_discharge_power = get_device_power_limits(device_type)
            assert rated_power <= abs(min_charge_power)
            assert rated_power <= max_discharge_power


class TestPhaseSplit:
    """EM.GetStatus phases have to add up to the meter reading."""

    def test_single_phase_puts_everything_on_a(self) -> None:
        sim = BatterySimulator(initial_soc=60, phases=1)
        _quiet_house(sim, load=900)
        sim.settle(5.0)

        assert sim.em_b_power == 0
        assert sim.em_c_power == 0
        assert sim.em_a_power == sim.grid_power

    def test_three_phase_shares_the_load_and_sums_back(self) -> None:
        sim = BatterySimulator(initial_soc=60, phases=3)
        _quiet_house(sim, load=1200)
        sim.settle(5.0)

        assert sim.em_a_power + sim.em_b_power + sim.em_c_power == sim.grid_power
        assert sim.em_b_power != 0
