"""Compose one device status snapshot out of the individual Open API reads.

The UDP client owns the wire; this module owns the question "which commands
make up a poll, and in what order". Both the serial and the parallel poll run
off the same table so a new command only has to be described once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..firmware_profile import FirmwareProfile
from .command_builder import (
    get_battery_status,
    get_em_status,
    get_es_status,
    get_pv_status,
    get_wifi_status,
)
from .const import DEFAULT_UDP_PORT
from .data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_status_response,
    parse_pv_status_response,
    parse_wifi_status_response,
)
from .validators import ValidationError, json_rpc_result_usable

_LOGGER = logging.getLogger(__name__)

type StatusParser = Callable[[dict[str, Any]], dict[str, Any]]
type StatusSummary = Callable[[Mapping[str, Any]], str]


class StatusTransport(Protocol):
    """The slice of ``MarstekUDPClient`` a status poll needs."""

    async def send_request(
        self,
        message: str,
        target_ip: str,
        target_port: int,
        timeout: float = 5.0,
        *,
        quiet_on_timeout: bool = False,
        validate: bool = True,
        bypass_rate_limit: bool = False,
    ) -> dict[str, Any]:
        """Send one command and wait for the matching reply."""

    async def fetch_es_mode(
        self,
        device_ip: str,
        port: int = DEFAULT_UDP_PORT,
        timeout: float = 2.5,
        *,
        profile: FirmwareProfile | None = None,
        bypass_rate_limit: bool = False,
    ) -> dict[str, Any] | None:
        """Read ES.GetMode, probing both instance ids."""

    def loop_time(self) -> float:
        """Return the current event loop clock reading."""


@dataclass(frozen=True, slots=True)
class StatusRead:
    """One Open API read that contributes to a device status snapshot."""

    key: str
    method: str
    command: str
    parse: StatusParser
    summary: StatusSummary


def _es_mode_summary(data: Mapping[str, Any]) -> str:
    return f"Mode={data.get('device_mode')}, GridPower={data.get('ongrid_power')}W"


def _es_status_summary(data: Mapping[str, Any]) -> str:
    return (
        f"SOC={data.get('battery_soc')}%, "
        f"BattPower={data.get('battery_power')}W, "
        f"Status={data.get('battery_status')}"
    )


def _em_status_summary(data: Mapping[str, Any]) -> str:
    connected = "Connected" if data.get("ct_connected") else "Not connected"
    return f"CT={connected}, TotalPower={data.get('em_total_power')}W"


def _pv_status_summary(data: Mapping[str, Any]) -> str:
    return ", ".join(f"PV{channel}={data.get(f'pv{channel}_power')}W" for channel in (1, 2, 3, 4))


def _wifi_status_summary(data: Mapping[str, Any]) -> str:
    return f"RSSI={data.get('wifi_rssi')} dBm, SSID={data.get('wifi_ssid')}"


def _bat_status_summary(data: Mapping[str, Any]) -> str:
    return (
        f"Temp={data.get('bat_temp')}°C, "
        f"ChargFlag={data.get('bat_charg_flag')}, "
        f"DischrgFlag={data.get('bat_dischrg_flag')}"
    )


def build_status_reads(
    profile: FirmwareProfile | None,
    *,
    include_em: bool,
    include_pv: bool,
    include_wifi: bool,
    include_bat: bool,
) -> list[StatusRead]:
    """Return the reads a poll should make, in wire order.

    ES.GetMode is not in the table: it needs the client's instance-id
    fallback, so the poll asks the transport for it directly.
    """
    reads = [
        StatusRead(
            "es_status",
            "ES.GetStatus",
            get_es_status(0),
            lambda response: parse_es_status_response(response, profile),
            _es_status_summary,
        )
    ]
    if include_em:
        reads.append(
            StatusRead(
                "em_status",
                "EM.GetStatus",
                get_em_status(0),
                lambda response: parse_em_status_response(response, profile),
                _em_status_summary,
            )
        )
    if include_pv:
        reads.append(
            StatusRead(
                "pv_status",
                "PV.GetStatus",
                get_pv_status(0),
                lambda response: parse_pv_status_response(response, profile),
                _pv_status_summary,
            )
        )
    if include_wifi:
        reads.append(
            StatusRead(
                "wifi_status",
                "Wifi.GetStatus",
                get_wifi_status(0),
                parse_wifi_status_response,
                _wifi_status_summary,
            )
        )
    if include_bat:
        reads.append(
            StatusRead(
                "bat_status",
                "Bat.GetStatus",
                get_battery_status(0),
                parse_bat_status_response,
                _bat_status_summary,
            )
        )
    return reads


class _PollRun:
    """One poll in progress: paces the reads and remembers what answered."""

    def __init__(
        self,
        transport: StatusTransport,
        device_ip: str,
        port: int,
        timeout: float,
        delay_between_requests: float,
    ) -> None:
        self._transport = transport
        self._device_ip = device_ip
        self._port = port
        self._timeout = timeout
        self._delay = delay_between_requests
        self._sent_one = False
        self.has_fresh_data = False

    async def _pace(self, *, apply_delay: bool) -> None:
        """Space serial reads out; the first read never waits."""
        if apply_delay and self._sent_one:
            await asyncio.sleep(self._delay)

    async def es_mode(
        self,
        *,
        profile: FirmwareProfile | None,
        apply_delay: bool,
        bypass_rate_limit: bool,
    ) -> dict[str, Any] | None:
        """Fetch ES.GetMode through the client's instance-id fallback."""
        await self._pace(apply_delay=apply_delay)
        parsed = await self._transport.fetch_es_mode(
            self._device_ip,
            self._port,
            self._timeout,
            profile=profile,
            bypass_rate_limit=bypass_rate_limit,
        )
        self._sent_one = True
        if parsed is None:
            _LOGGER.debug("ES.GetMode failed for %s: no usable result", self._device_ip)
            return None
        self.has_fresh_data = True
        _LOGGER.debug("ES.GetMode parsed for %s: %s", self._device_ip, _es_mode_summary(parsed))
        return parsed

    async def read(
        self, read: StatusRead, *, apply_delay: bool, bypass_rate_limit: bool
    ) -> dict[str, Any] | None:
        """Send one table read and parse its reply, or None on any failure."""
        await self._pace(apply_delay=apply_delay)
        try:
            response = await self._transport.send_request(
                read.command,
                self._device_ip,
                self._port,
                timeout=self._timeout,
                # A read that times out contributes nothing and is already
                # recorded below; the coordinator reports the outage once.
                # Warning per read would put six lines in the log for every
                # poll of a device that is simply switched off.
                quiet_on_timeout=True,
                bypass_rate_limit=bypass_rate_limit,
            )
        except (TimeoutError, OSError, ValueError, ValidationError) as err:
            self._sent_one = True
            _LOGGER.debug("%s failed for %s: %s", read.method, self._device_ip, err)
            return None
        self._sent_one = True
        if not json_rpc_result_usable(response):
            _LOGGER.debug(
                "%s failed for %s: %s",
                read.method,
                self._device_ip,
                response.get("error", "missing result"),
            )
            return None
        parsed = read.parse(response)
        self.has_fresh_data = True
        _LOGGER.debug("%s parsed for %s: %s", read.method, self._device_ip, read.summary(parsed))
        return parsed


