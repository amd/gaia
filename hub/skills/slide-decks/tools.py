# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``slide-decks`` skill.

Thin wrappers over the :mod:`slidedeck` engine in this folder. Every path is
checked through :mod:`gaia.skills.context` — reads against the agent's allowed
folders, writes with ``check_write`` (which asks before overwriting) — and the
LibreOffice render runs through ``run_program`` in the call's temporary
folder. Saved preferences come from the skill's own preference store.

Each tool returns a dict; a problem the model can fix comes back as
``{"status": "error", "error": ..., "problems": [...]}``, never a partial deck.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from slidedeck import blueprints as _bp
from slidedeck import markdown as _md
from slidedeck import style as _style
from slidedeck.outline import (
    OutlineError,
    fold_pseudo_slides,
    load_outline,
    normalize_outline,
)

from gaia.agents.base.tools import tool
from gaia.config import gaia_home
from gaia.logger import get_logger
from gaia.skills.context import (
    SkillContextError,
    check_read,
    check_write,
    copy_to_temp,
    current,
    find_program,
    preferences,
    run_program,
    temp_dir,
)
from gaia.skills.errors import SkillPermissionError

log = get_logger(__name__)


def _build_engine() -> Any:
    """The python-pptx half of the engine, imported when a tool runs, not at skill load."""
    from slidedeck import build

    return build


def _verify_engine() -> Any:
    from slidedeck import verify

    return verify


def _image_engine() -> Any:
    from slidedeck import image

    return image


#: LibreOffice's first start builds its profile; a long deck takes a while.
RENDER_TIMEOUT_S = 240
#: Longest LibreOffice profile path that stays clear of the Windows path limit.
MAX_PROFILE_PATH = 120
_SKILL_DIR = Path(__file__).resolve().parent


def _error(exc: Exception) -> Dict[str, Any]:
    problems = getattr(exc, "problems", None) or [str(exc)]
    return {"status": "error", "error": str(exc), "problems": problems}


#: The last build of each deck path that failed its check: (fingerprint, problems).
_FAILED_BUILDS: Dict[str, tuple] = {}
#: Failed checks in a row, per agent; a model that keeps missing stops and says so.
_FAILED_IN_A_ROW: Dict[int, int] = {}
_MAX_FAILED_IN_A_ROW = 2


#: Decks each agent wrote, so a rebuild replaces its own file and never another chat's.
_WRITTEN: Dict[int, set] = {}


def _agent_key() -> int:
    try:
        return id(current().agent)
    except SkillContextError:
        return 0


def _whole(value: Any) -> int:
    """A positive slide count, or 0; a bad value is left to the outline check."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


_NUMBER_WORDS = {
    "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip
#: "one-slide", "single executive slide", "5 slides"; "one of the slides" is not a count.
_COUNT_PHRASE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
    r"(?:[\s-]+(?!(?:of|the|a|an|more|per|each|every|for|in|on|to|other|extra|"
    r"additional|new|or|and)\b)[a-z]+){0,2}[\s-]+slides?\b",
    re.I,
)


def _asked_count(request: str) -> tuple:
    """The slide count the user's message names, with the phrase; (0, "") if none or mixed."""
    found = {}
    for m in _COUNT_PHRASE.finditer(request or ""):
        word = m.group(1).lower()
        n = int(word) if word.isdigit() else _NUMBER_WORDS[word]
        if n > 0:
            found.setdefault(n, m.group(0))
    if len(found) != 1:
        return 0, ""
    return next(iter(found.items()))


#: A line saying what grows across the stages and introducing a list, e.g.
#: "As we move from left to right, increase:" or "Across the tiers these grow: a, b".
_LEAD_LINE = re.compile(r"^([^\n:]*):[ \t]*(.*)$", re.M)
_GROWTH = re.compile(r"\b(?:increas|grow|rise|rising|more)\w*", re.I)
_ACROSS = re.compile(
    r"\b(?:left[\s-]+to[\s-]+right|stages?|steps?|tiers?|levels?|phases?|"
    r"moves?|progress\w*|across)\b",
    re.I,
)
_LIST_ITEM = re.compile(r"^\s*(?:[*•-]|\d+[.)])\s+(.+?)\s*$")
#: Words too generic to tell one listed dimension from another.
_FILLER = {"number", "requirements", "requirement", "involved", "level", "amount"}


def _grows_list(request: str) -> List[str]:
    """What the user lists as growing across the stages; [] unless they list two or more."""
    m = next(
        (
            x
            for x in _LEAD_LINE.finditer(request or "")
            if _GROWTH.search(x.group(1)) and _ACROSS.search(x.group(1))
        ),
        None,
    )
    if not m:
        return []
    inline = m.group(2).strip()
    if inline:
        items = [x.strip(" .") for x in re.split(r",|\band\b", inline)]
    else:
        items = []
        for line in request[m.end() :].split("\n")[1:]:
            item = _LIST_ITEM.match(line)
            if item:
                items.append(item.group(1))
            elif line.strip() and items:
                break
    items = [x for x in items if x]
    return items if len(items) >= 2 else []


