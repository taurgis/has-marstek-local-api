"""HTTP face of the battery's smart plug.

One port serves two plugs over the same counters:

* a HomeWizard Energy Socket (HWE-SKT) on its local API v1, which the
  Home Assistant core ``homewizard`` integration polls every 5 seconds;
* a Shelly-style ``/rpc/Switch.GetStatus`` in watt-hours, which the dev-only
  ``mock_energy_plug`` custom integration reads as a third-party plug.

Only what those two clients read is served. The HomeWizard switch is locked,
as it should be on a battery: a PUT is stored and echoed but never cuts power.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .simulators.plug import PlugReading

MAINS_VOLTAGE = 230.0


def homewizard_data(reading: PlugReading, rssi: int) -> dict[str, Any]:
    """Return ``/api/v1/data`` as an Energy Socket reports it (kWh, 3 decimals)."""
    current = abs(reading.power_w) / MAINS_VOLTAGE
    return {
        "wifi_ssid": "marstek-dev",
        "wifi_strength": max(0, min(100, 2 * (rssi + 100))),
        "total_power_import_kwh": round(reading.import_wh / 1000, 3),
        "total_power_import_t1_kwh": round(reading.import_wh / 1000, 3),
        "total_power_export_kwh": round(reading.export_wh / 1000, 3),
        "total_power_export_t1_kwh": round(reading.export_wh / 1000, 3),
        "active_power_w": reading.power_w,
        "active_power_l1_w": reading.power_w,
        "active_voltage_v": MAINS_VOLTAGE,
        "active_current_a": round(current, 3),
        "active_frequency_hz": 50.0,
    }


def shelly_switch_status(reading: PlugReading) -> dict[str, Any]:
    """Return ``Switch.GetStatus`` as a Shelly plug reports it (Wh)."""
    return {
        "id": 0,
        "source": "init",
        "output": True,
        "apower": float(reading.power_w),
        "voltage": MAINS_VOLTAGE,
        "current": round(abs(reading.power_w) / MAINS_VOLTAGE, 3),
        "aenergy": {"total": round(reading.import_wh, 3)},
        "ret_aenergy": {"total": round(reading.export_wh, 3)},
        "temperature": {"tC": 31.5},
    }


class PlugServer:
    """Serve the plug from a daemon thread."""

    def __init__(
        self,
        port: int,
        serial: str,
        read: Callable[[], PlugReading],
        rssi: Callable[[], int],
    ) -> None:
        self.port = port
        self.serial = serial
        self._read = read
        self._rssi = rssi
        self._state: dict[str, Any] = {"power_on": True, "switch_lock": True, "brightness": 255}
        self._system: dict[str, Any] = {"cloud_enabled": False}
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        """Bind the port and serve until the process exits."""
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        """Stop serving."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def respond(self, method: str, path: str, body: object) -> dict[str, Any] | None:
        """Return the JSON for a request, or None for 404."""
        if method == "PUT" and isinstance(body, dict):
            if path == "/api/v1/state":
                self._state.update(body)
            elif path == "/api/v1/system":
                self._system.update(body)
        routes: dict[str, Callable[[], dict[str, Any]]] = {
            "/api": self._device,
            "/api/v1/data": lambda: homewizard_data(self._read(), self._rssi()),
            "/api/v1/state": lambda: dict(self._state),
            "/api/v1/system": lambda: dict(self._system),
            "/rpc/Shelly.GetDeviceInfo": self._shelly_device,
            "/rpc/Switch.GetStatus": lambda: shelly_switch_status(self._read()),
        }
        route = routes.get(path)
        return None if route is None else route()

    def _device(self) -> dict[str, Any]:
        return {
            "product_type": "HWE-SKT",
            "product_name": "Energy Socket",
            "serial": self.serial,
            "firmware_version": "4.19",
            "api_version": "v1",
        }

    def _shelly_device(self) -> dict[str, Any]:
        return {
            "id": f"mockplug-{self.serial}",
            "mac": self.serial.upper(),
            "model": "MOCK-PLUG",
            "gen": 2,
            "ver": "1.0.0",
            "app": "MockPlug",
        }

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._reply(server.respond("GET", urlsplit(self.path).path, None))

            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"null")
                except ValueError:
                    body = None
                self._reply(server.respond("PUT", urlsplit(self.path).path, body))

            def _reply(self, payload: dict[str, Any] | None) -> None:
                data = json.dumps(payload if payload is not None else {"error": "not found"})
                self.send_response(200 if payload is not None else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data.encode())

            def log_message(self, format: str, *args: Any) -> None:
                """Keep the 5-second polls out of the device log."""

        return Handler
