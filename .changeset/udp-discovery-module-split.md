---
"ha-marstek-release-tools": patch
---

Move broadcast discovery out of the Open API UDP client into its own module so the sweep, the reply mapping and the discovery cache can be changed without touching the socket and unicast paths.