def _covered(item: str, axis: str) -> bool:
    words = [
        w
        for w in re.findall(r"[a-z]+", item.lower())
        if len(w) >= 4 and w not in _FILLER
    ]
    return not words or any(w[:5] in axis for w in words)


#: What a build did on the user's behalf, reported in its result's notes.
_NOTES: List[str] = []


def _items_of(slide: Dict[str, Any]) -> List[Any]:
    return slide.get("items") or slide.get("steps") or slide.get("stages") or []


def _axis_from(listed: List[str]) -> str:
    """The user's growth list as one short axis line: a word per item."""
    words = []
    for item in listed:
        found = [
            w
            for w in re.findall(r"[A-Za-z]+", item)
            if len(w) >= 4 and w.lower() not in _FILLER
        ]
        word = max(found, key=len).lower() if found else item.strip().lower()
        if word not in words:
            words.append(word)
    if len(words) == 1:
        return f"More {words[0]}"
    return f"More {', '.join(words[:-1])} and {words[-1]}"


def _missing_axis(slides: List[Dict[str, Any]]) -> Optional[str]:
    """Why the slides do not show what the user listed as growing across the stages."""
    try:
        listed = _grows_list(current().request)
    except SkillContextError:
        return None
    if not listed:
        return None
    stages = [
        s
        for s in slides
        if s.get("type") == "diagram"
        and (s.get("diagram") or s.get("layout") or "boxes") in ("process", "boxes")
        and len(s.get("items") or s.get("steps") or s.get("stages") or []) >= 3
    ]
    named = "; ".join(listed)
    if not stages:
        shown = json.dumps(
            [{k: v for k, v in s.items() if k != "notes"} for s in slides]
        ).lower()
        left_out = [x for x in listed if not _covered(x, shown)]
        if not left_out:
            return None
        return (
            "The user listed what grows across the stages, but the slides "
            f"never show {'; '.join(left_out)}. Put the stages as '### ' boxes "
            f"under 'Diagram: process' and name {named} on an 'Axis:' line."
        )
    axes = [_text_of(s.get("x_axis")).lower() for s in stages if s.get("x_axis")]
    if not axes and len(stages) == 1 and len(_items_of(stages[0])) <= 6:
        # The user wrote the list out, so the axis is theirs: add it, not a retry.
        stage = stages[0]
        stage["x_axis"] = _axis_from(listed)
        key = "layout" if "layout" in stage and "diagram" not in stage else "diagram"
        stage[key] = "process"
        _NOTES.append(
            f"Added the user's list as the stages' axis: '{stage['x_axis']}'."
        )
        return None
    if not axes:
        return (
            "The user listed what grows across the stages, but the stages slide "
            "has no 'Axis:' line, so that progression is missing or buried "
            f"in a box. Add one 'Axis:' line on that slide naming: {named}. "
            "Shorten each to a word or two, keep it under 10 words, and take it "
            "out of the boxes."
        )
    left_out = [x for x in listed if not any(_covered(x, a) for a in axes)]
    if left_out:
        return (
            "The 'Axis:' line leaves out what the user listed as growing: "
            f"{'; '.join(left_out)}. Name every item of: {named}, a word or two "
            "each, under 10 words."
        )
    return None


#: The user invites invented numbers: "make up", "dummy data", "hypothetical"...
_INVENTED = re.compile(
    r"\b(?:make|made|making)\s+(?:\w+\s+){0,2}up\b|\binvent\w*|\bdummy\b|"
    r"\bplaceholder\b|\bfake\b|\bhypothetical\b|\bmock\b|"
    r"\bsample\s+(?:data|numbers|figures)\b",
    re.I,
)
_LABELLED = re.compile(
    r"illustrative|hypothetical|example|sample|not actual|made[- ]up|placeholder",
    re.I,
)


#: A number as written: "1,450", "12.4", "-3"; "Q3" and "v2" are labels, not numbers.
_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
#: "Phase 2", "Step 3": a position in a sequence, not a figure that needs a source.
_ORDINAL = re.compile(
    r"\b(?:phase|step|stage|tier|level|wave|round|sprint|milestone|option|pillar|"
    r"horizon|gate|track|part|pass)\s+\d{1,2}\b",
    re.I,
)
#: Outline keys that hold layout settings, not words or values shown on the slide.
_NOT_SHOWN = {
    "notes", "index", "type", "diagram", "layout", "chart_type", "theme", "style",
    "level", "highlight", "columns_n", "numbered", "direction", "expected_slides",
    "required_items", "allow_split", "slide_numbers", "require_notes", "path",
}  # fmt: skip


