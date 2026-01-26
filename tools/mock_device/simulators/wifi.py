"""WiFi signal strength simulator."""

import random
import time


class WiFiSimulator:
    """Simulates realistic WiFi signal strength variations."""

    def __init__(self, base_rssi: int = -55):
        """Initialize WiFi simulator.

        Args:
            base_rssi: Base RSSI value in dBm (typical: -30 excellent to -90 poor)
        """
        self.base_rssi = base_rssi
        self._current_rssi = base_rssi
        self._last_update = 0.0
        self._drift_target = base_rssi
        self._interference_until = 0.0
        self._interference_amount = 0

    def get_rssi(self) -> int:
        """Get current RSSI with realistic variations.

        Returns realistic WiFi signal variations:
        - Base signal strength (-30 to -90 dBm typical)
        - Slow drift (±5 dBm over minutes)
        - Fast micro-fluctuations (±2 dBm per second)
        - Occasional interference events (±10-20 dBm)
        """
        now = time.time()

        # Update drift target every 30-60 seconds
        if now - self._last_update > random.uniform(30, 60):
            self._last_update = now
            drift = random.randint(-5, 5)
            self._drift_target = max(-90, min(-30, self.base_rssi + drift))

            # 5% chance of interference event
            if random.random() < 0.05:
                self._interference_amount = random.randint(10, 20)
                self._interference_until = now + random.uniform(5, 30)

        # Gradual move toward drift target
        if self._current_rssi < self._drift_target:
            self._current_rssi = min(self._current_rssi + 1, self._drift_target)
        elif self._current_rssi > self._drift_target:
            self._current_rssi = max(self._current_rssi - 1, self._drift_target)

        # Add micro-fluctuation
        rssi = self._current_rssi + random.randint(-2, 2)

        # Apply interference if active
        if now < self._interference_until:
            rssi -= self._interference_amount

        # Clamp to realistic range
        return max(-95, min(-25, rssi))
