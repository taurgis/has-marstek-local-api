---
"ha-marstek-release-tools": patch
---

Set a device up exactly once when its address changes, instead of twice from discovery and repairs or not at all when a reconfigure corrected an unreachable device, and stop Home Assistant logging a deprecation warning about this integration on every reauth or reconfigure.
