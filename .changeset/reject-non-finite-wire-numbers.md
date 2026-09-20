---
"ha-marstek-release-tools": patch
---

Fix a glitched device reply permanently poisoning sensor values by rejecting non-finite numbers at the UDP boundary and in the status merge.
