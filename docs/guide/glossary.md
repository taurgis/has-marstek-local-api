---
title: Glossary
description: Plain-language definitions of the terms used in the Marstek integration docs, from Open API and CT meter to passive mode and HACS.
---

# Glossary

Terms you'll meet in these docs, grouped by topic and in alphabetical order within each group. Other pages link here when they use a term without explaining it.

## Your battery

### CT meter (CT002, CT003)

A clamp-on meter at your fuse box that measures how much power your house takes from or sends to the grid. Marstek batteries use it in Auto mode. CT stands for current transformer, the clamp that does the measuring.

### Depth of discharge (DoD)

How far the battery may discharge, as a percentage of its capacity. A lower value keeps more charge in reserve.

### Firmware

The software running inside the battery. Marstek updates it through the app. Each version has a number, such as 150. The integration reads that number to decide which features are safe on your battery.

### HMG-50

The controller board inside Venus C and Venus E 2.0. It shares one Wi-Fi channel between the Open API and the link to its CT meter, which causes the [Venus C caveat](supported-devices.md#venus-c-caveat).

### Open API (Local API)

The local network interface built into Marstek batteries. It lets Home Assistant read and control the battery without Marstek's cloud. It is off by default. You turn it on in the Marstek app.

### PV

Photovoltaic, meaning solar panels. Venus A and Venus D have solar inputs.

### SoC (state of charge)

How full the battery is, in percent.

### SoH (state of health)

How much the battery can still store, compared with when it was new.

## Operating modes

The operating mode decides what the battery does on its own. You pick it in the Marstek app or in Home Assistant.

### AI mode

A Marstek operating mode where the battery decides when to charge and discharge using Marstek's own logic.

### Auto mode (self-consumption)

The battery watches your CT meter and charges from excess solar or discharges to cover the house, aiming for zero grid import or export.

### Manual mode

The battery follows a weekly timetable of up to ten time slots (six on Venus E mini), each with a power and days of the week.

### Passive mode

The battery charges or discharges at a power you choose, for a time you choose, then stops.

### UPS mode

A Marstek operating mode for backup power. Available on Venus A, D, and E 3.0 with firmware 150 or newer, and on Venus E mini firmware 301. Not on Venus C.

## Home Assistant

### Config entry

Home Assistant's name for one configured instance of an integration. Each Marstek battery is its own config entry.

### Device action

A ready-made action in Home Assistant's automation editor, such as **Charge battery**, that needs no YAML.

### Diagnostics

A file Home Assistant can download for one integration, with its settings and recent state. Network addresses, MAC addresses, and the Wi-Fi name are hidden. Attach it to a bug report so others can see what your battery reported. Find it on the integration page under **⋮** > **Download diagnostics**.

### Entity

One value or control in Home Assistant, such as **Battery level** or the **Operating mode** select. Each battery shows up as a device with many entities.

### HACS

The Home Assistant Community Store. A free add-on that installs and updates community integrations like this one. [hacs.xyz](https://hacs.xyz/)

### Integration

A piece of software that connects Home Assistant to a device or service. This integration connects it to Marstek batteries.

### Polling

Asking the battery for its state at a regular interval. The battery never reports changes by itself, so Home Assistant has to ask.

### Repairs

A page in Home Assistant, under **Settings** > **System** > **Repairs**, where integrations report problems you may need to act on.

### Service (action)

A command you call from an automation or script, such as `marstek.set_passive_mode`. Home Assistant now calls these "actions"; older docs call them services. Unlike a device action, a service can take extra settings like a schedule.

## Network

### Discovery

Home Assistant sends a broadcast question on your network and lists the Marstek batteries that answer. A broadcast is one message sent to every device on the network at once.

### IP address

The number that identifies a device on your network, such as `192.168.1.40`. Your router hands it out. If it changes, the integration looks for the battery again.

### Modbus

An older wired protocol for reading and controlling equipment. Some Marstek integrations use it instead of the Open API. It usually needs an adapter cable.

### MQTT

A messaging system where devices publish values to a central server, called a broker. Some Marstek integrations use it instead of the Open API.

### Port

A number that identifies which program on a device receives a network message. The Open API uses UDP port 30000 unless you change it in the app.

### Subnet (/24)

The range of addresses on one network. Most home routers use a /24 network, where all devices share the first three numbers of their IP address (mask `255.255.255.0`).

### UDP

A lightweight network protocol. Messages are small and fast, but a lost message is not resent automatically.

### VLAN

A separate virtual network on the same physical network, often used to keep smart-home devices apart. Discovery doesn't cross VLANs.
