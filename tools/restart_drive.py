"""Poll a device the way the coordinator does and print every outage it closes.

Each poll is the integration's own ``get_device_status`` (retransmits, parser,
carried-forward values) followed by its ``RestartDetector``, so a printed line
is what the Restarts sensor would record. Pair it with the mock's fault flags
to see restarts and Wi-Fi drops side by side:

    python3 -m tools.mock_device --port 30011 --restart-every 300 \\
        --boot-seconds 60 --energy-save-seconds 120 --wifi-drop-rate 0.2
    python3 tools/restart_drive.py 127.0.0.1 --port 30011 --interval 15 --minutes 20

A lost datagram is retried inside the poll and never prints. A poll that stays
silent opens an outage; the line printed when the device answers again says
whether a boot trace came with it. Run it from the repository root in the
Home Assistant test venv.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.helpers.coordinator_helpers import raise_if_invalid_status
from custom_components.marstek.helpers.restart_detector import RestartDetector
from custom_components.marstek.pymarstek import MarstekUDPClient

_LOGGER = logging.getLogger(__name__)


async def drive(args: argparse.Namespace) -> RestartDetector:
    """Poll until the time is up and return the detector."""
    client = MarstekUDPClient(args.port, bind_port=args.bind)
    await client.async_setup()
    # The coordinator sets this from the firmware profile; 150+ is safe.
    client.set_openapi_retransmit_safe(args.ip, True)
    profile = resolve_firmware_profile(args.device, args.ver)
    detector = RestartDetector()
    previous: dict[str, Any] | None = None
    deadline = time.monotonic() + args.minutes * 60
    try:
        while time.monotonic() < deadline:
            started = time.monotonic()
            status = await _poll(client, args, profile, previous)
            now = time.time()
            if status is None:
                detector.record_failure()
                print(f"{time.strftime('%H:%M:%S')} poll failed", flush=True)
            else:
                outage = detector.record_success(now, previous, status)
                if outage is not None:
                    traces = ", ".join(outage.evidence) or "no boot trace"
                    print(
                        f"{time.strftime('%H:%M:%S')} {outage.kind.upper()}: offline "
                        f"{outage.offline_s:.0f}s, {outage.failed_polls} failed poll(s), {traces}",
                        flush=True,
                    )
                previous = status
            await asyncio.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    finally:
        await client.async_cleanup()
    return detector


async def _poll(
    client: MarstekUDPClient,
    args: argparse.Namespace,
    profile: Any,
    previous: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return one merged status, or None when the poll failed."""
    try:
        status = await client.get_device_status(
            args.ip,
            port=args.port,
            timeout=args.timeout,
            include_pv=False,
            include_wifi=False,
            include_bat=False,
            delay_between_requests=args.delay,
            previous_status=previous,
            profile=profile,
        )
        # The coordinator's own check: a poll nothing answered is a failure,
        # even though it carries the previous values forward.
        raise_if_invalid_status(args.ip, status, _LOGGER)
    except (TimeoutError, OSError, ValueError):
        return None
    return status


def main() -> None:
    """Parse arguments, drive the device and print a summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ip")
    parser.add_argument("--port", type=int, default=30000)
    parser.add_argument("--bind", type=int, default=0, help="Local port; 0 is ephemeral")
    parser.add_argument("--device", default="VenusE 3.0")
    parser.add_argument("--ver", type=int, default=150)
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between polls")
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds per request")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    parser.add_argument("--minutes", type=float, default=30.0)
    args = parser.parse_args()
    detector = asyncio.run(drive(args))
    kinds = [event.kind for event in detector.events]
    print(f"restarts: {kinds.count('restart')}, dropouts: {kinds.count('dropout')}")


if __name__ == "__main__":
    main()
