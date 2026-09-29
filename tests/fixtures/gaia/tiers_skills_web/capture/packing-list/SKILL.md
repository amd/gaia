---
name: packing-list
description: Turn a trip description into a packing list split into what to wear and what to carry.
version: "1.0.0"
license: MIT
---

# Packing List

When the user describes a trip and asks what to pack:

1. Open with the exact header line `PACK LIST - <destination>`.
2. A **Wear** section and a **Carry** section, bullets under each.
3. Close with the exact line `Checked by packing-list.`

Only list items that follow from what the user said about the trip.
