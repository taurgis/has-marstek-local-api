# Installation

You install the integration once, then add each battery. Check the [requirements](#requirements-checklist) below first if you haven't yet.

## HACS (recommended)

[HACS](guide/glossary.md#hacs) is the community store for Home Assistant. Installing through it means Home Assistant tells you when a new version is out. This integration is not in the HACS default list yet, so you add it once as a custom repository.

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu > **Custom repositories**. Paste `https://github.com/taurgis/has-marstek-local-api` and choose **Integration** as the type.
3. Search for **Marstek** and install.
4. Restart Home Assistant.

## Manual

Use this only if you don't run HACS. You will have to repeat it by hand for every update.

1. Download the `custom_components/marstek` folder from the [GitHub repository](https://github.com/taurgis/has-marstek-local-api).
2. Copy it into your Home Assistant config folder (the folder that holds `configuration.yaml`), so it ends up at `config/custom_components/marstek`.
3. Restart Home Assistant.

## Requirements checklist

- Home Assistant Core **2025.10** or newer.
- **Open API turned on** in the Marstek app. The [Open API](guide/glossary.md#open-api-local-api) is the local interface the integration talks to. It is off by default.
- Home Assistant can reach the battery over your network on the Open API port, **30000** unless you changed it in the Marstek app. The integration uses [UDP](guide/glossary.md#udp) on that [port](guide/glossary.md#port).
- For automatic discovery, the battery and Home Assistant are on the **same network**. If the battery sits on a separate [VLAN](guide/glossary.md#vlan), add it by IP address and allow UDP traffic on its port in your router or firewall.
- Your battery is not a Venus **E 2.0**. That model is **not supported**: polling can disconnect it from its CT003 meter.

## Next

Add the battery in [Configuration](configuration.md), then follow the [visual tour](screenshots.md) for the Battery health dashboard, the cards, and a linked smart plug.