def _numbers_in(text: str) -> List[Tuple[str, float]]:
    out = []
    for m in _NUMBER.finditer(text):
        token = m.group(0)
        try:
            out.append((token, abs(float(token.replace(",", "")))))
        except ValueError:
            continue
    return out


def _shown_numbers(node: Any, where: str, out: List[Tuple[str, float, str]]) -> None:
    """Every number a slide shows, as (token, value, where it is)."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        out.append((format_value(node), abs(float(node)), where))
    elif isinstance(node, str):
        out += [(t, v, where) for t, v in _numbers_in(_ORDINAL.sub(" ", node))]
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in _NOT_SHOWN:
                continue
            if k == "csv" and isinstance(v, str):
                # Cell by cell: "Q1,3.4" is a label and a value, not "1,3".
                for row in csv.reader(io.StringIO(v)):
                    for cell in row:
                        _shown_numbers(cell.strip(), where, out)
                continue
            _shown_numbers(k, where, out)
            _shown_numbers(v, "title" if k in ("title", "subtitle") else where, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _shown_numbers(v, where, out)


def format_value(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _grounded(value: float, token: str, known: List[float]) -> bool:
    places = len(token.split(".", 1)[1]) if "." in token else 0
    for e in known:
        # The same figure rounded, as a percent of a fraction, or in K/M/B units.
        for scaled in (e, e * 100, e / 1e3, e / 1e6, e / 1e9, e * 1e3, e * 1e6):
            if round(scaled, places) == value:
                return True
    return False


def _derived(given: List[float]) -> List[float]:
    """Differences, sums, shares and growth between the user's own numbers.

    Only the user's numbers: a handful, so a chance match cannot excuse an
    invented figure, as it could among the hundreds in a file.
    """
    base = sorted(set(given))[:40]
    out = [sum(base)]
    for a in base:
        for b in base:
            if a == b:
                continue
            out += [abs(a - b), a + b]
            if b:
                out += [a / b * 100, abs(a - b) / b * 100]
    return out


def _hidden_in_notes(slides: List[Dict[str, Any]], said: List[str]) -> Optional[str]:
    """Numbers the user gave that are only in the speaker notes, not on a slide."""
    asked = {v for text in said for _t, v in _numbers_in(text)}
    on_slides = set()
    in_notes: Dict[float, str] = {}
    for s in slides:
        shown: List[Tuple[str, float, str]] = []
        _shown_numbers(s, "body", shown)
        on_slides |= {v for _t, v, _w in shown}
        for t, v in _numbers_in(str(s.get("notes") or "")):
            in_notes.setdefault(v, t)
    hidden = sorted(t for v, t in in_notes.items() if v in asked and v not in on_slides)
    if not hidden:
        return None
    return (
        f"{', '.join(hidden)} from the user's request is only in the speaker notes. "
        "Requested content goes on a slide: cut other words, merge slides, or use "
        "cards, and keep notes for extra detail."
    )


def _invented_numbers(slides: List[Dict[str, Any]]) -> Optional[str]:
    """Numbers on the slides that nothing the user said or a tool returned contains."""
    try:
        call = current()
    except SkillContextError:
        return None
    if not call.request:
        # No user message to check against: a library call, not a chat turn.
        return None
    said = call.user_messages()
    hidden = _hidden_in_notes(slides, said)
    if hidden:
        return hidden
    known = [v for text in call.sources() for _t, v in _numbers_in(text)]
    known += _derived([v for text in said for _t, v in _numbers_in(text)])
    this_year = datetime.date.today().year
    invited = bool(_INVENTED.search(call.request))
    problems: List[str] = []
    for i, s in enumerate(slides, 1):
        shown: List[Tuple[str, float, str]] = []
        _shown_numbers(s, "body", shown)
        parts = max(
            len(s.get(k) or [])
            for k in ("items", "bullets", "rows", "categories", "kpis", "steps")
        )
        bad = sorted(
            {
                t
                for t, v, where in shown
                if not _grounded(v, t, known)
                and not (v.is_integer() and this_year - 1 <= v <= this_year + 1)
                and not (where == "title" and v.is_integer() and 0 < v <= parts)
            }
        )
        if not bad:
            continue
        label = s.get("title") or f"slide {i}"
        if invited:
            if not _LABELLED.search(
                json.dumps({k: v for k, v in s.items() if k != "notes"})
            ):
                problems.append(
                    f"The user asked for made-up numbers, so '{label}' must say so "
                    "where it is seen: set its source to 'Illustrative data, not "
                    "actual results', and say so in the reply."
                )
            continue
        problems.append(f"'{label}': {', '.join(bad)}")
    if not problems or invited:
        return " ".join(problems) or None
    return (
        "These numbers are not in anything the user said or a tool returned, so "
        f"they would be invented: {'; '.join(problems)}. Use only numbers from "
        "the user's messages or a file, page or calculation you ran; compute a "
        "share, growth or total with run_python and use its result; otherwise "
        "drop the number or say it in words. Never make numbers up."
    )


def _figures_from_user(slides: List[Dict[str, Any]]) -> List[int]:
    """Slides whose every number is one the user typed or one derived from them.

    Those figures have no file or system to cite, so verify does not ask for a
    Source line the model would have to make up.
    """
    try:
        call = current()
    except SkillContextError:
        return []
    if not call.request:
        return []
    given = [v for text in call.user_messages() for _t, v in _numbers_in(text)]
    known = given + _derived(given)
    out = []
    for i, s in enumerate(slides, 1):
        shown: List[Tuple[str, float, str]] = []
        _shown_numbers({k: v for k, v in s.items() if k != "source"}, "body", shown)
        if shown and all(_grounded(v, t, known) for t, v, _w in shown):
            out.append(i)
    return out


#: Pictographs and flags: many slide viewers and the image export draw them as empty boxes.
_EMOJI = re.compile("[🀀-🫿☀-➿🇦-🇿️]")


def _stray_emoji(slides: List[Dict[str, Any]]) -> Optional[str]:
    """Emoji on the slides that the user did not use themselves."""
    try:
        request = current().request
    except SkillContextError:
        return None
    shown = json.dumps(
        [{k: v for k, v in s.items() if k != "notes"} for s in slides],
        ensure_ascii=False,
    )
    stray = sorted({e for e in _EMOJI.findall(shown) if e not in request and e != "️"})
    if not stray:
        return None
    return (
        f"Remove the emoji {' '.join(stray)} from the slides: many viewers and the "
        "image export draw them as empty boxes, and they read as informal."
    )


def _text_of(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("title") or "")
    return str(value or "")


def _merge_hint(count: int) -> str:
    if count == 1:
        return (
            "For one slide: leave out the '# ' cover, put the parts as '### ' "
            "boxes on a single '## ' slide, and for stages add 'Diagram: process' "
            "with what grows across them on an 'Axis:' line instead of a "
            "separate table or slide."
        )
    return (
        "Merge slides that make one point, fold a table or list that only "
        "explains a diagram into its 'Axis:' line or bullets, or cut the cover."
    )


def _hold_slide_count(expected: int) -> int:
    """The slide count the user's message names, which ``expected`` must match.

    A count error is fixed by changing the slides, so a model's own number
    never replaces the user's, and 0 does not skip the check. With no count in
    the message, ``expected`` is the model's choice.
    """
    try:
        request = current().request
    except SkillContextError:
        return expected
    asked, phrase = _asked_count(request)
    if not asked:
        return expected
    if expected and expected != asked:
        raise OutlineError(
            [
                f"The user asked for {asked} slide{'s' if asked > 1 else ''} "
                f'("{phrase}"), so expected_slides is {asked}, not {expected}. '
                f"Change the slides so there are exactly {asked}. " + _merge_hint(asked)
            ]
        )
    return asked


def _slides_built(data: Dict[str, Any], result: Dict[str, Any]) -> List[str]:
    """One line per deck slide, from what was built: the reply describes these."""
    # The engine's own reading of the outline: what it drew, not what was sent.
    slides = normalize_outline(data)["slides"]
    built = []
    for outline_no, deck_nos in (result.get("slide_map") or {}).items():
        idx = int(outline_no) - 1
        s = slides[idx] if 0 <= idx < len(slides) else {}
        kind = s.get("type") or "slide"
        if s.get("type") == "diagram" and s.get("layout"):
            kind = f"{kind}: {s['layout']}"
        line = f"{s.get('title') or '(untitled)'} [{kind}]"
        if s.get("takeaway"):
            line += f" Takeaway: {s['takeaway']}"
        for deck_no in deck_nos:
            built.append((deck_no, line))
    return [f"{n}. {text}" for n, text in sorted(built)]


def _fingerprint(*parts: Any) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()


def _saved_prefs() -> Dict[str, Any]:
    """The user's saved preferences, or {} when no skill call is bound."""
    try:
        return dict(preferences().get() or {})
    except SkillContextError:
        return {}


