---
"ha-marstek-release-tools": patch
---

Stop a running discovery sweep from holding up a Home Assistant shutdown, and debounce repeat discovery of unconfigured devices on a UTC clock so a daylight saving change cannot skip or repeat an hour of it.
