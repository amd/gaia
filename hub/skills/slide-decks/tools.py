# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``slide-decks`` skill.

Thin wrappers over the :mod:`slidedeck` engine in this folder. Every path is
checked through :mod:`gaia.skills.context` — reads against the agent's allowed
folders, writes with ``check_write`` (which asks before overwriting) — and the
LibreOffice render runs through ``run_program`` in the call's temporary
folder. Saved preferences come from the skill's own preference store.

A build makes the deck whenever the outline has a slide. What was read, drawn
or left out differently from what was sent, and every quality finding, comes
back in the result's ``warnings``, each with the call that fixes it. A tool
returns ``{"status": "error", "error": ..., "problems": [...]}`` only when no
file can be made (no slides, no folder to write in) or a path is outside the
folders this chat may use.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from slidedeck import blueprints as _bp
from slidedeck import data as _data
from slidedeck import markdown as _md
from slidedeck import style as _style
from slidedeck.outline import (
    OutlineError,
    canonical_fields,
    load_outline,
    normalize_outline,
)

from gaia.agents.base.tools import tool
from gaia.agents.tools.search_scope import (
    is_broad_root,
    path_validator_of,
    search_roots,
    session_workspace,
)
from gaia.logger import get_logger
from gaia.security import MISSING_REFUSAL_PREFIX
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

#: The smallest call that builds a deck, quoted in errors that have no other way on.
_MINIMAL_CALL = "build_deck_from_markdown(markdown='## Slide title\\n- First point\\n- Second point')"


def _error(exc: Exception) -> Dict[str, Any]:
    problems = getattr(exc, "problems", None) or [str(exc)]
    return {"status": "error", "error": str(exc), "problems": problems}


#: Builds in a row whose check found errors, per agent; after two the model is told to stop.
_FAILED_IN_A_ROW: Dict[int, int] = {}
_MAX_FAILED_IN_A_ROW = 2


def _owner() -> str:
    """This chat's mark on the decks it builds; "" outside a skill call."""
    try:
        agent = current().agent
    except SkillContextError:
        return ""
    token = getattr(agent, "_slide_decks_owner", "")
    if not token:
        token = uuid.uuid4().hex
        setattr(agent, "_slide_decks_owner", token)
    return token


def _request(call: Any) -> str:
    """The user message the agent is answering, or "" outside a turn.

    Without the memory context the agent prepends: that is earlier chats'
    text, not what the user asked now.
    """
    agent = call.agent
    return str(
        getattr(agent, "_original_user_input", None)
        or getattr(agent, "_current_query", "")
        or ""
    )


def _sources(call: Any) -> List[str]:
    """What the user said and what other tools returned this conversation.

    This skill's own tool results, its loaded instructions and reads of its
    folder are left out: their example values are not facts.
    """
    request = _request(call)
    seen = getattr(call.agent, "seen_sources", None)
    if seen is None:
        return [request] if request else []
    own = f"{call.skill_name}/"

    def norm(text: str) -> str:
        return re.sub(r"[\\/]+", "/", text).lower()

    folder = norm(str(getattr(call.skill, "directory", "") or ""))
    out = []
    for source, args, text in seen():
        if source.startswith(own) or source == "load_skill":
            continue
        if folder and folder in norm(json.dumps(args, default=str)):
            continue
        out.append(text)
    return out


def _user_messages(call: Any) -> List[str]:
    """What the user said this conversation, oldest first."""
    request = _request(call)
    seen = getattr(call.agent, "seen_sources", None)
    if seen is None:
        return [request] if request else []
    return [text for source, _args, text in seen() if source == "user"]


def _agent_key() -> int:
    try:
        return id(current().agent)
    except SkillContextError:
        return 0


