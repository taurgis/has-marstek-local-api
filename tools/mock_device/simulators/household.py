"""Household power consumption simulator."""

import random
import threading
import time
from datetime import datetime


class HouseholdSimulator:
    """Simulates realistic household power consumption (what a P1 meter would see)."""

    def __init__(self, base_load: int = 200):
        """Initialize household simulator.
        
        Args:
            base_load: Base load in watts (fridge, standby devices, etc.)
        """
        self.base_load = base_load
        self.current_consumption = base_load
        self._lock = threading.Lock()

        # Event simulation
        self._cooking_until: float = 0
        self._cooking_power: int = 0
        self._appliance_until: float = 0
        self._appliance_power: int = 0

        # Time-based patterns
        self._last_event_check: float = 0

        # Second-by-second fluctuation state
        self._fluctuation_base: int = 0
        self._fluctuation_target: int = 0
        self._fluctuation_step: float = 0
        self._last_fluctuation_update: float = 0

    def get_consumption(self) -> int:
        """Get current household power consumption in watts (positive = consuming from grid)."""
        with self._lock:
            now = time.time()

            # Check for random events every 30 seconds
            if now - self._last_event_check > 30:
                self._last_event_check = now
                self._maybe_trigger_event(now)

            # Calculate current consumption
            consumption = self.base_load

            # Add time-of-day variation (morning/evening peaks)
            hour = datetime.now().hour
            consumption += self._get_time_based_load(hour)

            # Add active events
            if now < self._cooking_until:
                consumption += self._cooking_power
            if now < self._appliance_until:
                consumption += self._appliance_power

            # Add realistic second-by-second micro-fluctuations
            consumption += self._get_micro_fluctuation(now)

            self.current_consumption = max(50, consumption)  # Minimum 50W
            return self.current_consumption

    def _get_micro_fluctuation(self, now: float) -> int:
        """Get micro-fluctuations that change every second."""
        # Update fluctuation target every 1-3 seconds
        if now - self._last_fluctuation_update > random.uniform(0.5, 2.0):
            self._last_fluctuation_update = now
            self._fluctuation_base = self._fluctuation_target

            # Random walk with occasional spikes
            if random.random() < 0.1:  # 10% chance of a spike
                spike = random.choice([-1, 1]) * random.randint(50, 200)
                self._fluctuation_target = max(-100, min(300, self._fluctuation_base + spike))
            else:
                drift = random.randint(-20, 20)
                self._fluctuation_target = max(-50, min(150, self._fluctuation_base + drift))

        # Smooth interpolation between base and target
        elapsed = now - self._last_fluctuation_update
        progress = min(1.0, elapsed / 1.0)
        current = self._fluctuation_base + (self._fluctuation_target - self._fluctuation_base) * progress

        return int(current)

    def _get_time_based_load(self, hour: int) -> int:
        """Get additional load based on time of day."""
        if 6 <= hour < 9:  # Morning peak
            return random.randint(200, 500)
        elif 9 <= hour < 17:  # Midday
            return random.randint(50, 150)
        elif 17 <= hour < 22:  # Evening peak
            return random.randint(300, 800)
        else:  # Night
            return random.randint(0, 50)

    def _maybe_trigger_event(self, now: float) -> None:
        """Randomly trigger household events."""
        hour = datetime.now().hour

        # Cooking events
        if now >= self._cooking_until:
            cooking_chance = 0.05
            if hour in [7, 8, 12, 13, 18, 19, 20]:
                cooking_chance = 0.20

            if random.random() < cooking_chance:
                self._cooking_power = random.randint(1500, 3000)
                self._cooking_until = now + random.randint(5, 30) * 60
                print(
                    f"[HOUSE] 🍳 Cooking started: {self._cooking_power}W "
                    f"for {int((self._cooking_until - now) / 60)} min"
                )

        # Appliance events
        if now >= self._appliance_until:
            if random.random() < 0.03:
                appliances = [
                    ("Washing machine", 400, 800, 30, 60),
                    ("Dryer", 2000, 3000, 45, 90),
                    ("Dishwasher", 1200, 1800, 60, 120),
                    ("Vacuum cleaner", 800, 1500, 10, 30),
                    ("Iron", 1000, 2000, 10, 20),
                    ("Kettle", 2000, 3000, 2, 5),
                    ("Microwave", 800, 1200, 2, 10),
                ]
                name, min_power, max_power, min_mins, max_mins = random.choice(appliances)
                self._appliance_power = random.randint(min_power, max_power)
                self._appliance_until = now + random.randint(min_mins, max_mins) * 60
                print(
                    f"[HOUSE] 🔌 {name} started: {self._appliance_power}W "
                    f"for {int((self._appliance_until - now) / 60)} min"
                )

    def force_cooking_event(self, power: int = 2500, duration_mins: int = 15) -> None:
        """Force a cooking event for testing."""
        with self._lock:
            self._cooking_power = power
            self._cooking_until = time.time() + duration_mins * 60
            print(f"[HOUSE] 🍳 Forced cooking: {power}W for {duration_mins} min")
