"""Tests for the per-port UDP client pool."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.udp_clients import (
    acquire_udp_client_lease,
    async_paused_udp_receivers,
    async_release_udp_client_for_entry,
    bind_port_for_host,
    configured_device_port,
    domain_has_udp_leases,
    entry_bind_port,
    get_udp_client,
    get_udp_client_for_entry,
    iter_udp_clients,
    store_udp_client,
    transfer_reset_prone_mark_for_entry,
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
    lan = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.50", "port": 30002})
    loopback = MockConfigEntry(domain=DOMAIN, data={"host": "127.0.0.1", "port": 30002})
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


async def test_paused_udp_receivers_context_resumes_after_error(
    hass: HomeAssistant,
) -> None:
    """Broadcast discovery must resume listeners even when a scan fails."""
    client = MagicMock()
    client.async_pause_receiver = AsyncMock()
    client.async_resume_receiver = AsyncMock()
    store_udp_client(hass, 30000, client)

    with pytest.raises(RuntimeError, match="probe failed"):
        async with async_paused_udp_receivers(hass):
            raise RuntimeError("probe failed")

    client.async_pause_receiver.assert_awaited_once()
    client.async_resume_receiver.assert_awaited_once()


async def test_release_uses_runtime_client_bind_port(
    hass: HomeAssistant,
) -> None:
    """Unload after a host/port change closes the socket that was actually used."""
    runtime_client = MagicMock()
    runtime_client.bind_port = 30000
    runtime_client.async_cleanup = AsyncMock()
    leftover_client = MagicMock()
    leftover_client.async_cleanup = AsyncMock()
    store_udp_client(hass, 30000, runtime_client)
    store_udp_client(hass, 30001, leftover_client)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.50", "port": 30001},
    )
    entry.runtime_data = MagicMock(coordinator=MagicMock(udp_client=runtime_client))
    entry.add_to_hass(hass)

    await async_release_udp_client_for_entry(hass, entry)

    runtime_client.async_cleanup.assert_awaited_once()
    leftover_client.async_cleanup.assert_not_called()
    assert 30000 not in udp_client_pool(hass)
    assert udp_client_pool(hass)[30001] is leftover_client


async def test_paused_udp_receivers_serialize_concurrent_scans(
    hass: HomeAssistant,
) -> None:
    """Scanner and config-flow broadcasts must not overlap SO_REUSEPORT binds."""
    order: list[str] = []
    client = MagicMock()

    async def pause() -> None:
        order.append("pause")

    async def resume() -> None:
        order.append("resume")

    client.async_pause_receiver = pause
    client.async_resume_receiver = resume
    store_udp_client(hass, 30000, client)

    async def scan(label: str) -> None:
        async with async_paused_udp_receivers(hass):
            order.append(f"discover-{label}")
            await asyncio.sleep(0.02)

    await asyncio.gather(scan("a"), scan("b"))

    assert order in (
        ["pause", "discover-a", "resume", "pause", "discover-b", "resume"],
        ["pause", "discover-b", "resume", "pause", "discover-a", "resume"],
    )


async def test_pause_cancelled_resumes_already_paused_clients(
    hass: HomeAssistant,
) -> None:
    """Cancellation during pause must not leave a listener permanently paused."""
    client_a = MagicMock()
    client_b = MagicMock()
    started = asyncio.Event()
    client_a.async_pause_receiver = AsyncMock()
    client_a.async_resume_receiver = AsyncMock()
    client_b.async_resume_receiver = AsyncMock()

    async def pause_b() -> None:
        started.set()
        await asyncio.sleep(30)

    client_b.async_pause_receiver = pause_b
    store_udp_client(hass, 30000, client_a)
    store_udp_client(hass, 30001, client_b)

    async def run() -> None:
        async with async_paused_udp_receivers(hass):
            pass

    task = hass.async_create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    client_a.async_resume_receiver.assert_awaited()


async def test_transfer_reset_prone_mark_clears_old_client_on_port_change(
    hass: HomeAssistant,
) -> None:
    """A listen-port change must not leave the new IP marked on the old socket."""
    old_client = MagicMock()
    new_client = MagicMock()
    old_client.is_openapi_reset_prone = MagicMock(return_value=True)
    store_udp_client(hass, 30000, old_client)
    store_udp_client(hass, 30003, new_client)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.50", "port": 30000},
    )
    entry.add_to_hass(hass)

    transfer_reset_prone_mark_for_entry(hass, entry, "192.168.1.50", "192.168.1.51", new_port=30003)

    old_client.clear_openapi_reset_prone.assert_called_once_with(
        "192.168.1.50", owner=entry.entry_id
    )
    old_client.transfer_openapi_reset_prone.assert_not_called()
    new_client.set_openapi_reset_prone.assert_called_once_with(
        "192.168.1.51", True, owner=entry.entry_id
    )


async def test_release_keeps_shared_socket_while_retry_entry_holds_lease(
    hass: HomeAssistant,
) -> None:
    """SETUP_RETRY entries keep the pooled socket after another entry unloads."""
    client = MagicMock()
    client.bind_port = 30000
    client.async_cleanup = AsyncMock()
    store_udp_client(hass, 30000, client)

    loaded = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={"host": "192.168.1.50", "port": 30000},
    )
    retry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={"host": "192.168.1.51", "port": 30000},
    )
    loaded.add_to_hass(hass)
    retry.add_to_hass(hass)
    loaded.mock_state(hass, ConfigEntryState.LOADED)
    retry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    acquire_udp_client_lease(hass, loaded.entry_id, 30000)
    acquire_udp_client_lease(hass, retry.entry_id, 30000)

    await async_release_udp_client_for_entry(hass, loaded)

    client.async_cleanup.assert_not_called()
    assert domain_has_udp_leases(hass)
    assert udp_client_pool(hass)[30000] is client


async def test_setup_retry_port_change_closes_unused_socket(
    hass: HomeAssistant,
) -> None:
    """SETUP_RETRY host/port migration must close the unused previous socket."""
    old_client = MagicMock()
    old_client.async_cleanup = AsyncMock()
    new_client = MagicMock()
    store_udp_client(hass, 30000, old_client)
    store_udp_client(hass, 30003, new_client)

    retry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={"host": "192.168.1.51", "port": 30003},
    )
    retry.add_to_hass(hass)
    retry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    acquire_udp_client_lease(hass, retry.entry_id, 30000)

    stale = acquire_udp_client_lease(hass, retry.entry_id, 30003)

    assert stale is old_client
    assert 30000 not in udp_client_pool(hass)
    assert udp_client_pool(hass)[30003] is new_client
