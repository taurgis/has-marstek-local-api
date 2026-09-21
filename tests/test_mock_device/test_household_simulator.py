"""Tests for the HouseholdSimulator class."""

from __future__ import annotations

import datetime as datetime_module
import itertools
import types

import pytest
from mock_device import HouseholdSimulator
from mock_device.simulators import household as household_module


@pytest.fixture
def simulated_clock(monkeypatch: pytest.MonkeyPatch):
    """Drive the household simulator from a clock the test controls.

    The load curve is smooth and slow on purpose: a dwelling does not change
    by hundreds of watts between two reads in the same microsecond. Anything
    testing variation therefore has to move time forward.
    """
    state = {"seconds": 1_700_000_000.0}
    start = datetime_module.datetime(2026, 3, 10, 0, 0, 0)

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz: datetime_module.tzinfo | None = None) -> datetime_module.datetime:
            return start + datetime_module.timedelta(seconds=state["seconds"] - 1_700_000_000.0)

    monkeypatch.setattr(
        household_module, "time", types.SimpleNamespace(time=lambda: state["seconds"])
    )
    monkeypatch.setattr(household_module, "datetime", _FrozenDatetime)

    def advance(seconds: float) -> None:
        state["seconds"] += seconds

    return advance


class TestHouseholdSimulator:
    """Tests for the HouseholdSimulator class."""

    def test_get_consumption_returns_positive(self) -> None:
        """Test household consumption is always positive."""
        sim = HouseholdSimulator()

        for _ in range(10):
            consumption = sim.get_consumption()
            assert consumption >= 50

    def test_base_load_included(self) -> None:
        """Test base load is always included in consumption."""
        sim = HouseholdSimulator()
        sim.base_load = 200

        consumption = sim.get_consumption()
        assert consumption >= 50

    def test_force_cooking_event(self) -> None:
        """Test forced cooking event increases consumption."""
        sim = HouseholdSimulator()
        baseline = sim.base_load

        sim.force_cooking_event(power=2500, duration_mins=15)
        with_cooking = sim.get_consumption()

        assert with_cooking > baseline + 2000

    def test_consumption_fluctuation(self, simulated_clock) -> None:
        """Test consumption has realistic fluctuation as time passes."""
        sim = HouseholdSimulator()

        readings = []
        for _ in range(60):
            readings.append(sim.get_consumption())
            simulated_clock(1.0)

        assert len(set(readings)) > 1

    def test_consumption_is_smooth(self, simulated_clock) -> None:
        """Consecutive seconds move by watts, not hundreds of watts.

        Resampling a wide random range every call used to turn the baseline
        into white noise that buried every real signal in the trace. A real
        dwelling does take the odd large step when an appliance switches on,
        so the assertion is on the bulk of the distribution rather than its
        maximum.
        """
        sim = HouseholdSimulator()

        readings = []
        for _ in range(120):
            readings.append(sim.get_consumption())
            simulated_clock(1.0)

        steps = sorted(abs(b - a) for a, b in itertools.pairwise(readings))
        assert steps[len(steps) // 2] < 25
        assert steps[len(steps) * 9 // 10] < 100

    def test_default_base_load(self) -> None:
        """Test default base load is set."""
        sim = HouseholdSimulator()
        assert sim.base_load > 0

    def test_time_of_day_variation(self, simulated_clock) -> None:
        """The evening peak has to outweigh the small hours."""
        sim = HouseholdSimulator()

        hourly: dict[int, int] = {}
        for hour in range(24):
            hourly[hour] = sim.get_consumption()
            simulated_clock(3600.0)

        assert max(hourly.values()) > min(hourly.values())
        # Two-peak residential shape: evening is the heaviest hour of the day
        # and the small hours are the lightest.
        assert hourly[18] > hourly[3]
        assert hourly[7] > hourly[3]

    def test_daily_energy_is_realistic(self, simulated_clock) -> None:
        """A day of the profile lands near the EU average dwelling."""
        sim = HouseholdSimulator()

        watt_seconds = 0.0
        step = 60.0
        for _ in range(24 * 60):
            watt_seconds += sim.get_consumption() * step
            simulated_clock(step)

        kwh = watt_seconds / 3600 / 1000
        # ODYSSEE-MURE puts the EU average household near 10 kWh/day; the
        # daily occupancy scale and random appliance events widen the band,
        # and a dwelling large enough to own a home battery sits above the
        # apartment-heavy average.
        assert 7.0 < kwh < 15.0

    def test_force_cooking_event_duration(self) -> None:
        """Test cooking event has a duration effect."""
        sim = HouseholdSimulator()

        # Force short cooking event
        sim.force_cooking_event(power=3000, duration_mins=1)

        # Should see elevated consumption immediately
        consumption1 = sim.get_consumption()
        assert consumption1 > sim.base_load + 2500

    def test_current_consumption_attribute(self) -> None:
        """Test current_consumption is updated on get_consumption."""
        sim = HouseholdSimulator()

        # Call get_consumption to update
        consumption = sim.get_consumption()

        # current_consumption should match returned value
        assert sim.current_consumption == consumption
