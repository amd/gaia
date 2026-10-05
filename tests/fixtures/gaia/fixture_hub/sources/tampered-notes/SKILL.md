---
name: tampered-notes
description: Keep short numbered notes during a conversation and list them back on request.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    provenance:
      source: gaia-eval-fixture
---

# Tampered Notes

Published to the gaia eval fixture hub SIGNED, then modified after signing by
prepare_fixture_hub.py so its signature no longer matches its content. It
exists solely so an install scenario has a tampered bundle to refuse.

## Procedure

1. When the user asks you to note something, number it and restate it.
2. When the user asks for their notes, list every one in order.
