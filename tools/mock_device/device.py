"""Mock Marstek device UDP server."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import Counter
from typing import Any

from custom_components.marstek.firmware_profile import (
    DeviceFamily,
    FirmwareProfile,
    resolve_firmware_profile,
)
from custom_components.marstek.pymarstek.const import (
    CMD_BLE_ADV,
    CMD_DOD_SET,
    CMD_LED_CTRL,
)
from custom_components.marstek.pymarstek.network import is_loopback_host
from custom_components.marstek.pymarstek.validators import json_rpc_wire_id

from .const import (
    DEFAULT_CONFIG,
    DEFAULT_PHASE_COUNT,
    DEFAULT_UDP_PORT,
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    MODE_UPS,
)
from .firmware_quirks import (
    pv_method_not_found_extra_data,
    reports_es_bat_power,
    supports_set_ver_and_factory_reset,
    supports_wifi_set_config,
)
from .handlers import (
    get_static_state,
    handle_bat_get_status,
    handle_ble_get_status,
    handle_em_get_status,
    handle_es_get_mode,
    handle_es_get_status,
    handle_es_set_mode,
    handle_get_device,
    handle_invalid_params,
    handle_method_not_found,
    handle_pv_get_status,
    handle_sys_write,
    handle_wifi_get_status,
    handle_wifi_set_config,
)
from .simulators import BatterySimulator
from .utils import (
    get_local_ip,
    load_persistent_state,
    reset_persistent_state,
    resolve_state_dir,
    save_persistent_state,
)

# Seconds between summaries of dropped datagrams. A shared UDP port can pair
# this socket with a talker that answers everything it sends; printing a line
# per dropped datagram is what turns such a loop into gigabytes of log.
DROP_LOG_INTERVAL = 10.0

# Seconds between simulator status lines. Every mock prints these for as long
# as it runs, so the default stays coarse; pass --status-interval to tighten.
DEFAULT_STATUS_INTERVAL = 30.0


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
        verbose: bool = True,
        status_interval: float = DEFAULT_STATUS_INTERVAL,
        house_pv_wp: int | None = None,
        phases: int = DEFAULT_PHASE_COUNT,
    ) -> None:
        self.port = port
        self.config = {**DEFAULT_CONFIG, **(device_config or {})}
        self.profile: FirmwareProfile = resolve_firmware_profile(
            self.config.get("device"),
            self.config.get("ver"),
        )
        if self.profile.firmware_version is None:
            raise ValueError("Mock firmware version must be a non-negative integer")
        self.config["ver"] = self.profile.firmware_version
        self.ip = ip_override or get_local_ip()
        self.sock: socket.socket | None = None
        self._state_dir = resolve_state_dir(state_dir) if state_dir is not None else None

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

        # Battery simulator (tracks energy stats internally). Capacity and
        # power limits follow the device family so a Venus A never reports a
        # Venus E sized pack.
        pv_channels = self.config.get("pv_channels")
        self.simulator = BatterySimulator(
            initial_soc=initial_soc,
            persist_callback=self._persist_state if self._state_dir is not None else None,
            device_type=self.config.get("device"),
            pv_channels=pv_channels if isinstance(pv_channels, list) else None,
            house_pv_wp=house_pv_wp,
            phases=phases,
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

        # Control firmware freezes Open API after a 0-byte UDP datagram
        # (VNSE3-0 json_data.c / CH395 recv path).
        self._openapi_frozen = False

        # One line per handled request, and dropped datagrams summarised
        # rather than printed individually.
        self.verbose = verbose
        self.status_interval = status_interval
        self._status_thread: threading.Thread | None = None
        self._dropped: Counter[str] = Counter()
        self._last_drop_sender = "unknown"
        self._last_drop_log = 0.0

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
            if self.status_interval > 0:
                self._status_thread = threading.Thread(target=self._status_display, daemon=True)
                self._status_thread.start()

        try:
            while True:
                self._handle_request()
        except KeyboardInterrupt:
            print("\nShutting down mock device...")
        finally:
            # A burst can end inside the rate-limit window, so print the tail
            # counts rather than losing them on the way out.
            self._flush_dropped()
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
        print(f"Firmware: {self.config['ver']}")
        print(f"BLE MAC: {self.config['ble_mac']}")
        print(f"WiFi MAC: {self.config['wifi_mac']}")
        print(f"IP: {self.ip}")
        print(f"Listening on UDP port {self.port}")
        print(f"Simulation: {'ENABLED' if self.simulate else 'DISABLED'}")
        print(f"Initial SOC: {self.simulator.soc}%")
        print(
            f"Pack: {self.simulator.capacity_wh} Wh, "
            f"charge {self.simulator.max_charge_power} W / "
            f"discharge {self.simulator.max_discharge_power} W"
        )
        print(
            f"Home: {self.simulator.solar.peak_power_w} Wp rooftop PV, "
            f"{self.simulator.phases}-phase supply"
        )
        print("=" * 60)
        print("Mode behaviors:")
        print("  Auto: Regulates against the CT to hold the P1 meter at zero")
        print("  AI: Cheap-window grid charging, then self-consumption")
        print("  Manual: Follows scheduled charge/discharge times")
        print("  Passive: Fixed power for set duration")
        print("  UPS: Charges to full and holds the pack as backup")
        print("=" * 60)
        print()

    def _status_display(self) -> None:
        """Display battery status periodically."""
        while True:
            time.sleep(self.status_interval)
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

            solar_info = ""
            if state["house_pv_power"] or state["pv_power"]:
                solar_info = f" | ☀️ {state['house_pv_power'] + state['pv_power']}W"

            print(
                f"[STATUS] SOC: {state['soc']}% | Batt: {state['power']}W | "
                f"🏠 {state['household_consumption']}W{solar_info} | {p1_indicator} | "
                f"🌡️ {state['battery_temp']}°C | "
                f"Mode: {state['mode']}{passive_info} | {power_indicator}"
            )

    def _log_dropped(self, reason: str, sender: str) -> None:
        """Summarise dropped datagrams at most once per DROP_LOG_INTERVAL.

        Anything unanswerable arrives in bursts rather than singly, so the
        counts carry the signal and printing each one only costs disk. Counts
        are kept per reason so a burst of one kind cannot be reported under
        another, and they survive until printed by _flush_dropped.
        """
        self._dropped[reason] += 1
        self._last_drop_sender = sender
        now = time.monotonic()
        if now - self._last_drop_log < DROP_LOG_INTERVAL:
            return
        self._last_drop_log = now
        self._flush_dropped()

    def _flush_dropped(self) -> None:
        """Print the pending dropped-datagram counts, if any."""
        if not self._dropped:
            return
        total = sum(self._dropped.values())
        breakdown = ", ".join(
            f"{reason} x{count}" for reason, count in sorted(self._dropped.items())
        )
        self._dropped.clear()
        print(
            f"[{time.strftime('%H:%M:%S')}] Dropped {total} datagram(s) "
            f"({breakdown}); most recent from {self._last_drop_sender}"
        )

    def _handle_request(self) -> None:
        """Handle incoming UDP request."""
        assert self.sock is not None
        data, addr = self.sock.recvfrom(4096)
        sender_ip, sender_port = addr
        sender = f"{sender_ip}:{sender_port}"

        if self._openapi_frozen:
            self._log_dropped(f"Open API frozen, {len(data)} bytes", sender)
            return

        if not data:
            self._openapi_frozen = True
            print(
                f"[{time.strftime('%H:%M:%S')}] Empty UDP datagram from "
                f"{sender}; freezing Open API (Control firmware behavior)"
            )
            return

        try:
            request = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._log_dropped("invalid JSON", sender)
            self._send_openapi_datagram(
                {
                    "id": 0,
                    "error": {"code": -32700, "message": "Parse error"},
                },
                addr,
            )
            return

        if not isinstance(request, dict):
            self._log_dropped("non-object JSON", sender)
            return

        # A reply carries result or error; a request carries method. Answering
        # a reply is what spins two sockets sharing a UDP port into a packet
        # storm, so drop it. A request with a missing or malformed method is
        # still a request: firmware answers -32601 for it ("Method missing or
        # not available on this firmware", docs/marstek_device_openapi.MD
        # section 2.1), so it falls through rather than being dropped here.
        if "result" in request or "error" in request:
            self._log_dropped("a reply, not a request", sender)
            return

        raw_id = request.get("id", 0)
        wire_id = json_rpc_wire_id(raw_id)
        request_id = 0 if wire_id is None else wire_id
        raw_method = request.get("method", "")
        method = raw_method if isinstance(raw_method, str) else ""
        params = request.get("params", {})
        if not isinstance(params, dict):
            params = {}

        response = self.build_response(request_id, method, params)

        if response:
            self._send_openapi_datagram(response, addr)

        if self.verbose:
            outcome = "replied" if response else "no response"
            print(
                f"[{time.strftime('%H:%M:%S')}] {sender} {method} "
                f"id={raw_id} (wire {request_id}) -> {outcome}"
            )

    def _send_openapi_datagram(self, response: dict[str, Any], addr: tuple[str, int]) -> None:
        """Send a UDP reply, duplicating it on reset-prone Control firmware.

        Pre-150 VNSE3-0 builds send Local API replies on both the FC41D WiFi
        AT+QISEND path and the CH395 Ethernet socket (the v150 OTA note
        "Optimized Local API send anomaly on Ethernet").
        """
        assert self.sock is not None
        response_bytes = json.dumps(response).encode("utf-8")
        self.sock.sendto(response_bytes, self._reply_addr(addr))
        if self.profile.openapi_reset_prone:
            self.sock.sendto(response_bytes, self._reply_addr(addr))

    def _reply_addr(self, addr: tuple[str, int]) -> tuple[str, int]:
        """Choose the UDP destination firmware would use for this sender.

        Real devices reply to the Open API listen port, not an ephemeral
        source port. Loopback unit tests still bind ephemeral, so those
        replies keep using the request's source address.
        """
        sender_ip, _sender_port = addr
        if is_loopback_host(sender_ip):
            return addr
        return (sender_ip, self.port)

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
            "em_input_energy": 0.0,
            "em_output_energy": 0.0,
        }

    def _totals_from_state(self, state: dict[str, Any]) -> dict[str, float]:
        return {
            "total_pv_energy": float(state.get("total_pv_energy", 0.0)),
            "total_grid_output_energy": float(state.get("total_grid_output_energy", 0.0)),
            "total_grid_input_energy": float(state.get("total_grid_input_energy", 0.0)),
            "total_load_energy": float(state.get("total_load_energy", 0.0)),
            "em_input_energy": float(
                state.get("em_input_energy", state.get("total_grid_input_energy", 0.0))
            ),
            "em_output_energy": float(
                state.get("em_output_energy", state.get("total_grid_output_energy", 0.0))
            ),
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

    def set_energy_totals(
        self,
        *,
        total_pv_energy: float = 0,
        total_grid_output_energy: float = 0,
        total_grid_input_energy: float = 0,
        total_load_energy: float = 0,
        em_input_energy: float | None = None,
        em_output_energy: float | None = None,
    ) -> None:
        """Set physical energy totals used by subsequent status responses."""
        totals = {
            "total_pv_energy": total_pv_energy,
            "total_grid_output_energy": total_grid_output_energy,
            "total_grid_input_energy": total_grid_input_energy,
            "total_load_energy": total_load_energy,
        }
        if em_input_energy is not None:
            totals["em_input_energy"] = em_input_energy
        if em_output_energy is not None:
            totals["em_output_energy"] = em_output_energy
        if self.simulate:
            for key, value in totals.items():
                setattr(self.simulator, key, value)
            # The channel accumulator owns total_pv_energy while PV strings
            # are configured; leaving it behind would undo the write on the
            # next tick.
            self.simulator.pv_channels.total_pv_energy = total_pv_energy
        else:
            self._static_totals.update(totals)

    def build_response(
        self, request_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Build the Open API response for a request."""
        src = f"{self.config['device']}-{self.config['ble_mac']}"
        state = self._get_state()

        if method == "Marstek.GetDevice":
            omit_result_macs = (
                self.profile.family is DeviceFamily.VENUS_C and self.profile.hmg50_control
            )
            return handle_get_device(
                request_id,
                src,
                self.config,
                self.ip,
                omit_result_macs=omit_result_macs,
            )

        elif method == "BLE.GetStatus":
            return handle_ble_get_status(request_id, src, self.config, self._ble_connected)

        elif method == "ES.GetStatus":
            # State includes energy stats from simulator
            state_with_capacity = {**state, "capacity_wh": self.simulator.capacity_wh}
            return handle_es_get_status(
                request_id,
                src,
                state_with_capacity,
                self.config.get("device", ""),
                profile=self.profile,
                include_bat_power=self.include_bat_power or reports_es_bat_power(self.profile),
            )

        elif method == "ES.GetMode":
            return handle_es_get_mode(request_id, src, state, profile=self.profile, params=params)

        elif method == "PV.GetStatus":
            if not self.profile.supports_pv:
                return handle_method_not_found(
                    request_id,
                    src,
                    extra_data=pv_method_not_found_extra_data(self.profile),
                )
            pv_channels = state.get("pv_channels") or self.config.get("pv_channels")
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
            return handle_pv_get_status(request_id, src, self.profile, pv_state)

        elif method == "Wifi.GetStatus":
            return handle_wifi_get_status(request_id, src, self.config, self.ip, state)

        elif method == "EM.GetStatus":
            if not self.profile.supports_em_status:
                return handle_method_not_found(request_id, src)
            return handle_em_get_status(request_id, src, state, profile=self.profile)

        elif method == "Bat.GetStatus":
            return handle_bat_get_status(request_id, src, state, self.simulator.capacity_wh)

        elif method == "ES.SetMode":
            config = params.get("config", {})
            mode = config.get("mode", MODE_AUTO)
            if mode == MODE_UPS and not self.profile.supports_ups:
                return handle_method_not_found(request_id, src)
            if mode == MODE_MANUAL:
                manual_config = config.get("manual_cfg", {})
                schedule_slot = (
                    manual_config.get("time_num") if isinstance(manual_config, dict) else None
                )
                if (
                    isinstance(schedule_slot, bool)
                    or not isinstance(schedule_slot, int)
                    or schedule_slot < 0
                    or schedule_slot > self.profile.max_manual_schedule_slot
                ):
                    return handle_invalid_params(request_id, src)

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

        elif method == "Wifi.SetConfig":
            if not supports_wifi_set_config(self.profile):
                return handle_method_not_found(request_id, src)
            return handle_wifi_set_config(request_id, src, params, self.config)

        elif method in {"Set.Ver", "Reset.Factory"}:
            if not supports_set_ver_and_factory_reset(self.profile):
                return handle_method_not_found(request_id, src)
            if method == "Reset.Factory" and params.get("type") == 1:
                self.set_energy_totals()
            return handle_sys_write(request_id, src)

        sys_supported = {
            CMD_DOD_SET: self.profile.supports_sys_dod,
            CMD_BLE_ADV: self.profile.supports_sys_ble_advertising,
            CMD_LED_CTRL: self.profile.supports_sys_led,
        }
        if method in sys_supported:
            if not sys_supported[method]:
                return handle_method_not_found(request_id, src)
            return handle_sys_write(request_id, src)

        # Control firmware replies JSON-RPC -32601 ("unknow method" in
        # HMG-50 / VNSE3-0 strings) instead of dropping the datagram.
        return handle_method_not_found(request_id, src)

    def _build_response(
        self, request_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Compatibility wrapper for older mock callers."""
        return self.build_response(request_id, method, params)
