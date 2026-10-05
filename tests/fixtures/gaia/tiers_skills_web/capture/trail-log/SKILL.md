---
name: trail-log
description: Record a hike the user describes as a structured trail log entry.
version: "1.0.0"
license: MIT
---

# Trail Log

When the user describes a hike and asks you to log it:

1. Open with the exact header line `TRAIL LOG`.
2. Then exactly these three lines, filled from what the user said:
   `Trail: <name>`, `Distance: <n> km`, `Elevation gain: <n> m`.
3. Close with the exact line `Logged by trail-log.`

If the user did not give a value, write `unknown` for it — never estimate.
