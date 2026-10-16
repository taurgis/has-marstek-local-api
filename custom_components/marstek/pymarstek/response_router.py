"""Match inbound Marstek datagrams to the exchanges waiting for them.

JSON-RPC ids are uint16 on the wire and every device reuses them, so a reply
is matched on ``(source ip, id)`` first and only then on the bare id, which is
what broadcast discovery waits on. Replies nobody is waiting for are cached
for a short while: a broadcast drains them once its window closes, and a
unicast that retransmitted may find its answer already in.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .validators import json_rpc_wire_id

_LOGGER = logging.getLogger(__name__)

type ResponseKey = int | tuple[str, int]


def _is_jsonrpc_error(response: dict[str, Any]) -> bool:
    """Return True when the datagram is a JSON-RPC error object."""
    return isinstance(response.get("error"), dict)


class ResponseRouter:
    """Pending-reply waiters plus the short-lived cache of stray replies."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        max_cached: int = 50,
        max_age: float = 30.0,
    ) -> None:
        self._clock = clock
        self.pending: dict[ResponseKey, asyncio.Future[dict[str, Any]]] = {}
        self.cache: dict[ResponseKey, dict[str, Any]] = {}
        # Writes opt in. A read that still has Wi-Fi copies waiting must not
        # take an id-0 parse error, or the first busy reply cancels the retry.
        self._orphan_parse_error: set[ResponseKey] = set()
        self.max_cached = max_cached
        self.max_age = max_age

    @staticmethod
    def key(wire_id: int, device_ip: str | None) -> ResponseKey:
        """Return the waiter key for a unicast or a broadcast id."""
        if device_ip is None:
            return wire_id
        return (device_ip, wire_id)

    def track(
        self,
        request_id: Any,
        *,
        device_ip: str | None = None,
        accept_orphan_parse_error: bool = False,
    ) -> tuple[int, asyncio.Future[dict[str, Any]]]:
        """Register a waiter under the firmware's uint16 JSON-RPC id.

        ``accept_orphan_parse_error`` is for a one-shot unicast. Control
        firmware and VNSEM-0 301 answer a rejected body with JSON-RPC ``id`` 0
        and ``-32700`` instead of the request id (JSON-RPC 2.0 would use
        null). A read that will retransmit stays opted out so that first
        busy reply does not cancel the copies.
        """
        wire_id = json_rpc_wire_id(request_id)
        if wire_id is None:
            raise ValueError("Invalid message: missing id")
        key = self.key(wire_id, device_ip)
        existing = self.pending.get(key)
        if existing is not None and not existing.done():
            target = f" for {device_ip}" if device_ip else ""
            raise ValueError(f"Duplicate pending JSON-RPC id {wire_id}{target}")
        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self.pending[key] = future
        self._orphan_parse_error.discard(key)
        if accept_orphan_parse_error and isinstance(key, tuple):
            self._orphan_parse_error.add(key)
        return wire_id, future

    def drop_waiter(self, wire_id: int, *, device_ip: str | None = None) -> None:
        """Forget a waiter whose exchange has finished."""
        key = self.key(wire_id, device_ip)
        self.pending.pop(key, None)
        self._orphan_parse_error.discard(key)

    def pop_waiter(
        self, request_id: int, *, source_ip: str | None = None
    ) -> asyncio.Future[dict[str, Any]] | None:
        """Take the waiter a reply may complete, if there is one.

        A source-tagged datagram may only complete the matching ``(ip, id)``
        unicast or a generic broadcast request. Duplicate replies must not
        complete another device's pending request that happens to share the
        same JSON-RPC id.
        """
        if source_ip is not None:
            future = self.pending.pop((source_ip, request_id), None)
            if future is not None:
                return future
        return self.pending.pop(request_id, None)

    def deliver(self, request_id: int, response: dict[str, Any], addr: tuple[str, int]) -> None:
        """Cache a reply and hand it to its waiter, if one is still there."""
        self.cache[(addr[0], request_id)] = {
            "response": response,
            "addr": addr,
            "timestamp": self._clock(),
        }
        future = self.pop_waiter(request_id, source_ip=addr[0])
        if future is None and request_id == 0 and _is_jsonrpc_error(response):
            future = self._take_orphan_parse_error_waiter(addr[0])
        if future and not future.done():
            future.set_result(response)
        elif future is None:
            _LOGGER.debug(
                "Ignoring UDP response id=%s from %s; no matching pending request",
                request_id,
                addr[0],
            )

    def take_cached(self, request_id: int, *, since: float) -> list[dict[str, Any]]:
        """Take cached replies for a JSON-RPC id from every source IP.

        Only replies cached at or after *since* count. JSON-RPC ids are
        uint16 on the wire and discovery reuses them, so a late datagram
        answering the *previous* broadcast must not be handed to this one.
        """
        responses: list[dict[str, Any]] = []
        for key in list(self.cache):
            matches_id = key == request_id or (
                isinstance(key, tuple) and len(key) == 2 and key[1] == request_id
            )
            if not matches_id:
                continue
            if self.cache[key].get("timestamp", 0.0) < since:
                continue
            cached = self.cache.pop(key)
            response = cached.get("response")
            if isinstance(response, dict):
                responses.append(response)
        return responses

    def evict_stale(self) -> None:
        """Drop aged-out cache entries, then trim the cache back to size.

        Called every few replies so late or orphaned entries cannot grow the
        cache without bound.
        """
        if not self.cache:
            return

        current_time = self._clock()
        stale_ids = [
            request_id
            for request_id, cached in self.cache.items()
            if current_time - cached.get("timestamp", 0) > self.max_age
        ]
        for request_id in stale_ids:
            self.cache.pop(request_id, None)

        if len(self.cache) > self.max_cached:
            oldest_first = sorted(
                self.cache.items(), key=lambda entry: entry[1].get("timestamp", 0)
            )
            # Reaching this branch means the cache is over the max size, so
            # ``to_remove`` is always positive.
            to_remove = len(self.cache) - self.max_cached // 2
            for request_id, _ in oldest_first[:to_remove]:
                self.cache.pop(request_id, None)

            _LOGGER.debug(
                "Cleaned up %d stale response cache entries",
                to_remove + len(stale_ids),
            )

    def _take_orphan_parse_error_waiter(
        self, source_ip: str
    ) -> asyncio.Future[dict[str, Any]] | None:
        """Take the one in-flight unicast that opted in to an id-0 error.

        Two opted-in exchanges on the same host stay unmatched: the datagram
        does not say which request the firmware failed to parse.
        """
        matches = [
            key
            for key in self._orphan_parse_error
            if isinstance(key, tuple) and key[0] == source_ip and key in self.pending
        ]
        if len(matches) != 1:
            return None
        key = matches[0]
        self._orphan_parse_error.discard(key)
        return self.pending.pop(key)

    def cancel_all(self) -> None:
        """Cancel every waiter and forget every cached reply."""
        waiters = list(self.pending.values())
        self.pending.clear()
        self._orphan_parse_error.clear()
        for future in waiters:
            if not future.done():
                future.cancel()
        self.cache.clear()
