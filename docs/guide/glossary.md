---
title: Glossary
description: Plain-language definitions of the terms used in the Marstek integration docs, from Open API and CT meter to passive mode and HACS.
---

# Glossary

Terms you'll meet in these docs, in alphabetical order.

## AI mode

A Marstek operating mode where the battery decides when to charge and discharge using Marstek's own logic.

## Auto mode (self-consumption)

The battery watches your grid meter and charges from excess solar or discharges to cover the house, aiming for zero grid import or export.

## Config entry

Home Assistant's name for one configured instance of an integration. Each Marstek battery is its own config entry.

## CT meter (CT002, CT003)

A clamp-on meter at your fuse box that measures grid power. Marstek batteries use it in Auto mode. CT stands for current transformer.

## Depth of discharge (DoD)

How far the battery may discharge, as a percentage of its capacity. A lower value keeps more charge in reserve.

## Device action

A ready-made action in Home Assistant's automation editor, such as **Charge battery**, that needs no YAML.

## Discovery

Home Assistant sends a broadcast question on your network and lists the Marstek batteries that answer.

## Firmware

The software running inside the battery. Marstek updates it through the app. The integration reads its version number to decide which features are safe.

## HACS

The Home Assistant Community Store. A free add-on that installs and updates community integrations like this one. [hacs.xyz](https://hacs.xyz/)

## HMG-50

The controller board inside Venus C and Venus E 2.0. It shares one Wi-Fi channel between the Open API and the meter link, which causes the [Venus C caveat](supported-devices.md#venus-c-caveat).

## Manual mode

The battery follows a weekly timetable of up to ten time slots (six on Venus E mini), each with a power and days of the week.

## Open API (Local API)

The local network interface built into Marstek batteries. Off by default. You turn it on in the Marstek app.

## Passive mode

The battery charges or discharges at a power you choose, for a time you choose, then stops.

## Polling

Asking the battery for its state at a regular interval. The battery never reports changes by itself.

## Port

A number that identifies which program on a device receives a network message. The Open API uses UDP port 30000 unless you change it in the app.

## PV

Photovoltaic, meaning solar panels. Venus A and Venus D have solar inputs.

## Repairs

A page in Home Assistant, under **Settings** > **System** > **Repairs**, where integrations report problems you may need to act on.

## SoC (state of charge)

How full the battery is, in percent.

## SoH (state of health)

How much the battery can still store, compared with when it was new.

## UDP

A lightweight network protocol. Messages are small and fast, but a lost message is not resent automatically.

## UPS mode

A Marstek operating mode for backup power. Available on Venus A, D, and E 3.0 with firmware 150 or newer, and on Venus E mini firmware 301. Not on Venus C.

## VLAN

A separate virtual network on the same physical network, often used to keep smart-home devices apart. Discovery doesn't cross VLANs.
