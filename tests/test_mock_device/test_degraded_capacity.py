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
