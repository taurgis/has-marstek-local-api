---
"ha-marstek-release-tools": patch
---

Fix the device registry listing the Wi-Fi access point's MAC as the battery's network MAC, which let the access point's DHCP lease start a Marstek discovery; existing devices drop that connection on the next setup.