def _absolute(path: str, what: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise OutlineError(
            [
                f"{what} must be an absolute path (got {path!r}); use the folder the user named or the one the sources are in."
            ]
        )
    return p


def _readable(path: str, what: str) -> Path:
    resolved = check_read(_absolute(path, what))
    if not resolved.is_file():
        raise OutlineError([f"{what} {resolved} does not exist."])
    return resolved


def _deck_name(outline: Dict[str, Any]) -> str:
    slides = outline.get("slides") or []
    title = str(
        outline.get("title") or (slides[0].get("title") if slides else "") or ""
    )
    stem = re.sub(r"[^A-Za-z0-9]+", " ", title).strip()[:60].strip()
    return (stem or "Slide deck") + ".pptx"


def _writable_deck(
    path: str, outline: Dict[str, Any], what: str = "output_path"
) -> Path:
    # A bare name or no path saves in GAIA's documents folder, which every chat can write.
    path = (path or "").strip()
    p = Path(path).expanduser() if path else Path(_deck_name(outline))
    if not p.is_absolute():
        p = gaia_home() / "documents" / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p = _fresh_name(p)
    if p.suffix.lower() != ".pptx":
        raise OutlineError([f"{what} must end in .pptx, got {p.name!r}."])
    if _SKILL_DIR == p.resolve().parent or _SKILL_DIR in p.resolve().parents:
        raise OutlineError(
            [
                f"{what} is inside the skill's own folder; save the deck where the user keeps their files."
            ]
        )
    if not p.parent.is_dir():
        raise OutlineError(
            [f"the folder {p.parent} does not exist; save into an existing folder."]
        )
    out = check_write(p)
    _WRITTEN.setdefault(_agent_key(), set()).add(str(Path(out).resolve()))
    return out


def _fresh_name(p: Path) -> Path:
    """*p*, or "name (2).pptx" when another chat's deck already has that name."""
    mine = _WRITTEN.get(_agent_key(), set())
    n = 1
    found = p
    while found.exists() and str(found.resolve()) not in mine:
        n += 1
        found = p.with_name(f"{p.stem} ({n}){p.suffix}")
    return found


def _input_paths(outline: Dict[str, Any]) -> List[str]:
    """Every file an outline will read (images), so each is scope-checked."""
    out = []
    for slide in outline.get("slides") or []:
        if isinstance(slide, dict):
            for key in ("path", "image", "src"):
                if isinstance(slide.get(key), str) and slide.get(key):
                    out.append(slide[key])
    return out


def _check_inputs(
    outline: Any, prefs: Dict[str, Any], template_path: str
) -> Optional[str]:
    data = load_outline(outline)
    for p in _input_paths(data):
        _readable(p, "image")
    if template_path:
        _readable(template_path, "template_path")
    for key in ("template_path", "logo_path"):
        if prefs.get(key):
            _readable(prefs[key], f"preference {key}")
    return None


def _renderer():
    """A PDF renderer for verify_deck, or None when LibreOffice is unavailable."""
    if find_program("soffice") is None:
        return None

    def render(deck: Path) -> Path:
        copy = copy_to_temp(deck, "d.pptx")
        out_dir = temp_dir("o")
        profile = temp_dir("p")
        if len(str(profile)) > MAX_PROFILE_PATH:
            # LibreOffice crashes (rather than erroring) when the paths inside
            # its profile pass the Windows path limit.
            raise RuntimeError(
                f"the temporary folder path is {len(str(profile))} characters, too "
                f"long for LibreOffice (limit {MAX_PROFILE_PATH}); move GAIA_HOME "
                "to a shorter path to enable rendering"
            )
        proc = run_program(
            "soffice",
            [
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--norestore",
                "--nologo",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(copy),
            ],
            timeout=RENDER_TIMEOUT_S,
        )
        pdf = out_dir / "d.pdf"
        if proc.returncode != 0 or not pdf.is_file():
            raise RuntimeError(
                f"LibreOffice exited with code {proc.returncode} without a PDF. {(proc.stderr or proc.stdout or '').strip()[:300]}"
            )
        return pdf

    return render


@tool(display_label="Building slide deck")
def build_deck(
    outline: dict,
    output_path: str = "",
    theme: str = "",
    template_path: str = "",
    prefs: Optional[dict] = None,
) -> dict:
    """Build a .pptx from a JSON slide outline and check it; never hand-write python-pptx.

    The outline is {"slides": [...]}, each slide a "type" plus its fields
    (references/outline-json.md). Returns status, path, slides and verify
    {status, issues}; fix the issues and build again.

    Args:
        outline: The slide outline object, or its JSON text.
        output_path: Where to save the .pptx. Leave empty, or give just a file
            name, to save in GAIA's documents folder.
        theme: light, dark or minimal; empty uses the saved preference.
        template_path: Optional .pptx/.potx whose layouts and theme to use.
        prefs: Optional style the user asked for in this request (density, colors...).
    """
    return _build_from(outline, output_path, theme, template_path, prefs)


def _build_from(
    outline: Any,
    output_path: str,
    theme: str,
    template_path: str,
    prefs: Optional[dict],
    required_items: Optional[list] = None,
) -> dict:
    try:
        saved = _saved_prefs()
        request = dict(prefs or {})
        problems = _style.validate_prefs(request)
        if problems:
            raise OutlineError(problems)
        data = dict(load_outline(outline))
        count = _hold_slide_count(_whole(data.get("expected_slides")))
        if count:
            data["expected_slides"] = count
        data["slides"] = fold_pseudo_slides(data.get("slides") or [], count)
        if count and count < len(data.get("slides") or []):
            # The outline check only names the gap; say how to close it.
            raise OutlineError(
                [
                    f"The outline has {len(data['slides'])} slides but the user "
                    f"asked for {count}; keep expected_slides at {count} and "
                    "change the slides. " + _merge_hint(count)
                ]
            )
        slides = data.get("slides") or []
        _NOTES.clear()
        missing = (
            _missing_axis(slides) or _invented_numbers(slides) or _stray_emoji(slides)
        )
        if missing:
            raise OutlineError([missing])
        data["figures_from_user"] = _figures_from_user(slides)
        if request:
            data["style"] = _style.merge_prefs(data.get("style") or {}, request)
        if required_items:
            data["required_items"] = list(
                dict.fromkeys([*(data.get("required_items") or []), *required_items])
            )
        _check_inputs(data, saved, template_path)
        out = _writable_deck(output_path, data)
        key = _fingerprint(data, theme, template_path, saved)
        failed = _FAILED_BUILDS.get(str(out))
        if failed and failed[0] == key:
            raise OutlineError(
                [
                    "This is the same deck as the last build, so it would fail the "
                    "same check. Change the Markdown to fix these, or tell the user "
                    "they are not done:",
                    *failed[1],
                ]
            )
        result = _build_engine().build_deck(
            data, str(out), theme or None, template_path or None, saved
        )
        if result.get("status") == "built_with_issues":
            issues = (result.get("verify") or {}).get("issues") or []
            problems = [
                f"{i.get('detail')} {i.get('fix') or ''}".strip()
                for i in issues
                if i.get("severity") == "error"
            ]
            _FAILED_BUILDS[str(out)] = (key, problems)
            misses = _FAILED_IN_A_ROW.get(_agent_key(), 0) + 1
            _FAILED_IN_A_ROW[_agent_key()] = misses
            result["next"] = (
                "Stop building. Give the user the path and tell them which of "
                "these errors remain; never call the deck verified."
                if misses >= _MAX_FAILED_IN_A_ROW
                else "The deck failed its check. Change the Markdown to fix each "
                "error and build again; if you stop, tell the user which errors "
                "remain. Never call it verified."
            )
        else:
            _FAILED_BUILDS.pop(str(out), None)
            _FAILED_IN_A_ROW.pop(_agent_key(), None)
            result["next"] = (
                "The deck is built and passed its check. Report it to the user "
                "now, describing it only from slides_built; build again only if "
                "they ask for a change."
            )
        result["slides_built"] = _slides_built(data, result)
        if _NOTES:
            result["notes"] = [*(result.get("notes") or []), *_NOTES]
        result["style_options"] = _style.style_offer(
            _style.resolve_tokens(request, saved)
        )
        return result
    except (OutlineError, _style.PrefsError, SkillPermissionError) as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        log.exception("build_deck failed")
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "problems": [f"The deck could not be built: {exc}"],
        }