def _whole(value: Any) -> int:
    """A positive whole number, or 0 for anything else."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------
# Content findings: reported in the result's warnings, never a refusal
# ----------------------------------------------------------------------

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


def _shown(slides: List[Any]) -> List[Dict[str, Any]]:
    """What each slide puts in front of the audience: its fields without the notes."""
    return [
        {k: v for k, v in s.items() if k != "notes"}
        for s in slides
        if isinstance(s, dict)
    ]


def _hidden_in_notes(slides: List[Any], said: List[str]) -> Optional[str]:
    """Numbers the user gave that are only in the speaker notes, not on a slide."""
    asked = {v for text in said for _t, v in _numbers_in(text)}
    on_slides = set()
    in_notes: Dict[float, str] = {}
    for s in slides:
        if not isinstance(s, dict):
            continue
        shown: List[Tuple[str, float, str]] = []
        _shown_numbers(s, "body", shown)
        on_slides |= {v for _t, v, _w in shown}
        for t, v in _numbers_in(str(s.get("notes") or "")):
            in_notes.setdefault(v, t)
    hidden = sorted(t for v, t in in_notes.items() if v in asked and v not in on_slides)
    if not hidden:
        return None
    return (
        f"{', '.join(hidden)} from the user's request is only in the speaker "
        "notes, where the audience does not see it. To show it, replace that "
        "slide with update_slide and put the number in its bullets or title."
    )


def _unsourced_numbers(slides: List[Any], call: Any, said: List[str]) -> Optional[str]:
    """Numbers on the slides that nothing the user said or a tool returned contains."""
    known = [v for text in _sources(call) for _t, v in _numbers_in(text)]
    known += _derived([v for text in said for _t, v in _numbers_in(text)])
    this_year = datetime.date.today().year
    found: List[str] = []
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            continue
        shown: List[Tuple[str, float, str]] = []
        if s.get("data_read_from"):
            # Rows this tool read from the user's file are sourced by that file.
            s = {k: v for k, v in s.items() if k != "rows"}
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
        if bad:
            found.append(f"slide {i} '{s.get('title') or ''}': {', '.join(bad)}")
    if not found:
        return None
    return (
        "These numbers are on the slides but in nothing the user said or a tool "
        f"returned this conversation: {'; '.join(found)}. The deck was built "
        "with them. Keep each one that came from the user or a source; replace "
        "an estimate with update_slide (label it 'Illustrative'), or tell the "
        "user which figures are estimates."
    )


def _figures_from_user(slides: List[Any]) -> List[int]:
    """Slides whose every number is one the user typed or one derived from them.

    Those figures have no file or system to cite, so verify does not ask for a
    Source line the model would have to make up.
    """
    try:
        call = current()
    except SkillContextError:
        return []
    if not _request(call):
        return []
    given = [v for text in _user_messages(call) for _t, v in _numbers_in(text)]
    known = given + _derived(given)
    out = []
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            continue
        shown: List[Tuple[str, float, str]] = []
        _shown_numbers({k: v for k, v in s.items() if k != "source"}, "body", shown)
        if shown and all(_grounded(v, t, known) for t, v, _w in shown):
            out.append(i)
    return out


#: "9 of 14", "9 out of 14", "9/14": a count the reader needs its base for.
_PART_OF = re.compile(
    r"(?<![\w.])(\d[\d,]*)\s*(?:of|out of|/)\s*(\d[\d,]*)(?![\w.%])", re.I
)

#: Only the spelled-out form from the user: "3/4" there may be a date.
_PART_OF_WORDS = re.compile(
    r"(?<![\w.])(\d[\d,]*)\s+(?:of|out of)\s+(\d[\d,]*)(?![\w.%])", re.I
)


def _dropped_bases(slides: List[Any], said: List[str]) -> Optional[str]:
    """A user's "7 of 14" shown on a slide as a bare 7, so the base is lost."""
    shown_text = json.dumps(_shown(slides), ensure_ascii=False)
    kept = set(_PART_OF.findall(shown_text))
    bare = {t for t, _v in _numbers_in(shown_text)}
    lost = []
    for text in said:
        for a, b in _PART_OF_WORDS.findall(text):
            if (a, b) not in kept and a in bare and a != b:
                lost.append(f"{a} of {b}")
    if not lost:
        return None
    lost = list(dict.fromkeys(lost))
    return (
        f"The user gave {', '.join(lost)}, but the slides show the count without "
        f"its base. To fix, replace that slide with update_slide and write it as "
        f"the user did (e.g. '{lost[0]}')."
    )


#: Pictographs and flags: many slide viewers and the image export draw them as empty boxes.
_EMOJI = re.compile("[🀀-🫿☀-➿🇦-🇿️]")


def _stray_emoji(slides: List[Any], request: str) -> Optional[str]:
    """Emoji on the slides that the user did not use themselves."""
    shown = json.dumps(_shown(slides), ensure_ascii=False)
    stray = sorted({e for e in _EMOJI.findall(shown) if e not in request and e != "️"})
    if not stray:
        return None
    return (
        f"The slides use the emoji {' '.join(stray)}: many viewers and the image "
        "export draw them as empty boxes. To fix, replace those slides with "
        "update_slide, without the emoji."
    )


def _content_warnings(slides: List[Any]) -> List[str]:
    """What the slides say that the user may not want, checked against this chat.

    Empty outside a chat turn: a library call has no request to check against.
    """
    try:
        call = current()
    except SkillContextError:
        return []
    request = _request(call)
    if not request:
        return []
    said = _user_messages(call)
    found = (
        _hidden_in_notes(slides, said),
        _unsourced_numbers(slides, call, said),
        _dropped_bases(slides, said),
        _stray_emoji(slides, request),
    )
    return [x for x in found if x]


