---
"ha-marstek-release-tools": patch
---

Stop creating grid meter power sensors on firmware that never answers EM.GetStatus, such as HMG-50 reporting as Venus C below 155, where they could only ever read unknown.
