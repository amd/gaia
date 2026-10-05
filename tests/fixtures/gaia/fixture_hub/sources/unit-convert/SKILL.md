---
name: unit-convert
description: Convert a quantity between metric and imperial units and show the result in a fixed, checkable format. Use when the user asks to convert miles, kilometres, pounds, kilograms, feet, metres, or temperatures.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    provenance:
      source: gaia-eval-fixture
---

# Unit Convert

A minimal instruction-only skill published SIGNED to the gaia eval fixture hub
and never pre-installed, so a scenario can run the whole search, install, load,
use, remove loop against it.

## Output format

Answer every conversion in exactly three lines:

1. The exact line `UNIT-CONVERT v1.0.0`.
2. `<value> <from-unit> = <result> <to-unit>`, the result rounded to two
   decimals.
3. The exact line `(converted by unit-convert)`.

## Factors

- 1 mile = 1.609344 km
- 1 lb = 0.45359237 kg
- 1 ft = 0.3048 m
- °F to °C: (F - 32) × 5 / 9