@tool(display_label="Building slide deck from Markdown", draft_argument="markdown")
def build_deck_from_markdown(
    markdown: str,
    output_path: str = "",
    theme: str = "",
    prefs: Optional[dict] = None,
    required_items: Optional[list] = None,
    expected_slides: int = 0,
) -> dict:
    """Build and check a .pptx from Markdown: the easiest way to make a deck.

    '# Title' is the cover, each '## Takeaway sentence' a slide, '-' lines
    bullets; ```chart, ```table and ```kpi blocks take CSV
    (references/markdown-format.md). Returns what build_deck returns.

    Args:
        markdown: The deck as Markdown.
        output_path: Where to save the .pptx. Leave empty, or give just a file
            name, to save in GAIA's documents folder.
        theme: light, dark or minimal; empty uses the saved preference.
        prefs: Optional style the user asked for in this request.
        required_items: Facts the request needs visible on the slides; checked.
        expected_slides: The slide count the user asked for; 0 skips the check.
            A count the user named always applies: on a count error, change the slides.
    """
    if "\n" not in markdown and "\\n" in markdown:
        # The model escaped its line breaks twice; the deck is one line otherwise.
        markdown = markdown.replace("\\n", "\n")
    try:
        count = _hold_slide_count(_whole(expected_slides))
        outline = _md.outline_from_markdown(markdown, count or None)
    except OutlineError as exc:
        return _error(exc)
    return _build_from(outline, output_path, theme, "", prefs, required_items)