def _count_warning(result: Dict[str, Any], expected: int) -> Optional[str]:
    """Actual against expected slides, with the call that closes the gap."""
    built = int(result.get("slides") or 0)
    if not expected or built == expected:
        return None
    head = (
        f"The deck has {built} slide{'s' if built != 1 else ''} and "
        f"expected_slides was {expected}; every slide counts, a cover included. "
        "It was built as written. "
    )
    if built < expected:
        return head + (
            f"To reach {expected}, add each missing slide with "
            f"add_slide(deck_path='{result.get('path')}', slide={{\"type\": "
            '"bullets", "title": "...", "bullets": ["..."]})'
            "; otherwise tell the user the count."
        )
    return head + (
        f"If the user asked for exactly {expected}, build again with "
        f"{expected} '## ' slide{'s' if expected != 1 else ''} (merge slides "
        "that make one point; a '# ' cover line adds a slide); otherwise tell "
        "the user the count."
    )


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


# ----------------------------------------------------------------------
# Paths: every read and write goes through gaia.skills.context
# ----------------------------------------------------------------------


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
                f"{what} must be an absolute path (got {path!r}). Call again with "
                f"the full path, e.g. {what}='C:/Users/me/Documents/{p.name}'."
            ]
        )
    return p


def _readable(path: str, what: str) -> Path:
    resolved = check_read(_absolute(path, what))
    if not resolved.is_file():
        raise OutlineError(
            [
                f"{what} {resolved} does not exist. Call again with the path of "
                "an existing file."
            ]
        )
    return resolved


def _built_deck(path: str, what: str) -> Path:
    """A deck to check, draw or edit; a missing one names the call that makes it."""
    try:
        return _readable(path, what)
    except OutlineError as exc:
        raise OutlineError(
            [
                f"{exc} Use the 'path' a build result returned, or build the "
                "deck first with " + _MINIMAL_CALL
            ]
        ) from exc


def _usable_file(path: Any, what: str) -> Tuple[Optional[str], str]:
    """(resolved path, "") for a file this chat may read, else (None, why not).

    A file that is not there costs only itself; the caller builds without it
    and says so.

    Raises:
        SkillPermissionError: the file is outside the folders this chat may read.
    """
    if not isinstance(path, str) or not path.strip():
        return None, f"{what} was not given as a file path"
    p = Path(path.strip()).expanduser()
    if not p.is_absolute():
        return None, f"{what} {path!r} is not an absolute path"
    try:
        resolved = check_read(p)
    except SkillPermissionError as exc:
        if str(exc).startswith(MISSING_REFUSAL_PREFIX):
            return None, f"{what} {p} does not exist"
        raise SkillPermissionError(
            f"{what} {p} is outside the folders this chat may read, so nothing "
            f"was built ({exc}). Call again without that file, or ask the user "
            "to allow its folder and call again."
        ) from exc
    if not resolved.is_file():
        return None, f"{what} {resolved} does not exist"
    return str(resolved), ""


#: The keys a slide's picture may arrive under once its fields are canonical.
_IMAGE_KEYS = ("path", "image", "src")


def _slide_object(raw: Any) -> Any:
    """A slide sent as JSON text, as the object it spells; anything else unchanged."""
    if isinstance(raw, str) and raw.lstrip().startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _scoped_slides(slides: List[Any]) -> List[Any]:
    """*slides* with every picture path checked against the folders this chat may read.

    Field names are made canonical first and a slide sent as JSON text is
    read, so ``image_path``, ``file`` and every other spelling are checked
    like ``path``. The engine is handed only the resolved path that passed. A
    picture that is not there is taken off its slide with the reason in
    ``image_problem``; the engine builds the slide without it and reports it.

    Raises:
        SkillPermissionError: a picture is outside the folders this chat may read.
    """
    out = []
    for i, raw in enumerate(slides, 1):
        raw = _slide_object(raw)
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        slide = canonical_fields(raw)
        given = [slide[k] for k in _IMAGE_KEYS if slide.get(k)]
        for key in _IMAGE_KEYS:
            slide.pop(key, None)
        slide.pop("image_problem", None)
        if given:
            first = given[0]
            if isinstance(first, dict):
                first = first.get("path") or first.get("src") or first.get("file")
            path, problem = _usable_file(first, f"slide {i}: image")
            if path:
                slide["path"] = path
            else:
                slide["image_problem"] = problem
        _read_data_file(slide, i)
        out.append(slide)
    return out


_DATA_TYPES = (".csv", ".tsv", ".xlsx", ".xlsm")


