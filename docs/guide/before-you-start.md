---
title: Before you start
description: A five-minute checklist before installing the Marstek integration. Turn on Open API, give the battery a fixed IP address, and update its firmware.
---

# Before you start

Five checks, about five minutes. Each one prevents a problem that otherwise shows up later as "the battery is not found" or "the battery keeps going offline".

<ol class="mk-check">
<li>

**Your battery is supported.** Venus A, C, D, E 3.0, or E mini. Not Venus E 2.0. See [Will it work for me?](supported-devices.md)

</li>
<li>

**Home Assistant is version 2025.10 or newer.** Check under **Settings** > **About**.

</li>
<li>

**The battery is on your home network,** over Wi-Fi or Ethernet, and set up in the Marstek app.

</li>
<li>

**Open API is switched on** in the Marstek app. See the next section.

</li>
<li>

**The battery firmware is up to date.** Firmware is the software inside the battery. Update it in the Marstek app. On Venus A, D, and E 3.0, firmware 150 or newer is much more stable.

</li>
</ol>

## Turn on Open API

The Open API is the battery's local connection for other devices on your home network. It is off by default, and the integration cannot reach the battery until you turn it on.

1. Open the **Marstek** app and select your battery.
2. Open the battery's device settings and find **Open API**. Some app versions label it **Local API**. The exact menu differs between app versions.
3. Turn it on.
4. Note the **port**. The port is a number that tells the battery which messages belong to the Open API. The default is **30000**. Leave it unless you have a reason to change it.

> [!WARNING] Open API has no password
> Anyone on your home network can read and control the battery once Open API is on. That is how Marstek designed it. Keep the battery on a network you trust, such as your main home network or a separate IoT network, and not on a guest network.

> [!NOTE]
> Marstek notes that enabling Open API may disable some built-in app features to prevent conflicting commands.

## Give the battery a fixed IP address (recommended)

An IP address is a device's address on your home network. Your router hands these out and can give the battery a new one after a restart. The integration notices this and follows the battery, so this step is optional. A fixed address still makes everything faster and easier to troubleshoot.

In your router's settings, look for **DHCP reservation**, **static lease**, or **fixed IP**. Different routers use different names for it. Reserve the battery's current address there. You can find the address in the router's device list or in the Marstek app.

## Check your network setup

Most home networks need nothing here. Check these if your network is more advanced:

- **Separate VLAN for IoT devices?** A VLAN is a separate network for your smart-home devices. Automatic discovery won't reach across to it. Add the battery by IP address. In your router or firewall, allow UDP traffic on the battery's port (default 30000) between Home Assistant and the battery.
- **Wi-Fi "client isolation" or "AP isolation" on?** It blocks devices from talking to each other. Turn it off for the battery's network.
- **Home Assistant in Docker?** Use host networking (`network_mode: host`), as the [official container install](https://www.home-assistant.io/installation/linux#install-home-assistant-container) does. Without it, Home Assistant's network search and the battery's answers don't reach Home Assistant. If you use Home Assistant OS or a Home Assistant Green or Yellow, this doesn't apply to you.
- **Venus E mini on firmware 301?** It sends its answers to the network's `.255` address. That only works when the network uses the usual home-router subnet mask, `255.255.255.0` (also written /24). See [troubleshooting](../troubleshooting.md#venus-e-mini-manual-power-shows-as-or-passive-times-out).

## Is anything else talking to the battery?

Only one program should ask the battery for data through its Open API at a time. If you tried another Marstek integration, a script, or a Node-RED flow, turn it off first. Two programs asking at once doubles the load on fragile firmware. If both also listen on the same port, each one can catch answers meant for the other.

## Next

You're ready. Continue with [Getting started](getting-started.md).
