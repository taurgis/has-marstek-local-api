"""A faded pack moves state of charge faster than the nameplate admits."""

from __future__ import annotations

import random

import pytest
from mock_device.const import MODE_PASSIVE
from mock_device.device import MockMarstekDevice
from mock_device.simulators.battery import BatterySimulator

from custom_components.marstek.capacity_estimator import UsableCapacityEstimator


def test_nameplate_stays_at_the_family_rating_while_physics_use_true_capacity() -> None:
    """bat_cap and rated_capacity keep 5120 Wh when the pack is really 4600 Wh."""
    device = MockMarstekDevice(
        device_config={
            "device": "VenusE 3.0",
            "ver": 145,
            "ble_mac": "02deadbeef75",
            "wifi_mac": "02deadbeef76",
            "capacity_wh": 4600,
        },
        initial_soc=40,
        simulate=False,
    )

    status = device.build_response(1, "ES.GetStatus", {"id": 0})
    battery = device.build_response(2, "Bat.GetStatus", {"id": 0})
    assert status is not None
    assert battery is not None
    assert device.simulator.capacity_wh == 4600
    assert device.simulator.rated_capacity_wh == 5120
    assert status["result"]["bat_cap"] == 5120
    assert battery["result"]["rated_capacity"] == 5120
    assert battery["result"]["bat_capacity"] == 2048


def test_a_faded_pack_loses_state_of_charge_faster() -> None:
    """The same AC watts move a 4600 Wh pack further than a 5120 Wh pack."""
    faded = BatterySimulator(
        initial_soc=80,
        capacity_wh=4600,
        device_type="VenusE 3.0",
        rng=random.Random(1),
    )
    full = BatterySimulator(
        initial_soc=80,
        capacity_wh=5120,
        device_type="VenusE 3.0",
        rng=random.Random(1),
    )
    for simulator in (faded, full):
        simulator.set_mode(MODE_PASSIVE, {"power": 2000, "cd_time": 100_000})
        simulator.settle(1800.0)

    assert faded.soc < full.soc - 1.0
    assert faded.rated_capacity_wh == full.rated_capacity_wh == 5120


def test_soc_cycle_discharges_at_rated_power() -> None:
    """The cycle holds Passive and runs toward the low state-of-charge limit."""
    simulator = BatterySimulator(
        initial_soc=80,
        capacity_wh=4600,
        device_type="VenusE 3.0",
        rng=random.Random(2),
    )
    simulator.configure_capacity_experiment(soc_cycle=True)

    assert simulator.mode == MODE_PASSIVE
    simulator.settle(20.0)

    assert simulator.actual_power > 2000
    assert simulator.soc < 80


def test_time_scale_speeds_state_of_charge_and_reported_watts_together() -> None:
    """Accelerated physics and reported watts stay in proportion."""
    plain = BatterySimulator(
        initial_soc=80,
        device_type="VenusE 3.0",
        rng=random.Random(3),
    )
    scaled = BatterySimulator(
        initial_soc=80,
        device_type="VenusE 3.0",
        rng=random.Random(3),
    )
    scaled.configure_capacity_experiment(time_scale=4.0)
    for simulator in (plain, scaled):
        simulator.set_mode(MODE_PASSIVE, {"power": 2000, "cd_time": 100_000})
        simulator.settle(30.0)

    drop_plain = 80.0 - plain.soc
    drop_scaled = 80.0 - scaled.soc
    assert drop_plain > 0.2
    assert drop_scaled == pytest.approx(drop_plain * 4.0, rel=0.05)
    assert scaled.get_state()["power"] == pytest.approx(plain.get_state()["power"] * 4, rel=0.02)


def test_time_scale_scales_pv_with_the_ac_port() -> None:
    """PV watts scale with the inverter so the derived balance stays true."""
    channels = [{"channel": 1, "pv_power": 400.0, "pv_voltage": 40.0, "pv_current": 10.0}]
    simulator = BatterySimulator(
        initial_soc=60,
        device_type="VenusA",
        pv_channels=channels,
        rng=random.Random(4),
    )
    simulator.solar.pin_factor(1.0)
    simulator.configure_capacity_experiment(time_scale=4.0, power_fluctuation_pct=0)
    simulator.set_mode(MODE_PASSIVE, {"power": -400, "cd_time": 10_000})
    simulator.settle(3.0)

    state = simulator.get_state()
    assert state["pv_power"] == 1600
    assert state["pv_channels"][0]["pv_power"] == 1600
    # Reported battery power is ongrid - pv, which is the scaled AC port.
    assert state["ongrid_power"] - state["pv_power"] == state["power"]