def _read_data_file(slide: Dict[str, Any], i: int) -> None:
    """Replace a chart or table slide's ``data_path`` with the rows read from it.

    The file is checked against the folders this chat may read, then read
    here, so a chart never needs another tool to get its series. A file or
    range that cannot be read leaves ``data_problem``: the slide is built as
    text and the warning says what to pass.

    Raises:
        SkillPermissionError: the file is outside the folders this chat may read.
    """
    given = slide.pop("data_path", None)
    slide.pop("data_problem", None)
    data = slide.get("data")
    if (
        not given
        and isinstance(data, str)
        and "\n" not in data
        and data.strip().lower().endswith(_DATA_TYPES)
    ):
        given = slide.pop("data")
    if not given:
        return
    slide.setdefault("type", "chart")
    if any(slide.get(k) is not None for k in ("rows", "csv", "series", "values")):
        # Values sent with the call win over the file they were copied from.
        return
    what = f"slide {i}: data_path"
    path, problem = _usable_file(given, what)
    if not path:
        slide["data_problem"] = problem + "."
        return
    try:
        rows = _data.read_rows(
            path,
            sheet=str(slide.get("sheet") or ""),
            cell_range=str(slide.get("range") or ""),
            series_in=str(slide.get("series_in") or ""),
        )
    except _data.DataError as exc:
        slide["data_problem"] = f"{what}: {exc}"
        return
    except (OSError, ValueError, KeyError) as exc:
        # openpyxl raises these for a file that is not the workbook its name says.
        slide["data_problem"] = (
            f"{what}: {Path(path).name} could not be read "
            f"({type(exc).__name__}: {exc})."
        )
        return
    slide["rows"] = rows
    slide["data_read_from"] = Path(path).name
    slide.pop("columns", None)
    if not slide.get("source"):
        where = [Path(path).name, slide.get("sheet"), slide.get("range")]
        slide["source"] = ", ".join(str(x) for x in where if x)


def _slide_specs(slide: Any) -> List[Any]:
    """The slides an edit was given: one object, a list, {"slides": [...]} or JSON text of any."""
    if isinstance(slide, str):
        try:
            slide = json.loads(slide)
        except json.JSONDecodeError as exc:
            raise OutlineError(
                [
                    f"slide is not valid JSON ({exc.msg}). Pass an object, e.g. "
                    'slide={"type": "bullets", "title": "Next steps", '
                    '"bullets": ["First", "Second"]}.'
                ]
            ) from exc
    if isinstance(slide, dict) and isinstance(slide.get("slides"), list):
        slide = slide["slides"]
    return _scoped_slides(slide if isinstance(slide, list) else [slide])


_FILE_PREFS = ("template_path", "logo_path")
_TEMPLATE_TYPES = (".pptx", ".potx", ".pptm", ".potm")


def _usable_theme(theme: Any, warnings: List[str]) -> str:
    """*theme* if it names a theme, else "" with a warning naming the themes."""
    name = str(theme or "").strip() if isinstance(theme, str) else ""
    if not name:
        return ""
    if _style.THEME_ALIASES.get(name.lower(), name.lower()) in _style.THEMES:
        return name
    themes = list(_style.THEMES)
    warnings.append(
        f"theme {name!r} is not one of {', '.join(themes)}, so the deck was "
        "built with the saved or default theme (this result's 'theme'). To "
        f"change it, build again with theme='{themes[0]}'"
        + "".join(f", '{t}'" for t in themes[1:-1])
        + f" or '{themes[-1]}'."
    )
    return ""


def _usable_template(path: Any, what: str, warnings: List[str]) -> Optional[str]:
    """A template this chat may read, or None with a warning; the theme is used instead."""
    if not path:
        return None
    resolved, problem = _usable_file(path, what)
    if resolved and Path(resolved).suffix.lower() not in _TEMPLATE_TYPES:
        resolved, problem = None, f"{what} {path} is not a .pptx or .potx file"
    if not resolved:
        warnings.append(
            f"{problem}, so the deck was built with the skill's theme instead. "
            f"To use a template, build again with {what}=<absolute path of a "
            ".pptx or .potx file>."
        )
    return resolved


def _usable_style(layer: Any, what: str, warnings: List[str]) -> Dict[str, Any]:
    """*layer* (prefs or an outline's style) without the settings that cannot be used.

    Each dropped setting is named in *warnings* with its valid values; the
    deck is built from the rest.
    """
    if not layer:
        return {}
    if not isinstance(layer, dict):
        warnings.append(
            f"{what} was ignored: it must be an object, e.g. "
            '{"theme": "dark", "density": "light"}.'
        )
        return {}
    out: Dict[str, Any] = {}
    for key, value in layer.items():
        problems = _style.validate_prefs({key: value})
        if problems:
            warnings.append(
                f"{what}: {' '.join(problems)} That setting was ignored and the "
                "deck built without it."
            )
            continue
        if key in _FILE_PREFS and value:
            if key == "template_path":
                value = _usable_template(value, f"{what} {key}", warnings)
            else:
                value, problem = _usable_file(value, f"{what} {key}")
                if problem:
                    warnings.append(f"{problem}, so the deck was built without it.")
            if not value:
                continue
        out[key] = value
    return out


def _deck_name(outline: Dict[str, Any]) -> str:
    slides = outline.get("slides") or []
    first = slides[0] if slides and isinstance(slides[0], dict) else {}
    title = str(outline.get("title") or first.get("title") or "")
    stem = re.sub(r"[^A-Za-z0-9]+", " ", title).strip()[:60].strip()
    return (stem or "Slide deck") + ".pptx"


def _in_skill_folder(path: Path) -> bool:
    resolved = path.resolve()
    return _SKILL_DIR == resolved or _SKILL_DIR in resolved.parents


