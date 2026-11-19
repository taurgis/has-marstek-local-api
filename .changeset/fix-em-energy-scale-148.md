---
"ha-marstek-release-tools": patch
---

Fix meter input and output energy reading 10 times too high on firmware 148, including Venus E 3.0. Energy Dashboard history from before this fix stays too high, and the first corrected reading can show up as a spike; adjust it under Developer tools > Statistics.
