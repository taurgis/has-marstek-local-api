"""Mock Marstek device UDP server."""

import json
import socket
import threading
import time
from typing import Any

from .const import DEFAULT_CONFIG, DEFAULT_UDP_PORT, MODE_AI, MODE_AUTO, MODE_MANUAL, MODE_PASSIVE
from .handlers import (
    get_static_state,
    handle_bat_get_status,
    handle_ble_get_status,
    handle_em_get_status,
    handle_es_get_mode,
    handle_es_get_status,
    handle_es_set_mode,
    handle_get_device,
    handle_pv_get_status,
    handle_wifi_get_status,
)
from .simulators import BatterySimulator
from .utils import (
    get_local_ip,
    load_persistent_state,
    reset_persistent_state,
    resolve_state_dir,
    save_persistent_state,
)


class MockMarstekDevice:
    """Mock Marstek device that responds to UDP requests."""

    def __init__(
        self,
        port: int = DEFAULT_UDP_PORT,
        device_config: dict[str, Any] | None = None,
        ip_override: str | None = None,
        initial_soc: int = 50,
        simulate: bool = True,
        include_bat_power: bool = False,
        state_dir: str | None = None,
        reset_state: bool = False,
    ):
        self.port = port
        self.config = {**DEFAULT_CONFIG, **(device_config or {})}
        self.ip = ip_override or get_local_ip()
        self.sock: socket.socket | None = None
        self._state_dir = (
            resolve_state_dir(state_dir) if state_dir is not None else None
        )
        
        # Whether to include bat_power in ES.GetStatus responses
        # Default False since real Venus E 3.0 does NOT return bat_power
        # Enable for testing the direct bat_power code path
        self.include_bat_power = include_bat_power

        if self._state_dir is not None and reset_state:
            reset_persistent_state(self.config["ble_mac"], self._state_dir)

        persisted_state = (
            load_persistent_state(self.config["ble_mac"], self._state_dir)
            if self._state_dir is not None
            else None
        )

        # Battery simulator (tracks energy stats internally)
        self.simulator = BatterySimulator(
            initial_soc=initial_soc,
            persist_callback=self._persist_state if self._state_dir is not None else None,
        )
        self.simulate = simulate

        # BLE connection state (for mock purposes always disconnected)
        self._ble_connected = False

        # Static fallback values
        self._static_soc = initial_soc
        self._static_power = 0
        self._static_mode = MODE_AUTO
        self._static_totals = self._default_static_totals()

        if persisted_state:
            self.simulator.apply_persistent_state(persisted_state)
            self._static_soc = int(persisted_state.get("soc", initial_soc))
            self._static_totals = self._totals_from_state(persisted_state)

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
            self._persist_state()
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
        print("  AI: Time-based strategy (charges at night, conservative during day)")
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
            # P1 meter reading: positive = importing, negative = exporting
            p1 = state["grid_power"]
            if abs(p1) < 20:
                p1_indicator = "⚖️ P1=0 (balanced)"
            elif p1 > 0:
                p1_indicator = f"📥 P1=+{p1}W (import)"
            else:
                p1_indicator = f"📤 P1={p1}W (export)"

            passive_info = ""
            if state["mode"] == MODE_PASSIVE and state["passive_remaining"] > 0:
                passive_info = f" | ⏱️ {state['passive_remaining']}s left"

            print(
                f"[STATUS] SOC: {state['soc']}% | Batt: {state['power']}W | "
                f"🏠 {state['household_consumption']}W | {p1_indicator} | "
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
        return get_static_state(
            self._static_soc,
            self._static_power,
            self._static_mode,
            self._static_totals,
        )

    def _default_static_totals(self) -> dict[str, float]:
        return {
            "total_pv_energy": 0.0,
            "total_grid_output_energy": 0.0,
            "total_grid_input_energy": 0.0,
            "total_load_energy": 0.0,
        }

    def _totals_from_state(self, state: dict[str, Any]) -> dict[str, float]:
        return {
            "total_pv_energy": float(state.get("total_pv_energy", 0.0)),
            "total_grid_output_energy": float(
                state.get("total_grid_output_energy", 0.0)
            ),
            "total_grid_input_energy": float(
                state.get("total_grid_input_energy", 0.0)
            ),
            "total_load_energy": float(state.get("total_load_energy", 0.0)),
        }

    def _persist_state(self, state: dict[str, Any] | None = None) -> None:
        if state is None:
            if self._state_dir is None:
                return
            if state is None:
                if self.simulate:
                    state = self.simulator.get_persistent_state()
                else:
                    state = {
                        "soc": float(self._static_soc),
                        **self._static_totals,
                    }
        try:
            save_persistent_state(self.config["ble_mac"], self._state_dir, state)
        except OSError as exc:
            print(f"[WARN] Failed to persist mock state: {exc}")

    def _build_response(
        self, request_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Build response for a given method."""
        src = f"{self.config['device']}-{self.config['ble_mac']}"
        state = self._get_state()

        if method == "Marstek.GetDevice":
            return handle_get_device(request_id, src, self.config, self.ip)

        elif method == "BLE.GetStatus":
            return handle_ble_get_status(
                request_id, src, self.config, self._ble_connected
            )

        elif method == "ES.GetStatus":
            # State includes energy stats from simulator
            state_with_capacity = {**state, "capacity_wh": self.simulator.capacity_wh}
            return handle_es_get_status(
                request_id,
                src,
                state_with_capacity,
                self.config.get("device", ""),
                include_bat_power=self.include_bat_power,
            )

        elif method == "ES.GetMode":
            return handle_es_get_mode(request_id, src, state)

        elif method == "PV.GetStatus":
            # PV is supported by Venus A and Venus D; Venus C/E do NOT
            device_type = self.config.get("device", "").lower()
            if (
                "venusa" not in device_type
                and "venus a" not in device_type
                and "venusd" not in device_type
                and "venus d" not in device_type
            ):
                # Return error for unsupported method on Venus C/E devices
                return {
                    "id": request_id,
                    "src": src,
                    "error": {
                        "code": -32601,
                        "message": "Method not found",
                    },
                }
            pv_channels = self.config.get("pv_channels")
            if isinstance(pv_channels, list) and pv_channels:
                pv_state = {
                    "pv_channels": pv_channels,
                }
            else:
                pv_state = {
                    "pv_power": state.get("pv_power", 0),
                    "pv_voltage": state.get("pv_voltage", 0),
                    "pv_current": state.get("pv_current", 0),
                }
            return handle_pv_get_status(request_id, src, pv_state)

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
                elif mode == MODE_AI:
                    self.simulator.set_mode(mode, config.get("ai_cfg", {}))
                else:
                    self.simulator.set_mode(mode)
            else:
                self._static_mode = mode

            print(f"   Mode changed to: {mode}")
            return handle_es_set_mode(request_id, src)

        return None