def _pinned_pv(device: str, watts: float, *, capacity_wh: int, soc: int) -> BatterySimulator:
    """Return a Venus A/D whose MPPT is held at ``watts``."""
    channels = [{"channel": 1, "pv_power": watts, "pv_voltage": 40.0, "pv_current": watts / 40}]
    simulator = BatterySimulator(
        initial_soc=soc,
        capacity_wh=capacity_wh,
        device_type=device,
        pv_channels=channels,
        rng=random.Random(4),
    )
    simulator.solar.pin_factor(1.0)
    simulator.configure_capacity_experiment(power_fluctuation_pct=0)
    return simulator


def test_pv_only_charge_reaches_the_pack_without_inverter_loss() -> None:
    """800 W of MPPT for half an hour stores 400 Wh in the pack."""
    simulator = _pinned_pv("VenusA", 800, capacity_wh=1900, soc=40)
    simulator.set_mode(MODE_PASSIVE, {"power": -800, "cd_time": 10_000})
    simulator.settle(1800.0)

    assert simulator.ongrid_power == pytest.approx(0, abs=5)
    assert simulator.pv_power == pytest.approx(800, abs=1)
    assert simulator.soc == pytest.approx(40 + 400 / 1900 * 100, abs=0.4)


def test_mixed_pv_and_ac_charge_taxes_only_the_ac_port() -> None:
    """600 W PV plus 1000 W of AC import stores 600 + 1000 * 0.93 watt-hours per hour."""
    simulator = _pinned_pv("VenusD", 600, capacity_wh=2300, soc=40)
    simulator.set_mode(MODE_PASSIVE, {"power": -1600, "cd_time": 10_000})
    simulator.settle(1800.0)

    stored = (600 + 1000 * 0.93) * 0.5
    assert simulator.ongrid_power == pytest.approx(-1000, abs=5)
    assert simulator.soc == pytest.approx(40 + stored / 2300 * 100, abs=0.4)


def test_discharge_beside_pv_taxes_only_the_exported_watts() -> None:
    """Exporting 2000 W while PV supplies 800 W draws (2000 / 0.93 - 800) from the pack."""
    simulator = _pinned_pv("VenusA", 800, capacity_wh=1900, soc=80)
    simulator.set_mode(MODE_PASSIVE, {"power": 1200, "cd_time": 10_000})
    simulator.settle(1800.0)

    drawn = (2000 / 0.93 - 800) * 0.5
    assert simulator.ongrid_power == pytest.approx(2000, abs=5)
    assert simulator.soc == pytest.approx(80 - drawn / 1900 * 100, abs=0.4)


def test_venus_e_and_venus_c_keep_the_ac_only_pack_movement() -> None:
    """No device PV, so both families still move state of charge by AC / 0.93."""
    for device, capacity in (("VenusE 3.0", 4600), ("VenusC", 2400)):
        simulator = BatterySimulator(
            initial_soc=80,
            capacity_wh=capacity,
            device_type=device,
            rng=random.Random(4),
        )
        simulator.configure_capacity_experiment(power_fluctuation_pct=0)
        simulator.set_mode(MODE_PASSIVE, {"power": 2000, "cd_time": 10_000})
        simulator.settle(600.0)
        drawn = (2000 / 0.93) * (600 / 3600)
        assert simulator.pv_power == 0
        assert simulator.soc == pytest.approx(80 - drawn / capacity * 100, abs=0.4)


