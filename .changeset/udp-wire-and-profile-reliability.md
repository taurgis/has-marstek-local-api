---
"ha-marstek-release-tools": patch
---

Fix UDP wire handling and firmware gating: send datagrams through the event loop instead of blocking on a non-blocking socket, throttle hosts that merely end in `.255`, exempt real subnet broadcasts of any prefix length, keep the reply deadline from being spent on the per-device throttle, stop broadcast discovery dropping replies from its final interval, reload a config entry when a firmware update changes a measurement scale, and treat every HMG-50 Control build from generation 153 as HMG-50.
