"""Issue #108: a stale Marstek.GetDevice ip must not replace a live host."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_socket
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from mock_device import MockMarstekDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.discovery import discover_devices, get_device_info

_BLE_MAC = "3C:1A:CC:00:AB:34"
_STALE_IP = "192.168.2.173"
_LIVE_IP = "192.168.3.22"
_GET_DEVICE = {
    "id": 0,
    "src": "VenusE 3.0-3c1acc00ab34",
    "result": {
        "device": "VenusE 3.0",
        "ver": 147,
        "ble_mac": "3c1acc00ab34",
        "wifi_mac": "dab37000ab34",
        "wifi_name": "house",
        "ip": _STALE_IP,
    },
}


@pytest.fixture(autouse=True)
def _real_loopback_sockets() -> None:
    """These tests talk to a real mock on loopback."""
    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)


def test_broadcast_records_the_reply_source_not_the_stale_payload() -> None:
    """The capture in issue #108: answer from .22, payload ip still .173."""
    from custom_components.marstek.discovery import _device_info_from_response

    info = _device_info_from_response(_GET_DEVICE, _LIVE_IP, 30000)

    assert info is not None
    assert info["ip"] == _LIVE_IP
    assert str(info["ble_mac"]).replace(":", "").lower() == "3c1acc00ab34"
    assert info["version"] == 147


async def test_discover_devices_keeps_sender_when_payload_ip_is_stale() -> None:
    """A broadcast reply is stored under the address it was sent from."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    payload = json.dumps(_GET_DEVICE).encode()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(payload, ("127.0.0.1", port))
        devices = await discover_devices(
            timeout=0.5,
            ports=[port],
            broadcast_addresses=["127.0.0.1"],
            shared_sockets={port: sock},
        )
    finally:
        sock.close()

    assert len(devices) == 1
    assert devices[0]["ip"] == "127.0.0.1"
    assert devices[0]["ip"] != _STALE_IP


@contextmanager
def _running_stale_mock() -> Iterator[MockMarstekDevice]:
    """Serve a Venus E whose GetDevice ip is not the address it answers from."""
    device = MockMarstekDevice(
        port=0,
        device_config={
            "device": "VenusE 3.0",
            "ver": 147,
            "ble_mac": "3c1acc00ab34",
            "wifi_mac": "dab37000ab34",
            "wifi_name": "house",
            "stale_ip": _STALE_IP,
        },
        ip_override="127.0.0.1",
        simulate=False,
        verbose=False,
        status_interval=0,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.1)
    device.sock = sock
    device.port = int(sock.getsockname()[1])
    stop = threading.Event()
    methods: list[str] = []
    original = device.build_response

    def _record(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        methods.append(method)
        return original(request_id, method, params)

    device.build_response = _record  # type: ignore[method-assign]
    device.handled_methods = methods  # type: ignore[attr-defined]

    def _serve() -> None:
        while not stop.is_set():
            try:
                device._handle_request()
            except TimeoutError:
                continue
            except OSError:
                if stop.is_set():
                    return

    thread = threading.Thread(target=_serve, name="mock-stale-ip", daemon=True)
    thread.start()
    try:
        yield device
    finally:
        stop.set()
        sock.close()
        thread.join(timeout=2.0)


async def test_unicast_to_a_live_mock_ignores_stale_getdevice_ip() -> None:
    """Query the address that answers; do not record the stale payload ip.

    Wifi.GetStatus on this mock reports the live sta_ip. Discovery must not
    need that call: the reply source is enough, and only GetDevice is sent.
    """
    with _running_stale_mock() as device:
        info = await get_device_info("127.0.0.1", device.port, timeout=2.0)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(2.0)
            probe.sendto(
                json.dumps({"id": 1, "method": "Wifi.GetStatus", "params": {"id": 0}}).encode(),
                ("127.0.0.1", device.port),
            )
            raw, sender = probe.recvfrom(4096)
        wifi = json.loads(raw)
        handled = list(device.handled_methods)  # type: ignore[attr-defined]

    assert info is not None
    assert info["ip"] == "127.0.0.1"
    assert sender[0] == "127.0.0.1"
    assert wifi["result"]["sta_ip"] == "127.0.0.1"
    assert "Marstek.GetDevice" in handled
    assert handled.count("Wifi.GetStatus") == 1


def _entry(host: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="3c:1a:cc:00:ab:34",
        data={
            CONF_HOST: host,
            "port": 30000,
            "ble_mac": _BLE_MAC,
            "mac": _BLE_MAC,
            "device_type": "VenusE 3.0",
            "version": 147,
            "wifi_name": "house",
            "wifi_mac": "DA:B3:70:00:AB:34",
        },
    )


def _mark_answering(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    success: bool,
    failures: int,
) -> None:
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(
            last_update_success=success,
            consecutive_failures=failures,
        )
    )


async def test_scanner_flow_keeps_a_host_that_is_still_answering(
    hass: HomeAssistant,
) -> None:
    """Reconfigure to the live address, then a scan offers the stale one."""
    entry = _entry(_LIVE_IP)
    entry.add_to_hass(hass)
    _mark_answering(hass, entry, success=True, failures=0)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data={
            "ip": _STALE_IP,
            "ble_mac": _BLE_MAC,
            "mac": _BLE_MAC,
            "device_type": "VenusE 3.0",
            "version": 147,
            "wifi_name": "renamed",
            "wifi_mac": "DA:B3:70:00:AB:34",
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.data[CONF_HOST] == _LIVE_IP
    assert updated.data["wifi_name"] == "renamed"


async def test_scanner_flow_still_follows_a_host_that_stopped_answering(
    hass: HomeAssistant,
) -> None:
    """A real move updates the entry once polls to the stored address fail."""
    entry = _entry(_STALE_IP)
    entry.add_to_hass(hass)
    _mark_answering(hass, entry, success=False, failures=3)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data={
            "ip": _LIVE_IP,
            "ble_mac": _BLE_MAC,
            "mac": _BLE_MAC,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.data[CONF_HOST] == _LIVE_IP


async def test_scanner_flow_updates_when_the_last_poll_failed(
    hass: HomeAssistant,
) -> None:
    """Zero failure count with a failed update is not a healthy host."""
    entry = _entry(_STALE_IP)
    entry.add_to_hass(hass)
    _mark_answering(hass, entry, success=False, failures=0)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data={
            "ip": _LIVE_IP,
            "ble_mac": _BLE_MAC,
            "mac": _BLE_MAC,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data[CONF_HOST] == _LIVE_IP


async def test_loaded_entry_without_coordinator_still_accepts_a_new_host(
    hass: HomeAssistant,
) -> None:
    """No poll history is not evidence that the stored host is answering."""
    entry = _entry(_STALE_IP)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data={
            "ip": _LIVE_IP,
            "ble_mac": _BLE_MAC,
            "mac": _BLE_MAC,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data[CONF_HOST] == _LIVE_IP


async def test_dhcp_lease_updates_a_host_that_is_still_answering(
    hass: HomeAssistant,
) -> None:
    """A DHCP lease is the address the device requested, not a GetDevice field."""
    entry = _entry(_STALE_IP)
    entry.add_to_hass(hass)
    _mark_answering(hass, entry, success=True, failures=0)

    discovery_info = type(
        "DhcpInfo",
        (),
        {"ip": _LIVE_IP, "hostname": "venus", "macaddress": "3c1acc00ab34"},
    )()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "dhcp"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data[CONF_HOST] == _LIVE_IP
