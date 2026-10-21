#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regenerate the slide-decks skill's committed assets from its code.

- ``templates/{light,dark,minimal}.pptx`` from the design tokens
  (``slidedeck.style.THEMES`` through ``slidedeck.theme``)
- ``templates/blueprints/*.json`` from ``slidedeck.blueprint_data``

The skill builds themes in memory and reads blueprints from its own module, so
these files are for people (open them in PowerPoint, read the blueprints) and
for reproducibility. ``tests/unit/skills/slide_decks`` fails when they drift.

    python util/gen_slide_deck_assets.py
"""

import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "hub" / "skills" / "slide-decks"


def main() -> int:
    sys.path.insert(0, str(SKILL))
    from slidedeck.blueprint_data import BLUEPRINTS
    from slidedeck.theme import write_templates

    templates = SKILL / "templates"
    blueprints = templates / "blueprints"
    blueprints.mkdir(parents=True, exist_ok=True)
    for theme, path in write_templates(templates).items():
        print(f"theme {theme}: {path}")
    for bp in BLUEPRINTS:
        path = blueprints / f"{bp['name']}.json"
        path.write_text(
            json.dumps(bp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"blueprint {bp['name']}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
