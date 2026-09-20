---
"ha-marstek-release-tools": patch
---

Split the Open API UDP client and the config flow into focused modules — reply routing, request pacing, poll gating, firmware marks, command statistics, poll composition and the options flow — so each behaviour can be changed and tested on its own.