@tool(display_label="Checking slide deck", timeout=RENDER_TIMEOUT_S + 90)
def verify_deck(
    path: str,
    expected_slides: int = 0,
    require_native_charts: bool = True,
    require_notes: bool = False,
    render: bool = True,
    render_dir: str = "",
    outline: Optional[dict] = None,
    required_items: Optional[list] = None,
) -> dict:
    """Check a saved .pptx: slide count, required items, overflow, overlaps, fonts, charts, sources, notes.

    Works on any deck; renders with LibreOffice when it is installed.

    Args:
        path: Absolute path of the .pptx to check.
        expected_slides: The slide count the user asked for; 0 skips the check.
        require_native_charts: Flag pictures of charts where charts belong.
        require_notes: Flag slides without speaker notes.
        render: Render with LibreOffice (slower, about 10-30 s).
        render_dir: Optional existing folder to save slide PNGs in, to look at.
        outline: The outline the deck was built from, to compare chart values.
        required_items: Facts the request needs visible on slides (not only in notes).

    Returns:
        {"status": "pass"|"fail", "issues": [{slide, check, severity, fix}],
        "slides_overview", ...}. Fix every error, rebuild, check again.
    """
    try:
        deck = _readable(path, "path")
        normalized = normalize_outline(outline) if outline else None
        png_dir = None
        if render_dir:
            png_dir = _absolute(render_dir, "render_dir")
            if not png_dir.is_dir():
                raise OutlineError(
                    [
                        f"render_dir {png_dir} does not exist; give an existing folder or leave it empty."
                    ]
                )
            png_dir = check_write(png_dir / "slide_01.png").parent
        count = _hold_slide_count(_whole(expected_slides))
        renderer = _renderer() if render else None
        report = _verify_engine().verify_deck(
            str(deck),
            expected_slides=count or None,
            require_native_charts=require_native_charts,
            outline=normalized,
            require_notes=require_notes,
            render=render,
            render_dir=str(png_dir) if png_dir else None,
            prefs=_saved_prefs(),
            renderer=renderer,
            required_items=required_items or None,
        )
        if render and renderer is None:
            report["render"] = {
                "rendered": False,
                "reason": "LibreOffice (soffice) is not installed, so slides were not rendered; overflow was estimated from font metrics.",
            }
        return report
    except OutlineError as exc:
        return _error(exc)
    except SkillPermissionError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        log.exception("verify_deck failed")
        return {
            "status": "error",
            "error": f"Could not check {path}: {type(exc).__name__}: {exc}",
        }


