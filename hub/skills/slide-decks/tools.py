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

from pathlib import Path
from typing import Any, Dict, List, Optional

from slidedeck import blueprints as _bp
from slidedeck import build as _build
from slidedeck import markdown as _md
from slidedeck import style as _style
from slidedeck import verify as _verify
from slidedeck.outline import OutlineError, load_outline, normalize_outline

from gaia.agents.base.tools import tool
from gaia.logger import get_logger
from gaia.skills.context import (
    SkillContextError,
    check_read,
    check_write,
    copy_to_temp,
    find_program,
    preferences,
    run_program,
    temp_dir,
)
from gaia.skills.errors import SkillPermissionError

log = get_logger(__name__)

#: LibreOffice's first start builds its profile; a long deck takes a while.
RENDER_TIMEOUT_S = 240
#: Longest LibreOffice profile path that stays clear of the Windows path limit.
MAX_PROFILE_PATH = 120
_SKILL_DIR = Path(__file__).resolve().parent


def _error(exc: Exception) -> Dict[str, Any]:
    problems = getattr(exc, "problems", None) or [str(exc)]
    return {"status": "error", "error": str(exc), "problems": problems}


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


def _writable_deck(path: str, what: str = "output_path") -> Path:
    p = _absolute(path, what)
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
    return check_write(p)


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
    output_path: str,
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
        output_path: Absolute path of the .pptx to write, in an existing folder.
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
        if request:
            data["style"] = _style.merge_prefs(data.get("style") or {}, request)
        if required_items:
            data["required_items"] = list(
                dict.fromkeys([*(data.get("required_items") or []), *required_items])
            )
        _check_inputs(data, saved, template_path)
        out = _writable_deck(output_path)
        result = _build.build_deck(
            data, str(out), theme or None, template_path or None, saved
        )
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


@tool(display_label="Building slide deck from Markdown")
def build_deck_from_markdown(
    markdown: str,
    output_path: str,
    theme: str = "",
    prefs: Optional[dict] = None,
    required_items: Optional[list] = None,
) -> dict:
    """Build and check a .pptx from Markdown: the easiest way to make a deck.

    '# Title' is the cover, each '## Takeaway sentence' a slide, '-' lines
    bullets; ```chart, ```table and ```kpi blocks take CSV
    (references/markdown-format.md). Returns what build_deck returns.

    Args:
        markdown: The deck as Markdown.
        output_path: Absolute path of the .pptx to write, in an existing folder.
        theme: light, dark or minimal; empty uses the saved preference.
        prefs: Optional style the user asked for in this request.
        required_items: Facts the request needs visible on the slides; checked.
    """
    try:
        outline = _md.outline_from_markdown(markdown)
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
        renderer = _renderer() if render else None
        report = _verify.verify_deck(
            str(deck),
            expected_slides=expected_slides or None,
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
        deck_path, lambda d, prefs: _build.add_slide(d, specs, position or None, prefs)
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
        deck_path, lambda d, prefs: _build.update_slide(d, index, slide, prefs)
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