def test_pv_only_samples_recover_the_true_pack() -> None:
    """The estimator, reading the mock the way Home Assistant does, lands on 1900 Wh."""
    simulator = _pinned_pv("VenusA", 800, capacity_wh=1900, soc=20)
    simulator.set_mode(MODE_PASSIVE, {"power": -800, "cd_time": 100_000})
    simulator.settle(2.0)
    estimator = UsableCapacityEstimator()
    moment = 1_700_000_000.0
    for _ in range(200):
        simulator.settle(30.0)
        moment += 30.0
        state = simulator.get_state()
        estimator.observe(
            timestamp=moment,
            soc=float(state["soc"]),
            power_w=float(state["power"]),
            mode="passive",
            rated_wh=float(simulator.rated_capacity_wh),
            pv_w=float(state["pv_power"]),
            power_derived=True,
        )
        if estimator.estimate_wh is not None:
            break

    assert estimator.estimate_wh == pytest.approx(1900, rel=0.06)
    assert estimator.attributes()["efficiency_correction"] == "charge_median"


def test_asymmetric_efficiency_and_hidden_self_consumption() -> None:
    """A lower discharge efficiency and a hidden DC load both spend the pack faster."""
    shared = {
        "initial_soc": 80,
        "capacity_wh": 4600,
        "device_type": "VenusE 3.0",
    }
    baseline = BatterySimulator(**shared, rng=random.Random(5))
    lossy = BatterySimulator(**shared, rng=random.Random(5))
    baseline.configure_capacity_experiment(power_fluctuation_pct=0)
    lossy.configure_capacity_experiment(
        discharge_efficiency=0.80,
        pack_self_consumption_w=200,
        power_fluctuation_pct=0,
    )
    for simulator in (baseline, lossy):
        simulator.set_mode(MODE_PASSIVE, {"power": 2000, "cd_time": 100_000})
        simulator.settle(600.0)

    assert lossy.soc < baseline.soc - 1.0
    idle = BatterySimulator(**shared, rng=random.Random(6))
    idle.configure_capacity_experiment(pack_self_consumption_w=500, power_fluctuation_pct=0)
    idle.set_mode(MODE_PASSIVE, {"power": 0, "cd_time": 100_000})
    idle.settle(3600.0)
    assert idle.get_state()["power"] == 0
    assert idle.soc == pytest.approx(80 - 500 / 4600 * 100, abs=0.2)


def test_soc_jump_fires_once_while_charging() -> None:
    """Crossing the threshold adds the jump a single time."""
    simulator = BatterySimulator(
        initial_soc=80,
        capacity_wh=5120,
        device_type="VenusE 3.0",
        rng=random.Random(7),
    )
    simulator.configure_capacity_experiment(
        soc_jump_at=85,
        soc_jump_by=4,
        power_fluctuation_pct=0,
    )
    simulator.set_mode(MODE_PASSIVE, {"power": -2500, "cd_time": 100_000})
    simulator.settle(420.0)
    jumped = simulator.soc
    # 420s of 2500 W charging is about 5.3 points. Crossing 85 adds 4 once.
    assert jumped == pytest.approx(89.3, abs=0.5)

    simulator.soc = 80.0
    simulator.settle(420.0)
    assert simulator.soc == pytest.approx(85.3, abs=0.4)
    assert jumped > simulator.soc + 2


def test_sampled_discharge_converges_on_the_true_capacity() -> None:
    """Integer SOC and the AC port, sampled like a poll, recover 4600 Wh.

    The nameplate stays 5120 Wh. The estimator must not echo that field.
    """
    simulator = BatterySimulator(
        initial_soc=92,
        capacity_wh=4600,
        device_type="VenusE 3.0",
        rng=random.Random(0),
    )
    simulator.set_mode(MODE_PASSIVE, {"power": 2500, "cd_time": 100_000})
    simulator.settle(2.0)
    estimator = UsableCapacityEstimator()
    moment = 1_700_000_000.0
    for _ in range(160):
        simulator.settle(30.0)
        moment += 30.0
        state = simulator.get_state()
        estimator.observe(
            timestamp=moment,
            soc=float(state["soc"]),
            power_w=float(state["ongrid_power"]),
            mode="passive",
            rated_wh=float(simulator.rated_capacity_wh),
        )
        if estimator.estimate_wh is not None:
            break

    assert simulator.rated_capacity_wh == 5120
    assert estimator.estimate_wh == pytest.approx(4600, rel=0.06)
    assert estimator.state_of_health == pytest.approx(89.8, abs=3.0)