@tool(display_label="Saving slide as an image", timeout=RENDER_TIMEOUT_S + 60)
def export_slide_image(
    deck_path: str,
    slide: int = 1,
    image_format: str = "png",
    output_path: str = "",
    width: int = 1920,
) -> dict:
    """Save a built slide as a PNG (default) or JPEG, only when the user asks for an image.

    Build the deck first, then export it. Never draw a slide with image
    generation: this is the real slide, text and charts as built. A deck
    request without an image ask needs no export.

    Args:
        deck_path: The .pptx to draw, from a build result's path.
        slide: Which slide, from 1; 0 saves every slide.
        image_format: png, or jpeg if the user asked for it.
        output_path: Where to save the image (one slide); empty saves beside the deck.
        width: Image width in pixels; the height follows the slide.
    """
    try:
        deck = _readable(deck_path, "deck_path")
        fmt = _image_engine().image_format(image_format)
        renderer = _renderer()
        if renderer is None:
            raise OutlineError(
                [
                    "LibreOffice is not installed, so the slide cannot be drawn as an "
                    f"image. The deck at {deck} is ready; install LibreOffice "
                    "(https://www.libreoffice.org/download/) to export images."
                ]
            )
        pdf = renderer(deck)
        total = _image_engine().page_count(pdf)
        n = _whole(slide)
        if n > total:
            raise OutlineError([f"slide {n} does not exist; the deck has {total}."])
        pages = list(range(1, total + 1)) if n == 0 else [n]
        ext = "jpg" if fmt == "jpeg" else "png"
        if output_path and len(pages) == 1:
            outs = [_absolute(output_path, "output_path")]
            if outs[0].suffix.lower().lstrip(".") not in ("png", "jpg", "jpeg"):
                outs = [outs[0].with_suffix(f".{ext}")]
        else:
            outs = [deck.with_name(f"{deck.stem} - slide {k}.{ext}") for k in pages]
        outs = [check_write(o) for o in outs]
        images = _image_engine().save_pages(
            pdf, pages, outs, fmt, max(320, min(_whole(width) or 1920, 4096))
        )
        return {
            "status": "ok",
            "format": fmt,
            "images": images,
            "next": "Give the user the image path; it shows the slide exactly as built.",
        }
    except (OutlineError, SkillPermissionError) as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        log.exception("export_slide_image failed")
        return {
            "status": "error",
            "error": f"Could not save {deck_path} as an image: {type(exc).__name__}: {exc}",
        }


def _edit(deck_path: str, run) -> dict:
    try:
        deck = _readable(deck_path, "deck_path")
        check_write(deck)
        return run(str(deck), _saved_prefs())
    except OutlineError as exc:
        return _error(exc)
    except SkillPermissionError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        log.exception("slide edit failed")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


