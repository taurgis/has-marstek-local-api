---
"ha-marstek-release-tools": patch
---

Fix broadcast discovery in the config flow and IP-change scanner missing devices on a port an existing entry already uses, by reading the paused pooled socket instead of binding a second socket that split the replies with it.
