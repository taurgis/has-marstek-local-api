"""Serialization and ownership of reset-prone device IPs."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.command_builder import get_battery_status
from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
)
from custom_components.marstek.pymarstek.validators import MAX_JSON_RPC_ID, ValidationError

from ._helpers import (
    _patch_sock_sendto,
)


class TestResetProneRequestLock:
    """Tests for per-IP unicast serialization on reset-prone firmware."""

    async def test_device_io_lock_is_shared_per_ip(self) -> None:
        """The same IP reuses one lock; different IPs do not."""
        client = MarstekUDPClient()
        lock1 = await client._throttle.io_lock("192.168.1.100")
        lock2 = await client._throttle.io_lock("192.168.1.100")
        lock3 = await client._throttle.io_lock("192.168.1.101")
        assert lock1 is lock2
        assert lock1 is not lock3

    async def test_reset_prone_requests_do_not_overlap(self) -> None:
        """Two unicast requests to a reset-prone IP are serialized."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        client.set_openapi_reset_prone("192.168.1.100", True)

        inflight = 0
        peak = 0

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            await asyncio.sleep(0.05)
            inflight -= 1

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            await asyncio.gather(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                ),
                client.send_request(
                    '{"id": 2, "method": "ES.GetMode", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                ),
            )

        assert peak == 1

    async def test_unmarked_ips_may_overlap(self) -> None:
        """Firmware 150+ is not forced through the reset-prone lock."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        inflight = 0
        peak = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            started.set()
            await release.wait()
            inflight -= 1

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            first = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            started.clear()
            second = asyncio.create_task(
                client.send_request(
                    '{"id": 2, "method": "ES.GetMode", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            assert peak == 2
            release.set()
            await asyncio.gather(first, second)

    async def test_send_request_skip_validation_missing_id(self) -> None:
        """Test send_request raises ValueError for missing id when validation skipped."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Message without id field
        message = '{"method": "ES.GetStatus", "params": {}}'

        with pytest.raises(ValueError, match="missing id"):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                validate=False,
            )

    async def test_bat_get_status_blocked_on_reset_prone_ip(self) -> None:
        """UDP client refuses Bat.GetStatus to a marked reset-prone IP."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client.set_openapi_reset_prone("192.168.1.100", True)

        with _patch_sock_sendto() as mock_sendto:
            with pytest.raises(ValidationError, match=r"Bat\.GetStatus"):
                await client.send_request(
                    '{"id": 1, "method": "Bat.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    validate=False,
                )
            mock_sendto.assert_not_called()

    async def test_pause_receiver_waits_for_inflight_unicast(self) -> None:
        """Discovery does not stop the listener while a unicast is in flight."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = client._loop.create_task(asyncio.sleep(3600))

        entered = asyncio.Event()
        release_send = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await release_send.wait()
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            request_task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await entered.wait()
            pause_task = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            assert not pause_task.done()
            release_send.set()
            await request_task
            await pause_task
        assert client._listen_task is None

    async def test_nested_pause_waits_until_listener_stopped(self) -> None:
        """A second pause does not return until the first pause stopped the listener."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = client._loop.create_task(asyncio.sleep(3600))

        entered = asyncio.Event()
        release_send = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await release_send.wait()
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            request_task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await entered.wait()
            first_pause = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            second_pause = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            assert not first_pause.done()
            assert not second_pause.done()
            release_send.set()
            await request_task
            await first_pause
            await second_pause
        assert client._listen_task is None
        client._socket = None
        await client.async_resume_receiver()
        await client.async_resume_receiver()

    async def test_pause_receiver_cancellation_does_not_block_unicast(self) -> None:
        """Cancelling a pause while draining must resume unicast traffic."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = client._loop.create_task(asyncio.sleep(3600))
        entered = asyncio.Event()
        release_send = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await release_send.wait()
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            request_task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await entered.wait()
            pause_task = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            pause_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pause_task
            release_send.set()
            await request_task
        assert client._receiver_pause_count == 0
        if client._listen_task is not None and not client._listen_task.done():
            client._listen_task.cancel()
            with suppress(asyncio.CancelledError, ValueError):
                await client._listen_task
        client._listen_task = None
        client._socket = None

    async def test_pending_requests_are_keyed_by_ip(self) -> None:
        """Two devices may share a JSON-RPC id on one socket."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_send(*args: Any, **kwargs: Any) -> None:
            started.set()
            await release.wait()

        with patch.object(client, "_send_udp_message", side_effect=hold_send):
            first = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            started.clear()
            second = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.101",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            assert ("192.168.1.100", 1) in client._router.pending
            assert ("192.168.1.101", 1) in client._router.pending
            for future in client._router.pending.values():
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            release.set()
            await asyncio.gather(first, second)

    def test_duplicate_reply_does_not_steal_other_device_future(self) -> None:
        """A duplicate reply from device A must not complete device B's request."""
        client = MarstekUDPClient()
        _, future_a = client._router.track(0, device_ip="192.168.1.10")
        _, future_b = client._router.track(0, device_ip="192.168.1.20")

        popped_a = client._router.pop_waiter(0, source_ip="192.168.1.10")
        assert popped_a is future_a
        duplicate = client._router.pop_waiter(0, source_ip="192.168.1.10")
        assert duplicate is None
        assert client._router.pending[("192.168.1.20", 0)] is future_b

    async def test_hostname_pending_key_uses_resolved_ip(self) -> None:
        """Replies are sourced from the resolved IPv4, not the hostname."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_send(*args: Any, **kwargs: Any) -> None:
            started.set()
            await release.wait()

        client._resolve_unicast_ip = AsyncMock(return_value="192.168.1.50")
        with patch.object(client, "_send_udp_message", side_effect=hold_send):
            task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "device.local",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            assert ("192.168.1.50", 1) in client._router.pending
            future = client._router.pending[("192.168.1.50", 1)]
            future.set_result({"id": 1, "result": {}})
            release.set()
            await task

    async def test_reset_prone_blocks_bat_get_status(self) -> None:
        """Bat.GetStatus must not reach reset-prone Control firmware."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        client.set_openapi_reset_prone("192.168.1.100", True)
        with patch.object(client, "_send_udp_message", AsyncMock()) as mock_send:
            with pytest.raises(ValidationError, match="blocked on reset-prone"):
                await client.send_request(
                    get_battery_status(0),
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                )
        mock_send.assert_not_called()

    async def test_duplicate_pending_id_for_same_ip_is_rejected(self) -> None:
        """A second overlapping request with the same id to one IP is rejected."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_send(*args: Any, **kwargs: Any) -> None:
            started.set()
            await release.wait()

        with patch.object(client, "_send_udp_message", side_effect=hold_send):
            first = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            with pytest.raises(ValueError, match="Duplicate pending"):
                await client.send_request(
                    '{"id": 1, "method": "ES.GetMode", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            release.set()
            await first

    async def test_broadcast_rewrites_oversized_non_discovery_id(self) -> None:
        """validate=False broadcasts still send a uint16 JSON-RPC id."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        message = json.dumps({"id": 70000, "method": "ES.GetStatus", "params": {"id": 0}})
        with (
            patch.object(client, "_send_udp_message", AsyncMock()) as mock_send,
            patch.object(client, "_get_broadcast_addresses", return_value=["255.255.255.255"]),
        ):
            await client.send_broadcast_request(message, timeout=0, validate=False)

        sent = json.loads(mock_send.call_args.args[0])
        assert sent["id"] == (70000 & MAX_JSON_RPC_ID)
        assert sent["id"] != 0


