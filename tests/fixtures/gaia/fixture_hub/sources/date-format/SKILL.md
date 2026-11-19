---
name: date-format
description: Rewrite a calendar date in ISO 8601 (YYYY-MM-DD) and show the result in a fixed, checkable format. Use when the user asks to reformat, normalise, or standardise a date.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    provenance:
      source: gaia-eval-fixture
---

# Date Format

A minimal instruction-only skill published SIGNED to the gaia eval fixture hub.
The flagship does not bundle it, so a scenario can rely on it being absent until
the agent installs it from the hub.

## Output format

Answer every date request in exactly three lines:

1. The exact line `DATE-FORMAT v1.0.0`.
2. `<input> = <YYYY-MM-DD>`.
3. The exact line `(formatted by date-format)`.