def _default_folders(call: Any) -> List[Path]:
    """Where a deck with no folder goes, best first.

    The working folder, then a folder this chat was allowed, then the user's
    Documents; a whole home folder or drive comes last.
    """
    agent = call.agent
    roots = search_roots(agent) if path_validator_of(agent) is not None else []
    work = session_workspace(agent)
    folders = [
        *([work] if work else []),
        *[r for r in roots if not is_broad_root(r)],
        Path.home() / "Documents",
        *[r for r in roots if is_broad_root(r)],
    ]
    return list(dict.fromkeys(Path(f).resolve() for f in folders))


def _may_write(call: Any, target: Path) -> bool:
    """True when *target* may be written; never asks the user."""
    validator = path_validator_of(call.agent)
    if validator is None:
        return True
    allowed, _why = validator.validate_write(str(target), prompt_user=False)
    return bool(allowed)


def _fresh_name(p: Path, notes: List[str]) -> Path:
    """*p*, or "name (2).pptx" when a file this chat did not build has that name."""
    owner = _owner()
    n = 1
    found = p
    while found.exists() and not (owner and _build_engine().deck_owner(found) == owner):
        n += 1
        found = p.with_name(f"{p.stem} ({n}){p.suffix}")
    if found != p:
        notes.append(
            f"A file named '{p.name}' already exists that this chat did not "
            f"build, so it was kept and this deck is saved as '{found.name}'."
        )
    return found


def _writable_deck(
    path: Any, outline: Dict[str, Any], warnings: List[str], notes: List[str]
) -> Path:
    """Where to save the deck, checked for writing.

    An absolute *path* is used as given, its folder created if missing. With
    no path, or only a file name, the deck goes in the first default folder
    this chat may write to, and *notes* says which.

    Raises:
        OutlineError: the skill's own folder, or no folder to write in.
        SkillPermissionError: *path* is outside the folders this chat may write.
    """
    given = str(path or "").strip()
    wanted = Path(given).expanduser() if given else Path(_deck_name(outline))
    if wanted.suffix.lower() != ".pptx":
        if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", wanted.suffix):
            warnings.append(
                f"output_path ended in '{wanted.suffix}', but a deck is a .pptx "
                f"file, so it was saved as '{wanted.stem}.pptx'."
            )
            wanted = wanted.with_suffix(".pptx")
        else:
            wanted = wanted.with_name(wanted.name + ".pptx")
    if wanted.is_absolute():
        if _in_skill_folder(wanted):
            raise OutlineError(
                [
                    f"output_path {wanted} is inside the skill's own folder. Call "
                    "again with output_path='' to save in the working folder, or "
                    "with a path in the user's own folders."
                ]
            )
        try:
            out = check_write(wanted)
        except SkillPermissionError as exc:
            raise SkillPermissionError(
                f"output_path {wanted} cannot be written, so nothing was built "
                f"({exc}). Call again with output_path='' to save in the working "
                "folder, or with a path in a folder the user has allowed."
            ) from exc
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    call = current()
    tried = []
    for folder in _default_folders(call):
        target = folder / wanted.name
        tried.append(str(folder))
        if (
            not folder.is_dir()
            or _in_skill_folder(target)
            or not _may_write(call, target)
        ):
            continue
        target = _fresh_name(target, notes)
        try:
            out = check_write(target)
        except SkillPermissionError as exc:
            log.warning("slide-decks: cannot save to %s: %s", target, exc)
            continue
        notes.append(
            f"No folder was named, so the deck was saved in {out.parent}. Tell "
            "the user the full path."
        )
        return out
    raise OutlineError(
        [
            "No folder this chat may write to was found (tried "
            f"{', '.join(tried) or 'none'}). Call again with output_path set to "
            "an absolute .pptx path in a folder the user has allowed, e.g. "
            "output_path='C:/Users/me/Documents/deck.pptx'."
        ]
    )


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


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------


@tool(display_label="Building slide deck")
def build_deck(
    outline: dict,
    output_path: str = "",
    theme: str = "",
    template_path: str = "",
    prefs: Optional[dict] = None,
    expected_slides: int = 0,
    expect: Optional[dict] = None,
) -> dict:
    """Build a .pptx from a JSON slide outline and check it; never hand-write python-pptx.

    The outline is {"slides": [...]}; each slide has a "type", a "title" and
    the fields in the skill's references/outline-json.md. Chart and table
    values go in "rows" (header row first) or "data_path" + "range" for a
    .csv/.xlsx. `path` is the file; `warnings` give each fix.

    Example: build_deck(outline={"slides": [{"type": "chart", "title":
    "West grew", "rows": [["Q", "West"], ["Q1", 4.1]]}]})

    Args:
        outline: The slide outline object, or its JSON text.
        output_path: Absolute .pptx path. Leave empty to save in the working
            folder; the result's `path` says where.
        theme: light, dark or minimal; empty uses the saved preference.
        template_path: Optional .pptx/.potx whose layouts and theme to use.
        prefs: Optional style the user asked for in this request (density, colors...).
        expected_slides: The slide count the user asked for, cover included;
            a different count is built and reported in `warnings`. 0 skips it.
        expect: Counts the request asks for, e.g. {"min_charts": 2,
            "min_tables": 1, "slides": 8}; a deck short of it fails.
    """
    return _build_from(
        outline, output_path, theme, template_path, prefs, None, expected_slides, expect
    )


