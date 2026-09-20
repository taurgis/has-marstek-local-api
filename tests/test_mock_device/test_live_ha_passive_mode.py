"""Live Home Assistant tests against a UDP mock Venus E 3.0 device.

These tests bind a real ``MockMarstekDevice`` and use the integration's UDP
client (not a MagicMock) so issue #34 can be reproduced end-to-end.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
import pytest_socket
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from mock_device import MODE_PASSIVE as MOCK_MODE_PASSIVE
from mock_device import MockMarstekDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_POLL_INTERVAL_FAST,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    DOMAIN,
    MODE_PASSIVE,
)
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from custom_components.marstek.services import (
    ATTR_DEVICE_ID,
    ATTR_DURATION,
    ATTR_POWER,
    SERVICE_SET_PASSIVE_MODE,
)
from tests.conftest import create_mock_scanner

_BLE_MAC = "02feedface34"
_WIFI_MAC = "02cafebabe34"
_ISSUE_POWER = -500
_ISSUE_DURATION = 300


@contextmanager
def running_udp_mock_device(
    *,
    device_config: dict[str, Any] | None = None,
    initial_soc: int = 50,
) -> Iterator[MockMarstekDevice]:
    """Bind a mock Marstek on an ephemeral UDP port and serve in a thread."""
    pytest_socket.enable_socket()
    device = MockMarstekDevice(
        port=0,
        device_config=device_config,
        ip_override="127.0.0.1",
        initial_soc=initial_soc,
        simulate=True,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.1)
    device.sock = sock
    device.port = int(sock.getsockname()[1])

    stop = threading.Event()

    def _serve() -> None:
        while not stop.is_set():
            try:
                device._handle_request()
            except TimeoutError:
                continue
            except OSError:
                if stop.is_set():
                    return

    thread = threading.Thread(target=_serve, name="mock-marstek-venus-e-150", daemon=True)
    thread.start()
    try:
        yield device
    finally:
        stop.set()
        sock.close()
        thread.join(timeout=2.0)


def _venus_e_150_config() -> dict[str, Any]:
    return {
        "device": "VenusE 3.0",
        "ver": 150,
        "ble_mac": _BLE_MAC,
        "wifi_mac": _WIFI_MAC,
        "wifi_name": "MockNetwork",
    }


def _config_entry_for_mock(port: int) -> MockConfigEntry:
    ble_mac = format_mac(_BLE_MAC)
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ble_mac,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            "ble_mac": ble_mac,
            "mac": ble_mac,
            "device_type": "VenusE 3.0",
            "version": 150,
            "wifi_name": "MockNetwork",
            "wifi_mac": format_mac(_WIFI_MAC),
        },
        options={
            CONF_PARALLEL_API_REQUESTS: True,
            CONF_POLL_INTERVAL_FAST: 3600,
            CONF_REQUEST_DELAY: 0,
        },
    )


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_set_passive_mode_live_venus_e_150_truncated_device_id(
    hass: HomeAssistant,
) -> None:
    """Issue #34: truncated HA device IDs still set Passive on firmware 150.

    pytest-homeassistant-custom-component disables INET sockets during
    setup. Re-enable them here so the UDP mock and MarstekUDPClient can
    talk on 127.0.0.1.
    """
    pytest_socket.enable_socket()
    with (
        running_udp_mock_device(device_config=_venus_e_150_config()) as mock,
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=create_mock_scanner(),
        ),
    ):
        assert mock.simulator.get_state()["mode"] != MOCK_MODE_PASSIVE

        entry = _config_entry_for_mock(mock.port)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED

        device_registry = dr.async_get(hass)
        device = async_lookup_device_by_identifier(device_registry, (DOMAIN, format_mac(_BLE_MAC)))
        assert device is not None
        assert len(device.id) == 32

        truncated_id = device.id[:-1]
        assert len(truncated_id) == 31

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            {
                ATTR_DEVICE_ID: truncated_id,
                ATTR_POWER: _ISSUE_POWER,
                ATTR_DURATION: _ISSUE_DURATION,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        state = mock.simulator.get_state()
        assert state["mode"] == MOCK_MODE_PASSIVE
        assert state["passive_remaining"] > 0
        assert entry.runtime_data.coordinator.data.get("device_mode") == MODE_PASSIVE


@contextmanager
def _silenceable_udp_mock_device(
    *, device_config: dict[str, Any] | None = None
) -> Iterator[tuple[MockMarstekDevice, threading.Event]]:
    """Serve a mock Marstek that can be told to stop answering.

    Silence drains the request without replying, which is what an unplugged
    or renumbered device looks like on the wire: the socket stays bound, so
    the client times out rather than getting a port-unreachable error.
    """
    pytest_socket.enable_socket()
    device = MockMarstekDevice(
        port=0,
        device_config=device_config,
        ip_override="127.0.0.1",
        initial_soc=50,
        simulate=True,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.1)
    device.sock = sock
    device.port = int(sock.getsockname()[1])

    stop = threading.Event()
    silent = threading.Event()

    def _serve() -> None:
        while not stop.is_set():
            try:
                if silent.is_set():
                    sock.recvfrom(4096)
                    continue
                device._handle_request()
            except TimeoutError:
                continue
            except OSError:
                if stop.is_set():
                    return

    thread = threading.Thread(target=_serve, name="mock-marstek-silenceable", daemon=True)
    thread.start()
    try:
        yield device, silent
    finally:
        stop.set()
        sock.close()
        thread.join(timeout=2.0)


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_live_device_going_quiet_logs_once_and_recovers(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Drive a real outage over UDP: quiet device, unavailable, then back.

    Exercises the whole chain against a live device rather than a mock
    client: one warning per outage, one repair issue, entities unavailable,
    and a single recovery line when the device answers again.
    """
    pytest_socket.enable_socket()
    with (
        _silenceable_udp_mock_device(device_config=_venus_e_150_config()) as (
            mock,
            silent,
        ),
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=create_mock_scanner(),
        ),
    ):
        entry = _config_entry_for_mock(mock.port)
        entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_REQUEST_TIMEOUT: 1, CONF_FAILURE_THRESHOLD: 3},
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data.coordinator
        assert coordinator.data.get("battery_soc") is not None

        entity_registry = er.async_get(hass)
        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{format_mac(_BLE_MAC)}_battery_soc"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id).state not in (None, "unavailable")

        issue_registry = ir.async_get(hass)
        issue_id = f"cannot_connect_{entry.entry_id}"

        with caplog.at_level(logging.DEBUG, logger="custom_components.marstek.coordinator"):
            silent.set()
            # Let the serving thread finish the receive it is already in, so
            # the first poll after this cannot still be answered.
            await asyncio.sleep(0.3)
            for _ in range(3):
                await coordinator.async_refresh()
            await hass.async_block_till_done()

            assert coordinator.last_update_success is False
            assert hass.states.get(entity_id).state == "unavailable"
            assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
            assert (
                len(
                    [
                        record
                        for record in caplog.records
                        if record.levelno == logging.WARNING
                        and "status request failed" in record.getMessage()
                    ]
                )
                == 1
            )

            caplog.clear()
            silent.clear()
            await coordinator.async_refresh()
            await hass.async_block_till_done()

        assert coordinator.last_update_success is True
        assert hass.states.get(entity_id).state not in (None, "unavailable")
        assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
        assert (
            len(
                [
                    record
                    for record in caplog.records
                    if record.levelno == logging.INFO and "answering again" in record.getMessage()
                ]
            )
            == 1
        )
