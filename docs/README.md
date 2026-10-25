# Marstek documentation

This integration connects Marstek batteries (Venus A, C, D, E 3.0, and Venus E mini) to Home Assistant over your local network. Start with the install, then the pictures.

These pages follow a simple order: do a task, look up a fact, then read why it works that way. Install steps stay short. The calculation and the firmware notes live further down the list.

## Getting started

- [Installation](installation.md) — HACS or a manual copy, and what has to be true before setup.
- [Configuration](configuration.md) — discovery, a manual IP and port, and the device page.
- [Visual tour](screenshots.md) — screenshots of setup, the Battery health dashboard, the cards, a linked smart plug, automations, the Energy dashboard, and Repairs.

## Using the features

- [Battery health dashboard and cards](usable-capacity.md) — turn on the sidebar page, add the cards, link a smart plug, and read **Still measuring**.
- [Energy dashboard](energy_dashboard.md) — which sensors belong on Home Assistant's Energy dashboard.
- [Automations and services](services.md) — operating mode, Charge / Discharge / Stop, passive mode, and manual schedules.
- [Options](options.md) — polling, network, power, the Battery health dashboard, and the energy meter.

## Reference and troubleshooting

- [Entities](entities.md) — every sensor, switch, number, and select, including diagnostics that start disabled.
- [Repairs](repairs.md) — connection repair and the firmware warnings.
- [Troubleshooting](troubleshooting.md) — discovery, Venus C in Auto mode, missing controls, and entities that stay unavailable.

## Under the hood

- [How capacity estimation works](capacity-estimation-methods.md) — why the estimate is built the way it is.
- [Battery health, the calculation](usable-capacity.md#how-it-works-under-the-hood) — formula, plug measurement, stored history, and limits.
- [Development](development.md) — tests, the firmware test matrix, and releases.
- [Open API Rev 3.1](marstek_device_openapi.MD) — the local UDP protocol.

Venus E 2.0 is not supported. Details are on the [installation](installation.md) page.
