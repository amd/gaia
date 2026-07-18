# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guard against reintroducing broken agent-install advice (issue #2240).

`amd-gaia[agents]` names an unsatisfiable pip/uv extra (it lists 15
`gaia-agent-<id>` packages, none of which are published) — an unsatisfiable
extra doesn't fail cleanly, it makes the resolver backtrack past every
version that declares it and silently *downgrade* the user's install. A bare
`pip install gaia-agent-<id>` fails outright for the same reason: nothing is
published. Both phrases were copy-pasted into ~20 raised error messages
across `src/gaia/` and `hub/agents/python/`.

Scope, stated explicitly so this test's coverage is unambiguous:

* **In scope:** ``src/gaia/**/*.py`` and ``hub/agents/python/<id>/**/*.py``
  (this deliberately includes each hub agent's own ``tests/`` subtree and
  packaging scripts — nothing currently living there matches the patterns
  below, by design; see the false-positive corpus this test was validated
  against, in the PR description).
* **Out of scope, deliberately — NOT covered by this test:** ``docs/**``
  (fixed by hand in a separate increment), ``hub/agents/python/*/README.md``
  (already hedge with "once published"), and
  ``hub/agents/python/*/pyproject.toml`` (real dependency declarations, not
  user-facing advice). Non-``.py`` files are also naturally skipped by the
  ``*.py`` glob, but the README/pyproject exclusion is called out explicitly
  so a reader doesn't assume docs are covered when they are not.

No network calls (no PyPI queries) — this only reads local files with
``ast``/regex.

Why AST instead of a raw-text regex scan (the ``test_amd_gaia_urls.py``
style): a naive regex for ``gaia-agent-[a-z0-9_-]+`` over raw file text hits
many strings that are NOT install advice — console-script usage docstrings,
argparse ``prog=``, health-check JSON payloads, pyproject-name assertions in
each hub agent's own tests, a `--copy-metadata` pyinstaller flag, and a
docstring in ``gaia/hub/publisher.py`` describing the *future* PyPI design.
None of those are inside a raised exception. Every real offender in the
current codebase (verified by reading each of the ~20 sites) is a string
literal passed directly to a raised exception constructor (``raise
RuntimeError(...)`` / ``ImportError(...)`` / ``ValueError(...)``) — so this
test walks ``ast.Raise`` nodes specifically, not the whole file's text, for
the ``gaia-agent-<name>`` pattern. One real site (``agent_registry.py``)
builds its offending text in a local ``hint = "..."`` variable and
interpolates it into the raise's f-string rather than writing the literal
directly in the ``raise(...)`` call — so the scanner also resolves a simple
one-level "name assigned a string literal earlier in the file, then
referenced by a bare ``{name}`` in the raised f-string" indirection. This is
a deliberate, narrow extension beyond pure literal-argument scanning; it is
exercised directly by
``test_scan_resolves_a_name_bound_to_a_literal_and_interpolated_into_the_raise``
below.

The `amd-gaia[agents]` bracket-extra syntax, by contrast, is dead syntax with
no legitimate use anywhere in shipped source after the fix — so that check is
intentionally NOT limited to raise() calls; it's a broad whole-file substring
scan across the same in-scope files.

An allowlist exists for future-proofing: once an agent wheel is actually
published, its `gaia-agent-<id>` name becomes legitimate install advice and
should be added to `_PUBLISHED_AGENT_WHEELS` below. It is empty today — all
15 `gaia-agent-*` names 404 on PyPI as of this writing (issues #1179, #1513
track publishing).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_GAIA = REPO_ROOT / "src" / "gaia"
_HUB_AGENTS_PYTHON = REPO_ROOT / "hub" / "agents" / "python"

_BAD_PACKAGE_RE = re.compile(r"gaia-agent-[a-z0-9_-]+")
_BRACKET_EXTRA = "amd-gaia[agents]"

# Grows only once a hub agent wheel is actually published to PyPI (#1179,
# #1513). Empty today.
_PUBLISHED_AGENT_WHEELS: frozenset[str] = frozenset()


def _in_scope_files() -> list[Path]:
    """Every ``*.py`` under ``src/gaia/`` and each ``hub/agents/python/<id>/``.

    Deliberately excludes docs/**, hub agent READMEs, and pyproject.toml —
    see the module docstring.
    """
    files = list(_SRC_GAIA.rglob("*.py"))
    if _HUB_AGENTS_PYTHON.is_dir():
        for agent_dir in sorted(_HUB_AGENTS_PYTHON.iterdir()):
            if agent_dir.is_dir():
                files.extend(agent_dir.rglob("*.py"))
    return sorted(files)


def _string_literal_text(node: ast.AST) -> str:
    """Return the literal string content of a Constant or a JoinedStr's
    constant segments (FormattedValue expressions are not evaluated — a
    variable's runtime value can't contain a hardcoded broken package name
    from a variable alone; see the module docstring for the one exception
    this scanner does resolve)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    return ""


def _build_name_literal_map(tree: ast.AST) -> dict:
    """Resolve simple ``name = "literal"`` assignments anywhere in the file.

    Deliberately not scope-aware (module- vs function-local) — this is a
    lint-style heuristic, not an interpreter. It exists solely to catch the
    one real call site (``agent_registry.py``) that builds its offending
    text in a ``hint = "..."`` variable and interpolates it into the raised
    f-string via a bare ``{hint}``, rather than writing the string directly
    as a raise() argument.
    """
    mapping: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        text = _string_literal_text(node.value)
        if text:
            mapping[node.targets[0].id] = text
    return mapping


def _raise_call_text(call: ast.Call, name_map: dict) -> str:
    """Concatenate all string content passed to a raised exception's Call,
    including one-level resolution of bare-Name f-string interpolations
    against `name_map` (see `_build_name_literal_map`)."""
    chunks = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            chunks.append(arg.value)
        elif isinstance(arg, ast.JoinedStr):
            for value in arg.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    chunks.append(value.value)
                elif isinstance(value, ast.FormattedValue) and isinstance(
                    value.value, ast.Name
                ):
                    resolved = name_map.get(value.value.id)
                    if resolved:
                        chunks.append(resolved)
    return "".join(chunks)


def _scan_file_for_bad_raises(path: Path) -> list[str]:
    """Return one `"path:line: reason"` string per offending raise() in *path*."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []

    name_map = _build_name_literal_map(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        combined = _raise_call_text(node.exc, name_map)
        if not combined:
            continue
        bad_names = sorted(
            {
                m
                for m in _BAD_PACKAGE_RE.findall(combined)
                if m not in _PUBLISHED_AGENT_WHEELS
            }
        )
        has_bracket_extra = _BRACKET_EXTRA in combined
        if not bad_names and not has_bracket_extra:
            continue
        reasons = list(bad_names)
        if has_bracket_extra:
            reasons.append(_BRACKET_EXTRA)
        offenders.append(f"{path}:{node.lineno}: {', '.join(reasons)}")
    return offenders


def _file_contains_bracket_extra_anywhere(path: Path) -> bool:
    """Whole-file substring check — NOT limited to raise() calls. There is no
    legitimate reason for `amd-gaia[agents]` to appear anywhere in shipped
    source after #2240; unlike the `gaia-agent-<name>` pattern (which
    legitimately appears in docstrings, health-check payloads, etc.), this
    dead syntax gets no benefit-of-the-doubt scoping."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return _BRACKET_EXTRA in text


# ── Whole-repo scans (the actual regression guard) ──────────────────────────


def test_no_raise_recommends_installing_an_unpublished_agent_package():
    offenders: list[str] = []
    for path in _in_scope_files():
        offenders.extend(_scan_file_for_bad_raises(path))
    assert not offenders, (
        "raise() calls that advise installing an unpublished gaia-agent-* "
        "package or the broken amd-gaia[agents] extra (issue #2240):\n  "
        + "\n  ".join(str(o) for o in offenders)
    )


def test_amd_gaia_agents_extra_appears_nowhere_in_shipped_source():
    offenders = [
        str(path)
        for path in _in_scope_files()
        if _file_contains_bracket_extra_anywhere(path)
    ]
    assert not offenders, (
        "amd-gaia[agents] must not appear anywhere in shipped source "
        "(issue #2240) — an unsatisfiable pip/uv extra silently downgrades "
        "installs:\n  " + "\n  ".join(offenders)
    )


# ── Semantic-lock tests: nail down the scanner's own matching rules ─────────
# (self-contained tmp_path fixtures — these test the scanner's logic, not the
# repo's current state, so they are expected to PASS even in the red phase.)


def test_scan_flags_a_bare_pip_install_of_an_unpublished_package(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n" '    raise RuntimeError("pip install gaia-agent-foo")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_raises(
        f
    ), "a raise() naming gaia-agent-foo must be flagged"


def test_scan_ignores_a_comment_mentioning_the_package_name(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "# comment mentioning gaia-agent-foo\n" "def f():\n" "    return 1\n",
        encoding="utf-8",
    )
    assert _scan_file_for_bad_raises(f) == []


def test_scan_ignores_a_non_raise_assignment_of_the_package_name(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text('SERVICE_NAME = "gaia-agent-foo"\n', encoding="utf-8")
    assert _scan_file_for_bad_raises(f) == []


def test_scan_flags_the_bracket_extra_inside_a_raise(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n" "    raise ValueError('pip install \"amd-gaia[agents]\"')\n",
        encoding="utf-8",
    )
    assert _scan_file_for_bad_raises(
        f
    ), "a raise() naming amd-gaia[agents] must be flagged"


def test_scan_ignores_an_fstring_whose_interpolated_value_is_a_real_variable(tmp_path):
    """Mirrors cli.py's `wheel` variable case — the literal text around the
    interpolation contains no gaia-agent- substring, only the runtime value
    does, which this static scanner correctly can't (and shouldn't) see."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f(wheel):\n" '    raise RuntimeError(f"install {wheel} for all agents")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_raises(f) == []


def test_scan_resolves_a_name_bound_to_a_literal_and_interpolated_into_the_raise(
    tmp_path,
):
    """Mirrors agent_registry.py's `hint` variable: the offending text is
    assigned to a plain-string local earlier, then interpolated via a bare
    `{name}` into the raised f-string."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n"
        '    hint = "install gaia-agent-foo"\n'
        '    raise ValueError(f"boom.{hint}")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_raises(f), "the resolved hint variable must be flagged"


def test_bracket_extra_anywhere_flags_non_raise_usage(tmp_path):
    """The broader bracket-extra scan (unlike the raise-scoped one) also
    catches non-raise occurrences — e.g. a stray docstring or comment."""
    f = tmp_path / "mod.py"
    f.write_text('# See pip install "amd-gaia[agents]" for details\n', encoding="utf-8")
    assert _file_contains_bracket_extra_anywhere(f) is True


def test_bracket_extra_anywhere_false_when_absent(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _file_contains_bracket_extra_anywhere(f) is False


def test_published_agent_wheels_allowlist_is_empty_today():
    """Locks in the plan's stated fact so a silent edit doesn't go unnoticed;
    bump this (and cite the publish PR) only once a wheel is actually live
    on PyPI (#1179, #1513)."""
    assert _PUBLISHED_AGENT_WHEELS == frozenset()
