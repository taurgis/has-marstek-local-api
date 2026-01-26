"""Mock Marstek device UDP server."""

import json
import socket
import threading
import time
from typing import Any

from .const import DEFAULT_CONFIG, DEFAULT_UDP_PORT, MODE_AUTO, MODE_MANUAL, MODE_PASSIVE
from .handlers import (
    get_static_state,
    handle_bat_get_status,
    handle_em_get_status,
    handle_es_get_mode,
    handle_es_get_status,
    handle_es_set_mode,
    handle_get_device,
    handle_pv_get_status,
    handle_wifi_get_status,
)
from .simulators import BatterySimulator
from .utils import get_local_ip


class MockMarstekDevice:
    """Mock Marstek device that responds to UDP requests."""

    def __init__(
        self,
        port: int = DEFAULT_UDP_PORT,
        device_config: dict[str, Any] | None = None,
        ip_override: str | None = None,
        initial_soc: int = 50,
        simulate: bool = True,
    ):
        self.port = port
        self.config = {**DEFAULT_CONFIG, **(device_config or {})}
        self.ip = ip_override or get_local_ip()
        self.sock: socket.socket | None = None

        # Battery simulator
        self.simulator = BatterySimulator(initial_soc=initial_soc)
        self.simulate = simulate

        # Static fallback values
        self._static_soc = initial_soc
        self._static_power = 0
        self._static_mode = MODE_AUTO

    def start(self) -> None:
        """Start the mock device server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(("0.0.0.0", self.port))

        self._print_banner()

        if self.simulate:
            self.simulator.start()
            self._status_thread = threading.Thread(target=self._status_display, daemon=True)
            self._status_thread.start()

        try:
            while True:
                self._handle_request()
        except KeyboardInterrupt:
            print("\nShutting down mock device...")
        finally:
            if self.simulate:
                self.simulator.stop()
            if self.sock:
                self.sock.close()

    def _print_banner(self) -> None:
        """Print startup banner."""
        print("=" * 60)
        print("MOCK MARSTEK DEVICE")
        print("=" * 60)
        print(f"Device: {self.config['device']}")
        print(f"BLE MAC: {self.config['ble_mac']}")
        print(f"WiFi MAC: {self.config['wifi_mac']}")
        print(f"IP: {self.ip}")
        print(f"Listening on UDP port {self.port}")
        print(f"Simulation: {'ENABLED' if self.simulate else 'DISABLED'}")
        print(f"Initial SOC: {self.simulator.soc}%")
        print("=" * 60)
        print("Mode behaviors:")
        print("  Auto: Discharges to offset household consumption (P1 meter = 0)")
        print("  Manual: Follows scheduled charge/discharge times")
        print("  Passive: Fixed power for set duration")
        print("=" * 60)
        print()

    def _status_display(self) -> None:
        """Display battery status periodically."""
        while True:
            time.sleep(5)
            state = self.simulator.get_state()

            power_indicator = (
                "⚡ Charging"
                if state["power"] < 0
                else "🔋 Discharging"
                if state["power"] > 0
                else "💤 Idle"
            )
            grid_indicator = (
                f"📥 Buying {state['grid_power']}W"
                if state["grid_power"] > 50
                else f"📤 Selling {-state['grid_power']}W"
                if state["grid_power"] < -50
                else "⚖️ Balanced"
            )
            passive_info = ""
            if state["mode"] == MODE_PASSIVE and state["passive_remaining"] > 0:
                passive_info = f" | ⏱️ {state['passive_remaining']}s left"

            print(
                f"[STATUS] SOC: {state['soc']}% | Batt: {state['power']}W | "
                f"🏠 {state['household_consumption']}W | {grid_indicator} | "
                f"Mode: {state['mode']}{passive_info} | {power_indicator}"
            )

    def _handle_request(self) -> None:
        """Handle incoming UDP request."""
        data, addr = self.sock.recvfrom(4096)
        sender_ip, sender_port = addr

        try:
            request = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            print(f"[{time.strftime('%H:%M:%S')}] Invalid JSON from {sender_ip}:{sender_port}")
            return

        request_id = request.get("id", 0)
        method = request.get("method", "")

        print(f"[{time.strftime('%H:%M:%S')}] Request from {sender_ip}:{sender_port}")
        print(f"   Method: {method}")
        print(f"   ID: {request_id}")

        response = self._build_response(request_id, method, request.get("params", {}))

        if response:
            response_bytes = json.dumps(response).encode("utf-8")
            self.sock.sendto(response_bytes, addr)
            print(f"   -> Sent response: {method}")
        else:
            print("   -> Unknown method, no response")

        print()

    def _get_state(self) -> dict[str, Any]:
        """Get current device state."""
        if self.simulate:
            return self.simulator.get_state()
        return get_static_state(self._static_soc, self._static_power, self._static_mode)

    def _build_response(
        self, request_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Build response for a given method."""
        src = f"{self.config['device']}-{self.config['ble_mac']}"
        state = self._get_state()

        if method == "Marstek.GetDevice":
            return handle_get_device(request_id, src, self.config, self.ip)

        elif method == "ES.GetStatus":
            return handle_es_get_status(request_id, src, state)

        elif method == "ES.GetMode":
            return handle_es_get_mode(request_id, src, state)

        elif method == "PV.GetStatus":
            return handle_pv_get_status(request_id, src)

        elif method == "Wifi.GetStatus":
            return handle_wifi_get_status(request_id, src, self.config, self.ip, state)

        elif method == "EM.GetStatus":
            return handle_em_get_status(request_id, src, state)

        elif method == "Bat.GetStatus":
            return handle_bat_get_status(
                request_id, src, state, self.simulator.capacity_wh
            )

        elif method == "ES.SetMode":
            config = params.get("config", {})
            mode = config.get("mode", MODE_AUTO)

            if self.simulate:
                if mode == MODE_PASSIVE:
                    self.simulator.set_mode(mode, config.get("passive_cfg", {}))
                elif mode == MODE_MANUAL:
                    self.simulator.set_mode(mode, config.get("manual_cfg", {}))
                else:
                    self.simulator.set_mode(mode)
            else:
                self._static_mode = mode

            print(f"   Mode changed to: {mode}")
            return handle_es_set_mode(request_id, src)

        return None
