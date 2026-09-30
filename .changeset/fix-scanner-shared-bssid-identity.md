---
"ha-marstek-release-tools": patch
---

Fix the IP-change scanner and config flow matching batteries by the reported `wifi_mac`, which is the access point's BSSID, so one battery's entry could move to another battery on the same access point and a second battery could not be added.
