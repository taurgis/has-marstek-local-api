---
"ha-marstek-release-tools": patch
---

Stop Home Assistant logging a deprecation warning about this integration on every reauth or reconfigure, by letting the config entry's own update listener schedule the reload.
