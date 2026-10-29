# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The slide-decks skill's deck engine: outline -> .pptx, and a deck checker.

Plain functions with no GAIA dependency, so they can be called from the skill's
``tools.py``, from ``run_python`` (put the skill folder on ``sys.path``), or
from tests:

- :func:`slidedeck.build.build_deck`, :func:`~slidedeck.build.add_slide`,
  :func:`~slidedeck.build.update_slide`
- :func:`slidedeck.verify.verify_deck`
- :func:`slidedeck.blueprints.outline_from_blueprint`,
  :func:`~slidedeck.blueprints.list_blueprints`
- :func:`slidedeck.markdown.outline_from_markdown`
- :mod:`slidedeck.style` — design tokens, preferences, writing rules
"""
