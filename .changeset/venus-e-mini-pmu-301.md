---
"ha-marstek-release-tools": patch
---

Add the Venus E mini PMU 301 image, boot it in the firmware emulator until it answers the Open API itself, and match its quirks in the mock (silent unknown methods, Passive parse error, VNSEM-0 source, replies to the first three octets of its IP plus .255, any manual slot acknowledged).