def _next_step(result: Dict[str, Any], misses: int) -> str:
    path = result.get("path")
    if result.get("status") == "built_with_issues":
        if misses >= _MAX_FAILED_IN_A_ROW:
            return (
                f"Stop building. The deck is saved at {path}: give the user "
                "that path and tell them which errors in verify.issues remain; "
                "never call it verified."
            )
        return (
            f"The deck is saved at {path}, and its check found errors "
            "(verify.issues, severity 'error'). Fix each with "
            f"update_slide(deck_path='{path}', index=<slide number>, "
            "slide={...}), or build once more with them fixed. Slide numbers "
            "are the ones in slides_built: they start at 1 and the cover "
            "counts. If you stop, give the user the path and say which errors "
            "remain; never call it verified."
        )
    if result.get("warnings"):
        return (
            f"The deck is saved at {path}. Read `warnings`: make the call a "
            "warning names if the user would want it fixed, otherwise tell the "
            "user about it. Then report the path and describe the deck only "
            "from slides_built."
        )
    return (
        f"The deck is saved at {path} and passed its check. Report it to the "
        "user now, describing it only from slides_built; build again only if "
        "they ask for a change."
    )


def _build_from(
    outline: Any,
    output_path: Any,
    theme: Any,
    template_path: Any,
    prefs: Optional[dict],
    required_items: Optional[list] = None,
    expected_slides: Any = 0,
    expect: Any = None,
) -> dict:
    warnings: List[str] = []
    notes: List[str] = []
    try:
        data = dict(load_outline(outline))
        if expect:
            data["expect"] = expect
        data["slides"] = _scoped_slides(data["slides"])
        slides = data["slides"]
        count = _whole(expected_slides) or _whole(data.get("expected_slides"))
        if count:
            data["expected_slides"] = count
        saved = _usable_style(_saved_prefs(), "saved preference", warnings)
        request = _usable_style(prefs, "prefs", warnings)
        style = _usable_style(data.get("style"), "the outline's style", warnings)
        if isinstance(data.get("theme"), dict):
            # Only its files are checked here; the engine reads the rest.
            files = {k: v for k, v in data["theme"].items() if k in _FILE_PREFS}
            data["theme"] = {
                **{k: v for k, v in data["theme"].items() if k not in _FILE_PREFS},
                **_usable_style(files, "the outline's theme", warnings),
            }
        elif data.get("theme"):
            data["theme"] = _usable_theme(data["theme"], warnings) or None
        data["style"] = _style.merge_prefs(style, request)
        theme = _usable_theme(theme, warnings)
        template = _usable_template(template_path, "template_path", warnings)
        data["figures_from_user"] = _figures_from_user(slides)
        if required_items:
            data["required_items"] = list(
                dict.fromkeys([*(data.get("required_items") or []), *required_items])
            )
        data["owner"] = _owner()
        out = _writable_deck(output_path, data, warnings, notes)
        result = _build_engine().build_deck(
            data, str(out), theme or None, template, saved
        )
        result["warnings"] = [
            w
            for w in (
                *warnings,
                *(result.get("warnings") or []),
                _count_warning(result, count),
                *_content_warnings(slides),
            )
            if w
        ]
        result["notes"] = [*(result.get("notes") or []), *notes]
        if result.get("status") == "built_with_issues":
            misses = _FAILED_IN_A_ROW.get(_agent_key(), 0) + 1
            _FAILED_IN_A_ROW[_agent_key()] = misses
        else:
            misses = 0
            _FAILED_IN_A_ROW.pop(_agent_key(), None)
        result["next"] = _next_step(result, misses)
        result["slides_built"] = _slides_built(data, result)
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
            "problems": [
                f"The deck could not be built: {exc}. If the outline is the "
                "cause, a call this small always builds: " + _MINIMAL_CALL
            ],
        }


