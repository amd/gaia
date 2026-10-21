# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deck blueprints: outline templates with named slots, scaled to a slide count.

A blueprint (:data:`~slidedeck.blueprint_data.BLUEPRINTS`, exported as
``templates/blueprints/<name>.json``) lists slots in storyline
order. Each slot has a slide ``type``, a ``priority`` (1 = keep whatever the
length), optional ``repeat`` (how many copies it may grow to), ``min_deck``
(only in decks at least this long, e.g. section dividers), ``merge_into``
(where its content goes when it is dropped) and ``guidance`` for the writer.

Scaling to N slides is deterministic: every slot, and every extra copy of a
repeatable slot (costing ``grow`` more priority per copy), is a candidate;
the N best are kept, in storyline order. Unfilled slots come back as
clearly marked TODO slides that ``verify_deck`` reports as errors.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from .blueprint_data import BLUEPRINTS
from .outline import OutlineError

TEXT_TYPES = ("title", "section", "agenda", "bullets", "two_column", "quote")


def _load_all() -> Dict[str, Dict[str, Any]]:
    return {bp["name"]: bp for bp in BLUEPRINTS}


def list_blueprints() -> List[Dict[str, Any]]:
    """One compact entry per blueprint: name, use, slide range, slots."""
    out = []
    for name, bp in _load_all().items():
        out.append(
            {
                "name": name,
                "use_for": bp["description"],
                "default_slides": bp.get("default_slides"),
                "slides": f"{bp.get('min_slides', 1)}-{bp.get('max_slides', 40)}",
                "slots": [
                    f"{s['id']} ({s['type']}{', x' + str(s['repeat']) if s.get('repeat', 1) > 1 else ''})"
                    for s in bp["slots"]
                ],
                **({"variants": sorted(bp["variants"])} if bp.get("variants") else {}),
            }
        )
    return out


def get_blueprint(name: str) -> Dict[str, Any]:
    all_bp = _load_all()
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    for n, bp in all_bp.items():
        if key in (n, *[a.lower() for a in bp.get("aliases", [])]):
            return copy.deepcopy(bp)
    raise OutlineError(
        [
            f"blueprint {name!r} does not exist. Choose one of: {', '.join(sorted(all_bp))} (list_blueprints describes them)."
        ]
    )


def plan_slots(
    bp: Dict[str, Any], n: int
) -> Tuple[List[Tuple[Dict[str, Any], int]], List[Dict[str, Any]]]:
    """``([(slot, copy_number), ...] in storyline order, dropped slots)`` for *n* slides.

    Every slot, and every extra copy of a repeatable slot, is a candidate
    with a priority: the slot's own for the first copy, plus ``grow`` (default
    1.0) for each further copy. The *n* best candidates are kept (ties keep
    storyline order), so a longer deck first gains evidence slides, then the
    optional ones, deterministically.
    """
    slots = [dict(s, _pos=i) for i, s in enumerate(bp["slots"])]
    if n == 1:
        one = bp.get("one_slide") or next(
            (
                s["id"]
                for s in sorted(slots, key=lambda s: s.get("priority", 5))
                if s["type"] != "title"
            ),
            slots[0]["id"],
        )
        slot = next(s for s in slots if s["id"] == one)
        return [(slot, 1)], [s for s in slots if s["id"] != one]
    candidates = []
    for s in slots:
        if n < s.get("min_deck", 0):
            continue
        for k in range(1, int(s.get("repeat", 1)) + 1):
            candidates.append(
                (s.get("priority", 5) + (k - 1) * s.get("grow", 1.0), s["_pos"], k, s)
            )
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    if len(candidates) < n:
        raise OutlineError(
            [
                f"blueprint '{bp['name']}' reaches at most {len(candidates)} slides; {n} were asked for. Add slides yourself or pick a longer blueprint."
            ]
        )
    chosen = candidates[:n]
    # A copy never appears without the copies before it.
    plan = sorted(((c[3], c[2]) for c in chosen), key=lambda t: (t[0]["_pos"], t[1]))
    kept = {s["id"] for s, _k in plan}
    return plan, [s for s in slots if s["id"] not in kept]