class TestResetProneOwners:
    """Reset-prone marks are owned by config entry, not only by IP."""

    def test_owner_refcount_and_transfer(self) -> None:
        """Clearing one owner must not drop another device sharing the IP."""
        client = MarstekUDPClient()
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-b")
        client.set_openapi_reset_prone("1.2.3.4", False, owner="entry-a")
        assert client.is_openapi_reset_prone("1.2.3.4")

        client.transfer_openapi_reset_prone("1.2.3.4", "5.6.7.8", owner="entry-b")
        assert not client.is_openapi_reset_prone("1.2.3.4")
        assert client.is_openapi_reset_prone("5.6.7.8")

        client.clear_openapi_reset_prone("5.6.7.8", owner="entry-b")
        assert not client.is_openapi_reset_prone("5.6.7.8")

    def test_clear_owner_drops_marks_across_ips(self) -> None:
        """Unload must drop every IP this config entry marked."""
        client = MarstekUDPClient()
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        client.set_openapi_reset_prone("5.6.7.8", True, owner="entry-a")
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-b")
        client.clear_openapi_reset_prone_owner("entry-a")
        assert not client.is_openapi_reset_prone("5.6.7.8")
        assert client.is_openapi_reset_prone("1.2.3.4")
        assert client.is_openapi_reset_prone("1.2.3.4", owner="entry-b")

    def test_clear_without_owner_drops_all_marks(self) -> None:
        """Unload of the last client may drop every owner for an IP."""
        client = MarstekUDPClient()
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        client.clear_openapi_reset_prone("1.2.3.4")
        assert not client.is_openapi_reset_prone("1.2.3.4")

    def test_reset_prone_mark_drops_retransmit_safe(self) -> None:
        """A later reset-prone mark must cancel Wi-Fi copies on that IP."""
        client = MarstekUDPClient()
        client.set_openapi_retransmit_safe("1.2.3.4", True)
        assert client.is_openapi_retransmit_safe("1.2.3.4") is True
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        assert client.is_openapi_retransmit_safe("1.2.3.4") is False
        client.set_openapi_retransmit_safe("1.2.3.4", True)
        assert client.is_openapi_retransmit_safe("1.2.3.4") is False
