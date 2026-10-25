"""The mock battery's smart plug: counters, JSON shapes and the HTTP server."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest
from mock_device import BatterySimulator
from mock_device.plug_server import PlugServer, homewizard_data, shelly_switch_status
from mock_device.simulators.plug import PlugMeter, PlugReading

_READING = PlugReading(power_w=-460, import_wh=1_234.5, export_wh=987.6)


def test_meter_splits_net_power_into_import_and_export() -> None:
    meter = PlugMeter()
    meter.accumulate(8.0, 2.0)
    meter.accumulate(-500.0, 0.5)
    assert meter.reading(-500) == PlugReading(-500, 16.0, 250.0)
    restored = PlugMeter()
    restored.apply_state({**meter.to_state(), "unrelated": 1})
    assert restored.to_state() == meter.to_state()
    restored.apply_state({})
    assert restored.to_state() == meter.to_state()


def test_homewizard_and_shelly_payloads_use_their_units() -> None:
    homewizard = homewizard_data(_READING, rssi=-60)
    assert homewizard["total_power_import_kwh"] == 1.234
    assert homewizard["total_power_export_kwh"] == 0.988
    assert homewizard["active_power_w"] == -460
    assert homewizard["wifi_strength"] == 80
    shelly = shelly_switch_status(_READING)
    assert shelly["apower"] == -460.0
    assert shelly["aenergy"] == {"total": 1_234.5}
    assert shelly["ret_aenergy"] == {"total": 987.6}


def _server() -> PlugServer:
    return PlugServer(0, "aabbccddeeff", lambda: _READING, lambda: -60)


def test_routes_answer_the_two_clients_and_404_the_rest() -> None:
    server = _server()
    assert server.respond("GET", "/api", None)["product_type"] == "HWE-SKT"
    assert server.respond("GET", "/api/v1/data", None)["active_power_w"] == -460
    assert server.respond("GET", "/rpc/Shelly.GetDeviceInfo", None)["mac"] == "AABBCCDDEEFF"
    assert server.respond("GET", "/rpc/Switch.GetStatus", None)["output"] is True
    assert server.respond("GET", "/api/v2/data", None) is None


def test_a_put_is_stored_but_the_locked_switch_keeps_power() -> None:
    server = _server()
    state = server.respond("PUT", "/api/v1/state", {"power_on": False, "brightness": 10})
    assert state == {"power_on": False, "switch_lock": True, "brightness": 10}
    assert server.respond("PUT", "/api/v1/system", {"cloud_enabled": True}) == {
        "cloud_enabled": True
    }
    assert server.respond("PUT", "/api/v1/state", None)["brightness"] == 10
    assert server.respond("GET", "/api/v1/data", None)["active_power_w"] == -460


def _http(url: str, method: str = "GET", body: bytes | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_the_server_speaks_http() -> None:
    server = _server()
    server.start()
    try:
        assert server._server is not None
        base = f"http://127.0.0.1:{server._server.server_address[1]}"
        assert _http(f"{base}/api/v1/data?x=1")[1]["total_power_import_kwh"] == 1.234
        assert _http(f"{base}/api/v1/state", "PUT", b"not json")[0] == 200
        assert _http(f"{base}/nope") == (404, {"error": "not found"})
    finally:
        server.stop()
    _server().stop()


def test_the_battery_feeds_the_plug_with_its_standby_draw() -> None:
    sim = BatterySimulator(initial_soc=50)
    sim.standby_power = 8
    sim.ongrid_power = 0
    sim._update_energy_stats(3_600)
    reading = sim.get_plug_reading()
    assert reading.power_w == pytest.approx(8, abs=1)
    assert reading.import_wh == pytest.approx(8.0)
    state = sim.get_persistent_state()
    assert state["plug_import_energy"] == pytest.approx(8.0)
    fresh = BatterySimulator(initial_soc=50)
    fresh.apply_persistent_state(state)
    assert fresh.plug.to_state() == sim.plug.to_state()