def _todo_slide(slot: Dict[str, Any], key: str) -> Dict[str, Any]:
    guidance = slot.get("guidance", "")
    title = slot.get("title") or slot["id"].replace("_", " ").capitalize()
    if slot["type"] == "title":
        return {
            "type": "title",
            "title": f"TODO: {title}",
            "subtitle": f"TODO: {guidance}" if guidance else "TODO",
            "todo": True,
            "slot": key,
        }
    if slot["type"] == "section":
        return {"type": "section", "title": f"TODO: {title}", "todo": True, "slot": key}
    # Charts, tables, diagrams... need real data: stand in a text slide that
    # says what to put there, so the deck still builds and the gap is visible.
    return {
        "type": "bullets",
        "title": f"TODO: {title}",
        "bullets": [
            f"TODO ({slot['type']}{' ' + slot.get('spec', {}).get('diagram', '') if slot['type'] == 'diagram' else ''}): {guidance}"
        ],
        "todo": True,
        "slot": key,
    }


def outline_from_blueprint(
    name: str,
    slide_count: Optional[int] = None,
    slots: Optional[Dict[str, Any]] = None,
    variant: Optional[str] = None,
) -> Dict[str, Any]:
    """Scale blueprint *name* to *slide_count* slides and fill the given *slots*.

    *slots* maps a slot id (``"trend"``), or a copy (``"trend#2"``), to slide
    fields — or to a list of slide-field dicts, one per copy. Returns the
    outline plus a slot table saying which slide holds which slot and what is
    still TODO.
    """
    bp = get_blueprint(name)
    if variant:
        variants = bp.get("variants") or {}
        if variant not in variants:
            raise OutlineError(
                [
                    f"blueprint '{bp['name']}' has no variant {variant!r}; use one of {', '.join(sorted(variants)) or 'none'}."
                ]
            )
        for slot_id, override in variants[variant].items():
            for s in bp["slots"]:
                if s["id"] == slot_id:
                    s.setdefault("spec", {}).update(override.get("spec", {}))
                    for k in ("title", "guidance"):
                        if k in override:
                            s[k] = override[k]
    n = int(slide_count or bp.get("default_slides") or len(bp["slots"]))
    lo, hi = int(bp.get("min_slides", 1)), int(bp.get("max_slides", 40))
    if not lo <= n <= hi:
        raise OutlineError(
            [f"blueprint '{bp['name']}' supports {lo}-{hi} slides; {n} were asked for."]
        )
    fills = dict(slots or {})
    known = {s["id"] for s in bp["slots"]}
    unknown = sorted({k.split("#")[0] for k in fills} - known)
    if unknown:
        raise OutlineError(
            [
                f"unknown slot(s) {', '.join(unknown)} for blueprint '{bp['name']}'. Its slots: {', '.join(s['id'] for s in bp['slots'])}."
            ]
        )
    plan, dropped = plan_slots(bp, n)
    out_slides, table = [], []
    for i, (slot, k) in enumerate(plan, 1):
        key = slot["id"] if k == 1 else f"{slot['id']}#{k}"
        fill = fills.get(key)
        if fill is None and isinstance(fills.get(slot["id"]), list):
            lst = fills[slot["id"]]
            fill = lst[k - 1] if k - 1 < len(lst) else None
        elif fill is None and k == 1:
            fill = fills.get(slot["id"])
        if isinstance(fill, list):
            fill = fill[0] if fill else None
        if fill:
            if not isinstance(fill, dict):
                raise OutlineError(
                    [
                        f"slot {key}: the fill must be an object of slide fields (e.g. title, bullets, series)."
                    ]
                )
            slide = {
                "type": slot["type"],
                **copy.deepcopy(slot.get("spec", {})),
                **fill,
            }
            slide.setdefault("kind", slot.get("kind", slot["type"]))
            slide["slot"] = key
            filled = True
        else:
            slide = _todo_slide(slot, key)
            filled = False
        out_slides.append(slide)
        table.append(
            {
                "slide": i,
                "slot": key,
                "type": slot["type"],
                "filled": filled,
                "guidance": slot.get("guidance", ""),
            }
        )
    return {
        "outline": {
            "title": bp.get("title", ""),
            "expected_slides": n,
            "slides": out_slides,
        },
        "blueprint": bp["name"],
        "storyline": bp.get("storyline", ""),
        "slots": table,
        "todo": [t["slot"] for t in table if not t["filled"]],
        "dropped": [
            {"slot": s["id"], "fold_into": s.get("merge_into", "")} for s in dropped
        ],
    }
