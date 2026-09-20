---
"ha-marstek-release-tools": patch
---

Read the network interface table in a worker thread and ask Home Assistant which adapters are enabled, so a discovery sweep no longer blocks the event loop and reaches every adapter Home Assistant knows about.
