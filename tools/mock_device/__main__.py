"""Entry point for mock Marstek device."""

from __future__ import annotations

import argparse

from .const import DEFAULT_CONFIG, DEFAULT_HOUSE_PV_WP, DEFAULT_PHASE_COUNT, DEFAULT_UDP_PORT
from .device import DEFAULT_STATUS_INTERVAL, MockMarstekDevice
from .utils import DEFAULT_STATE_DIR


def firmware_version(value: str) -> int:
    """Parse a non-negative integer firmware version for argparse."""
    try:
        version = int(value, 10)
    except ValueError as err:
        raise argparse.ArgumentTypeError("firmware must be an integer") from err
    if version < 0:
        raise argparse.ArgumentTypeError("firmware must be non-negative")
    return version


def main() -> None:
    """Run mock Marstek device."""
    parser = argparse.ArgumentParser(
        description="Mock Marstek device for testing with realistic battery simulation"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help=f"UDP port (default: {DEFAULT_UDP_PORT})",
    )
    parser.add_argument("--ip", type=str, help="Override reported IP address")
    parser.add_argument("--device", type=str, default="VenusE 3.0", help="Device type")
    parser.add_argument(
        "--ver",
        type=firmware_version,
        default=DEFAULT_CONFIG["ver"],
        help=f"Firmware version (default: {DEFAULT_CONFIG['ver']})",
    )
    parser.add_argument("--ble-mac", type=str, default="009b08a5aa39", help="BLE MAC address")
    parser.add_argument("--wifi-mac", type=str, default="7483c2315cf8", help="WiFi MAC address")
    parser.add_argument(
        "--soc",
        type=int,
        default=50,
        help="Initial battery SOC percentage (default: 50)",
    )
    parser.add_argument(
        "--pv-channels",
        type=str,
        help=(
            "Optional PV channel values for VenusD in the format "
            "'power:voltage:current, ...' (up to 4 channels). "
            "Example: '300:40:7.5,250:38:6.6,200:36:5.5,0:0:0'"
        ),
    )
    parser.add_argument(
        "--house-pv-wp",
        type=int,
        default=None,
        help=(
            "Rooftop PV peak power in watts for the simulated home "
            f"(default: {DEFAULT_HOUSE_PV_WP}, or 0 when --pv-channels is given; "
            "0 disables rooftop solar)"
        ),
    )
    parser.add_argument(
        "--phases",
        type=int,
        choices=(1, 3),
        default=DEFAULT_PHASE_COUNT,
        help=(
            "Phases the home is supplied on. The Venus is single-phase either "
            f"way; on 3 it only offsets phase A (default: {DEFAULT_PHASE_COUNT})"
        ),
    )
    parser.add_argument(
        "--no-simulate",
        action="store_true",
        help="Disable dynamic simulation (static values only)",
    )
    parser.add_argument(
        "--state-dir",
        type=str,
        default=str(DEFAULT_STATE_DIR),
        help="Directory to store persisted mock device state",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset persisted state for this device",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not log a line per handled request (drops are still summarised)",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_STATUS_INTERVAL,
        help=(
            "Seconds between simulator status lines "
            f"(default: {DEFAULT_STATUS_INTERVAL:g}; 0 disables them)"
        ),
    )
    args = parser.parse_args()

    config = {
        "device": args.device,
        "ver": args.ver,
        "ble_mac": args.ble_mac.replace(":", "").lower(),
        "wifi_mac": args.wifi_mac.replace(":", "").lower(),
    }

    if args.pv_channels:
        channels: list[dict[str, float]] = []
        for idx, chunk in enumerate(args.pv_channels.split(","), start=1):
            parts = chunk.split(":")
            if len(parts) != 3:
                raise SystemExit(
                    "Invalid --pv-channels format. Expected 'power:voltage:current' per channel."
                )
            try:
                power = float(parts[0])
                voltage = float(parts[1])
                current = float(parts[2])
            except ValueError as exc:
                raise SystemExit("Invalid numeric values in --pv-channels.") from exc
            channels.append(
                {
                    "channel": idx,
                    "pv_power": power,
                    "pv_voltage": voltage,
                    "pv_current": current,
                }
            )
        config["pv_channels"] = channels

    device = MockMarstekDevice(
        port=args.port,
        device_config=config,
        ip_override=args.ip,
        initial_soc=args.soc,
        simulate=not args.no_simulate,
        state_dir=args.state_dir,
        reset_state=args.reset_state,
        verbose=not args.quiet,
        status_interval=args.status_interval,
        house_pv_wp=args.house_pv_wp,
        phases=args.phases,
    )
    device.start()


if __name__ == "__main__":
    main()
