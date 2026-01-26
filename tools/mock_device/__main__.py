"""Entry point for mock Marstek device."""

import argparse

from .const import DEFAULT_UDP_PORT
from .device import MockMarstekDevice


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
        "--ble-mac", type=str, default="009b08a5aa39", help="BLE MAC address"
    )
    parser.add_argument(
        "--wifi-mac", type=str, default="7483c2315cf8", help="WiFi MAC address"
    )
    parser.add_argument(
        "--soc",
        type=int,
        default=50,
        help="Initial battery SOC percentage (default: 50)",
    )
    parser.add_argument(
        "--no-simulate",
        action="store_true",
        help="Disable dynamic simulation (static values only)",
    )
    args = parser.parse_args()

    config = {
        "device": args.device,
        "ble_mac": args.ble_mac.replace(":", "").lower(),
        "wifi_mac": args.wifi_mac.replace(":", "").lower(),
    }

    device = MockMarstekDevice(
        port=args.port,
        device_config=config,
        ip_override=args.ip,
        initial_soc=args.soc,
        simulate=not args.no_simulate,
    )
    device.start()


if __name__ == "__main__":
    main()
