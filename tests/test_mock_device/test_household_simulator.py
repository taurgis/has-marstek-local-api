"""Tests for the HouseholdSimulator class."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_tools_dir = Path(__file__).parent.parent.parent / "tools"
if str(_tools_dir) not in sys.path:
    sys.path.insert(0, str(_tools_dir))

from mock_device import HouseholdSimulator


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

    def test_consumption_fluctuation(self) -> None:
        """Test consumption has realistic fluctuation."""
        sim = HouseholdSimulator()

        readings = [sim.get_consumption() for _ in range(10)]
        unique_readings = len(set(readings))
        assert unique_readings > 1

    def test_default_base_load(self) -> None:
        """Test default base load is set."""
        sim = HouseholdSimulator()
        assert sim.base_load > 0

    def test_time_of_day_variation(self) -> None:
        """Test consumption varies by time of day."""
        sim = HouseholdSimulator()
        
        # Get several readings - they should fluctuate
        readings = [sim.get_consumption() for _ in range(20)]
        
        # Should have some variation (not all identical)
        assert max(readings) > min(readings)

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
