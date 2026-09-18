"""Tests for the per-port UDP client pool."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DATA_UDP_CLIENTS, DOMAIN
from custom_components.marstek.helpers.udp_clients import (
    bind_port_for_host,
    configured_device_port,
    entry_bind_port,
    get_udp_client,
    get_udp_client_for_entry,
    iter_udp_clients,
    store_udp_client,
    udp_client_pool,
)


def test_bind_port_for_host_uses_device_port_on_lan() -> None:
    """LAN devices bind the configured Open API listen port."""
    assert bind_port_for_host("192.168.1.50", 30003) == 30003
    assert bind_port_for_host("10.0.0.8", 30000) == 30000


def test_bind_port_for_host_uses_ephemeral_on_loopback() -> None:
    """Loopback devices bind ephemeral so they do not collide with a local mock."""
    assert bind_port_for_host("127.0.0.1", 30000) == 0
    assert bind_port_for_host("localhost", 30001) == 0
    assert bind_port_for_host("::1", 30000) == 0


def test_configured_device_port_defaults_and_rejects_invalid() -> None:
    """Config entries fall back to 30000 when the stored port is unusable."""
    valid = MockConfigEntry(domain=DOMAIN, data={"port": 30003})
    missing = MockConfigEntry(domain=DOMAIN, data={})
    invalid = MockConfigEntry(domain=DOMAIN, data={"port": "abc"})
    out_of_range = MockConfigEntry(domain=DOMAIN, data={"port": 70000})

    assert configured_device_port(valid) == 30003
    assert configured_device_port(missing) == 30000
    assert configured_device_port(invalid) == 30000
    assert configured_device_port(out_of_range) == 30000


def test_entry_bind_port_follows_host_and_port() -> None:
    """Pool keys combine loopback detection with the configured listen port."""
    lan = MockConfigEntry(
        domain=DOMAIN, data={"host": "192.168.1.50", "port": 30002}
    )
    loopback = MockConfigEntry(
        domain=DOMAIN, data={"host": "127.0.0.1", "port": 30002}
    )
    assert entry_bind_port(lan) == 30002
    assert entry_bind_port(loopback) == 0


async def test_pool_stores_and_lists_clients_by_bind_port(
    hass: HomeAssistant,
) -> None:
    """The pool is keyed by bind port so mixed custom ports stay independent."""
    client_a = MagicMock(name="client-30000")
    client_b = MagicMock(name="client-30003")
    store_udp_client(hass, 30000, client_a)
    store_udp_client(hass, 30003, client_b)

    assert get_udp_client(hass, 30000) is client_a
    assert get_udp_client(hass, 30003) is client_b
    assert set(udp_client_pool(hass)) == {30000, 30003}
    assert set(iter_udp_clients(hass)) == {client_a, client_b}


async def test_get_udp_client_for_entry_prefers_coordinator(
    hass: HomeAssistant,
) -> None:
    """Control paths use the coordinator socket created for that entry."""
    coordinator_client = MagicMock(name="coordinator-client")
    pooled_client = MagicMock(name="pooled-client")
    store_udp_client(hass, 30000, pooled_client)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.50", "port": 30000},
    )
    entry.runtime_data = MagicMock(coordinator=MagicMock(udp_client=coordinator_client))

    assert get_udp_client_for_entry(hass, entry) is coordinator_client


async def test_get_udp_client_for_entry_falls_back_to_pool(
    hass: HomeAssistant,
) -> None:
    """Entries without runtime data still resolve the pooled bind-port client."""
    pooled_client = MagicMock(name="pooled-client")
    store_udp_client(hass, 30001, pooled_client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.50", "port": 30001},
    )

    assert get_udp_client_for_entry(hass, entry) is pooled_client
