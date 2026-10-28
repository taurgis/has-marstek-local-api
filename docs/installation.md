# Installation

## HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu > **Custom repositories**, paste this repository's URL, and choose **Integration** as the type.
3. Search for **Marstek** and install.
4. Restart Home Assistant.

## Manual

1. Copy `custom_components/marstek` into your HA config folder as `config/custom_components/marstek`.
2. Restart Home Assistant.

## Requirements checklist

- Home Assistant Core **2025.10+**
- Device and HA on the **same LAN segment**
- **Open API enabled** in the Marstek app
- The battery's Open API UDP port reachable from Home Assistant (**30000** unless you changed it in the Marstek app)
- Venus **E 2.0 is not supported** (may disconnect the device from CT003)

## Next

Add the battery in [Configuration](configuration.md), then follow the [visual tour](screenshots.md) for the Battery health dashboard, the cards, and a linked smart plug.