async def fetch_device_status(
    transport: StatusTransport,
    device_ip: str,
    port: int = DEFAULT_UDP_PORT,
    timeout: float = 2.5,
    *,
    include_pv: bool = True,
    include_wifi: bool = True,
    include_em: bool = True,
    include_bat: bool = True,
    parallel_requests: bool = False,
    delay_between_requests: float = 2.0,
    previous_status: dict[str, Any] | None = None,
    profile: FirmwareProfile | None = None,
) -> dict[str, Any]:
    """Poll a device and merge every answered read into one status dict.

    ``parallel_requests`` fires the whole table at once and bypasses the
    per-device throttle; the serial path paces reads by
    ``delay_between_requests``. Reads that fail contribute nothing, and
    ``previous_status`` keeps their last known values out of "Unknown".
    """
    reads = build_status_reads(
        profile,
        include_em=include_em,
        include_pv=include_pv,
        include_wifi=include_wifi,
        include_bat=include_bat,
    )
    run = _PollRun(transport, device_ip, port, timeout, delay_between_requests)
    collected: dict[str, dict[str, Any] | None] = {}

    if parallel_requests:
        tasks: dict[str, asyncio.Task[dict[str, Any] | None]] = {
            "es_mode": asyncio.create_task(
                run.es_mode(profile=profile, apply_delay=False, bypass_rate_limit=True)
            ),
            **{
                read.key: asyncio.create_task(
                    run.read(read, apply_delay=False, bypass_rate_limit=True)
                )
                for read in reads
            },
        }
        # Without return_exceptions a raising read propagates out of gather
        # and leaves its siblings running: they would go on sending UDP to a
        # device whose poll has already ended, and Python would report each
        # one as a never-retrieved task exception. A read that blew up
        # contributes nothing, exactly like one that timed out.
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                _LOGGER.debug(
                    "%s raised during parallel poll of %s: %s",
                    key,
                    device_ip,
                    result,
                )
                collected[key] = None
                continue
            collected[key] = result
    else:
        collected["es_mode"] = await run.es_mode(
            profile=profile, apply_delay=True, bypass_rate_limit=False
        )
        for read in reads:
            collected[read.key] = await run.read(read, apply_delay=True, bypass_rate_limit=False)

    status = merge_device_status(
        es_mode_data=collected.get("es_mode"),
        es_status_data=collected.get("es_status"),
        pv_status_data=collected.get("pv_status"),
        wifi_status_data=collected.get("wifi_status"),
        em_status_data=collected.get("em_status"),
        bat_status_data=collected.get("bat_status"),
        device_ip=device_ip,
        last_update=transport.loop_time(),
        previous_status=previous_status,
    )
    status["has_fresh_data"] = run.has_fresh_data
    return status