@tool(display_label="Adding slides")
def add_slide(deck_path: str, slide: dict, position: int = 0) -> dict:
    """Add one slide (or a list of slides) to an existing deck, in its theme.

    Use it to build a long deck in several small calls. The slide object has
    the same fields as one entry of build_deck's outline.

    Args:
        deck_path: Absolute path of the .pptx to change.
        slide: One slide object, or {"slides": [...]} for several.
        position: 1-based position to insert at; 0 appends at the end.
    """
    specs = (
        slide.get("slides")
        if isinstance(slide, dict) and isinstance(slide.get("slides"), list)
        else slide
    )
    for p in _input_paths({"slides": specs if isinstance(specs, list) else [specs]}):
        try:
            _readable(p, "image")
        except OutlineError as exc:
            return _error(exc)
    return _edit(
        deck_path,
        lambda d, prefs: _build_engine().add_slide(d, specs, position or None, prefs),
    )


@tool(display_label="Replacing a slide")
def update_slide(deck_path: str, index: int, slide: dict) -> dict:
    """Replace slide number `index` of an existing deck with a new slide.

    Args:
        deck_path: Absolute path of the .pptx to change.
        index: 1-based number of the slide to replace.
        slide: The new slide object (same fields as a build_deck outline entry).
    """
    for p in _input_paths({"slides": [slide]}):
        try:
            _readable(p, "image")
        except OutlineError as exc:
            return _error(exc)
    return _edit(
        deck_path,
        lambda d, prefs: _build_engine().update_slide(d, index, slide, prefs),
    )


@tool(display_label="Listing deck blueprints")
def list_blueprints() -> dict:
    """List the deck blueprints (storyline templates) and their slots.

    Returns:
        {"blueprints": [{name, use_for, default_slides, slides, slots}]}.
    """
    return {"blueprints": _bp.list_blueprints()}


@tool(display_label="Planning deck from blueprint")
def outline_from_blueprint(
    name: str, slide_count: int = 0, slots: Optional[dict] = None, variant: str = ""
) -> dict:
    """Make an outline from a blueprint, scaled to exactly slide_count slides.

    Unfilled slots come back as TODO slides; fill them (pass slots again, or
    edit the outline) before building — verify_deck fails on any TODO.

    Args:
        name: Blueprint name from list_blueprints, e.g. quarterly_review.
        slide_count: Slides wanted; 0 uses the blueprint's default.
        slots: Optional {slot_id: {slide fields}}; use "id#2" for a slot's 2nd copy.
        variant: Optional variant, e.g. 2x2, pyramid, cycle for one_slide_framework.

    Returns:
        {"outline", "slots": [{slide, slot, type, filled, guidance}], "todo", "dropped"}.
    """
    try:
        return _bp.outline_from_blueprint(
            name, slide_count or None, slots, variant or None
        )
    except OutlineError as exc:
        return _error(exc)


@tool(display_label="Reading deck Markdown")
def deck_outline_from_markdown(markdown: str) -> dict:
    """Turn deck Markdown into a build_deck outline without building it.

    Same format as build_deck_from_markdown (references/markdown-format.md).

    Args:
        markdown: The Markdown text.

    Returns:
        {"status": "ok", "outline": {...}, "slides": N}.
    """
    try:
        outline = _md.outline_from_markdown(markdown)
        return {"status": "ok", "outline": outline, "slides": len(outline["slides"])}
    except OutlineError as exc:
        return _error(exc)


@tool(display_label="Saving deck preferences")
def update_deck_prefs(feedback: str = "", changes: Optional[dict] = None) -> dict:
    """Save the user's deck style preferences from their feedback, for future decks.

    Call it when the user gives style feedback ("less text", "use our blue
    #1F4E79", "no speaker notes", "dark theme"). Then tell them what was saved.

    Args:
        feedback: The user's words; clear requests are turned into preferences.
        changes: Optional explicit preference changes (keys as in preferences.schema.json).

    Returns:
        {"saved": bool, "preferences": {...}, "changed": {...}, "note"}.
    """
    try:
        wanted = _style.merge_prefs(
            dict(changes or {}), _style.prefs_from_feedback(feedback)
        )
        if not wanted:
            return {
                "saved": False,
                "changed": {},
                "preferences": _saved_prefs(),
                "note": "Nothing in the feedback maps to a saved preference; apply it to this deck only.",
            }
        problems = _style.validate_prefs(wanted)
        if problems:
            return {
                "status": "error",
                "error": "; ".join(problems),
                "problems": problems,
            }
        result = preferences().update(wanted)
        return {
            "saved": bool(result.saved),
            "changed": wanted,
            "preferences": result.preferences,
            "note": getattr(result, "note", "") or "",
        }
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        log.exception("update_deck_prefs failed")
        return {"saved": False, "error": f"{type(exc).__name__}: {exc}"}