@tool(display_label="Building slide deck from Markdown", draft_argument="markdown")
def build_deck_from_markdown(
    markdown: str,
    output_path: str = "",
    theme: str = "",
    prefs: Optional[dict] = None,
    required_items: Optional[list] = None,
    expected_slides: int = 0,
    expect: Optional[dict] = None,
) -> dict:
    """Build and check a .pptx from Markdown: the easiest way to make a deck.

    Each '## Title' line starts a slide and '-' lines are its bullets; an
    optional '# Title' first is a cover (slide 1). A ```chart column block
    of CSV, or 'data:' + 'range:' for a file, is a native chart; ```table
    and ```kpi work alike. Diagrams and other lines are in the skill's
    references/markdown-format.md. `warnings` give each fix.

    Example: build_deck_from_markdown(markdown="## Sales grew 7%\\n- North up 12%")

    Args:
        markdown: The deck as Markdown.
        output_path: Absolute .pptx path. Leave empty to save in the working
            folder; the result's `path` says where.
        theme: light, dark or minimal; empty uses the saved preference.
        prefs: Optional style the user asked for in this request.
        required_items: Facts the request needs visible on the slides; checked.
        expected_slides: The slide count the user asked for, cover included;
            a different count is built and reported in `warnings`. 0 skips it.
        expect: Counts the request asks for, e.g. {"min_charts": 2,
            "min_tables": 1, "slides": 8}; a deck short of it fails.
    """
    markdown = str(markdown or "")
    if "\n" not in markdown and "\\n" in markdown:
        # The model escaped its line breaks twice; the deck is one line otherwise.
        markdown = markdown.replace("\\n", "\n")
    count = _whole(expected_slides)
    try:
        outline = _md.outline_from_markdown(markdown, count or None)
    except OutlineError as exc:
        return _error(
            OutlineError([f"{p} For example: {_MINIMAL_CALL}" for p in exc.problems])
        )
    return _build_from(
        outline, output_path, theme, "", prefs, required_items, count, expect
    )


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
    expect: Optional[dict] = None,
) -> dict:
    """Check a saved .pptx: slide count, required items, charts and tables, overflow, fonts, sources, notes.

    A build already runs this check; call it after add_slide or
    update_slide, or for a deck you did not build. Pass `expect` when the
    request asks for charts or tables. Renders with LibreOffice if installed.

    Example: verify_deck(path="C:/Users/me/review.pptx",
    expect={"min_charts": 2})

    Args:
        path: Absolute path of the .pptx to check.
        expected_slides: The slide count the user asked for, cover included;
            a different count is a warning. 0 skips it.
        require_native_charts: Flag pictures of charts where charts belong.
        require_notes: Flag slides without speaker notes.
        render: Render with LibreOffice (slower, about 10-30 s).
        render_dir: Optional existing folder to save slide PNGs in, to look at.
        outline: The outline the deck was built from, to compare chart values.
        required_items: Facts the request needs visible on slides (not only in notes).
        expect: What the request asks the deck to hold: {"min_charts": n,
            "min_tables": n, "slides": n}; native charts and tables only.

    Returns:
        {"status", "issues": [{slide, check, fix}], "counts"}.
    """
    try:
        deck = _built_deck(path, "path")
        normalized = normalize_outline(outline) if outline else None
        png_dir = None
        if render_dir:
            png_dir = _absolute(render_dir, "render_dir")
            if not png_dir.is_dir():
                raise OutlineError(
                    [
                        f"render_dir {png_dir} does not exist. Call again with "
                        "render_dir='' (no slide images), or an existing folder."
                    ]
                )
            png_dir = check_write(png_dir / "slide_01.png").parent
        renderer = _renderer() if render else None
        report = _verify_engine().verify_deck(
            str(deck),
            expected_slides=_whole(expected_slides) or None,
            require_native_charts=require_native_charts,
            outline=normalized,
            require_notes=require_notes,
            render=render,
            render_dir=str(png_dir) if png_dir else None,
            prefs=_saved_prefs(),
            renderer=renderer,
            required_items=required_items or None,
            expect=expect,
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

    Example: export_slide_image(deck_path="C:/Users/me/Documents/review.pptx", slide=1)

    Args:
        deck_path: The .pptx to draw, from a build result's path.
        slide: Which slide, from 1; 0 saves every slide.
        image_format: png, or jpeg if the user asked for it.
        output_path: Where to save the image (one slide); empty saves beside the deck.
        width: Image width in pixels; the height follows the slide.
    """
    try:
        deck = _built_deck(deck_path, "deck_path")
        fmt = _image_engine().image_format(image_format)
        renderer = _renderer()
        if renderer is None:
            raise OutlineError(
                [
                    "LibreOffice is not installed, so the slide cannot be drawn as an "
                    f"image. The deck at {deck} is ready: give the user that path "
                    "and tell them to install LibreOffice "
                    "(https://www.libreoffice.org/download/) to export images. Do "
                    "not call export_slide_image again."
                ]
            )
        pdf = renderer(deck)
        total = _image_engine().page_count(pdf)
        n = _whole(slide)
        if n > total:
            raise OutlineError(
                [
                    f"slide {n} does not exist; the deck has {total}. Call again "
                    f"with slide=1 to {total}, or slide=0 for every slide."
                ]
            )
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


def _edit_next(result: Dict[str, Any]) -> str:
    """What to do after an edit: which slide changed, and what the edit left open."""
    was = result.get("replaced")
    said = (
        f"Slide {was['index']} ({was['title']!r}, a {was['kind']} slide) was "
        f"replaced by {was['with']!r}. If that was not the slide you meant, "
        "slide_titles lists every slide with its number (from 1, the cover "
        "counts): put it back with update_slide. "
        if was
        else "slide_titles lists every slide with its number (from 1, the "
        "cover counts). "
    )
    if result.get("status") == "built_with_issues":
        return said + (
            "Part of what was sent is not on the slide: read `issues` and "
            "`warnings`, and send it again in a field the slide draws."
        )
    return said + (
        f"Then call verify_deck(path='{result.get('path')}') once the edits "
        'are done; pass expect={"min_charts": n} if the request asks for charts.'
    )


def _edit(deck_path: str, slide: Any, run) -> dict:
    try:
        specs = _slide_specs(slide)
        deck = _built_deck(deck_path, "deck_path")
        check_write(deck)
        warnings: List[str] = []
        prefs = _usable_style(_saved_prefs(), "saved preference", warnings)
        result = run(str(deck), specs, prefs)
        result["warnings"] = [
            *warnings,
            *(result.get("warnings") or []),
            *_content_warnings(specs),
        ]
        result["next"] = _edit_next(result)
        return result
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

    Example: add_slide(deck_path="C:/Users/me/Documents/review.pptx",
    slide={"type": "bullets", "title": "Next steps", "bullets": ["Hire two", "Ship in May"]})

    The result's `slide_titles` lists every slide with its number afterwards.

    Args:
        deck_path: Absolute path of the .pptx to change.
        slide: One slide object, or {"slides": [...]} for several. A chart
            takes its values as "rows" (header row, then one row per label)
            or "data_path" + "range".
        position: The number the new slide gets, counting from 1 with the
            cover as slide 1 (position=2 puts it right after the cover);
            0 appends at the end.
    """
    return _edit(
        deck_path,
        slide,
        lambda d, specs, prefs: _build_engine().add_slide(
            d, specs, _whole(position) or None, prefs
        ),
    )


@tool(display_label="Replacing a slide")
def update_slide(
    deck_path: str, index: int, slide: dict, replace_cover: bool = False
) -> dict:
    """Replace slide number `index` of an existing deck with a new slide.

    Slides are numbered from 1 and the cover is slide 1: use the numbers in
    a build's `slides_built`. The whole slide is replaced, so send every
    field it needs, with its "type"; `replaced` names the old slide.

    Example: update_slide(deck_path="C:/Users/me/Documents/review.pptx",
    index=2, slide={"type": "bullets", "title": "Costs fell 4%",
    "bullets": ["Energy down 9%"]})

    Args:
        deck_path: Absolute path of the .pptx to change.
        index: Number of the slide to replace, from 1; the cover is 1.
        slide: The new slide object (same fields as a build_deck outline entry).
        replace_cover: Pass true only to replace the cover (slide 1) with a
            slide that is not a cover; otherwise that call is refused.
    """
    try:
        number = int(index)
    except (TypeError, ValueError):
        return _error(
            OutlineError(
                [
                    f"index {index!r} is not a slide number. Pass the number of "
                    "the slide to replace, from 1 (the cover is slide 1)."
                ]
            )
        )
    return _edit(
        deck_path,
        slide,
        lambda d, specs, prefs: _build_engine().update_slide(
            d,
            number,
            specs[0] if len(specs) == 1 else specs,
            prefs,
            replace_cover=replace_cover is True
            or str(replace_cover).strip().lower() == "true",
        ),
    )


@tool(display_label="Listing deck blueprints")
def list_blueprints() -> dict:
    """List the deck blueprints (storyline templates) and their slots.

    Example: list_blueprints()

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
    edit the outline), then pass the outline to build_deck.

    Example: outline_from_blueprint(name="project_status", slide_count=6)

    Args:
        name: Blueprint name from list_blueprints.
        slide_count: Slides wanted; 0 uses the blueprint's default.
        slots: Optional {slot_id: {slide fields}}; use "id#2" for a slot's 2nd copy.
        variant: Optional variant a blueprint lists, e.g. 2x2 for one_slide_framework.

    Returns:
        {"outline", "slots": [{slide, slot, type, filled, guidance}], "todo", "dropped"}.
    """
    try:
        return _bp.outline_from_blueprint(
            name, _whole(slide_count) or None, slots, variant or None
        )
    except OutlineError as exc:
        return _error(exc)


@tool(display_label="Reading deck Markdown")
def deck_outline_from_markdown(markdown: str) -> dict:
    """Turn deck Markdown into a build_deck outline without building it.

    Same format as build_deck_from_markdown (references/markdown-format.md).

    Example: deck_outline_from_markdown(markdown="## Sales grew 7%\\n- North up 12%")

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

    Example: update_deck_prefs(feedback="dark theme, less text")

    Args:
        feedback: The user's words; clear requests are turned into preferences.
        changes: Optional explicit changes (theme, colors, fonts, density,
            speaker_notes, footer, aspect and the like).

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
        for key in _FILE_PREFS:
            if wanted.get(key):
                try:
                    _readable(wanted[key], f"preference {key}")
                except (OutlineError, SkillPermissionError) as exc:
                    return {"saved": False, **_error(exc)}
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
