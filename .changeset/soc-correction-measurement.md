---
"ha-marstek-release-tools": patch
---

Fix charge-level corrections being missed on slow solar charges, counted twice after a mid-range jump, or reported as holds on honest batteries that taper near full, and stop calling the drift figure a bound on the capacity error.
