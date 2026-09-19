---
"ha-marstek-release-tools": patch
---

Retransmit read-only Open API unicasts on known-safe firmware (Control 150+ / HMG-50 156+) after a silent 500 ms wait, staying inside the configured request timeout, so Wi-Fi timeouts recover without extra copies on writes, LAN, or unknown firmware.
