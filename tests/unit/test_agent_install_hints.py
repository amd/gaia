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

* **In scope:** ``src/gaia/**/*.py`` and ``hub/agents/python/<id>/**/*.py``,
  excluding any ``tests/`` subtree. Test files are skipped because they
  legitimately assert on the very strings this guard forbids — both the
  historical ones and the new replacement text — so scanning them would
  make the guard flag its own fixtures.
* **Out of scope, deliberately — NOT covered by this test:** ``docs/**``
  (fixed by hand in a separate increment), ``hub/agents/python/*/README.md``
  (already hedge with "once published"), and
  ``hub/agents/python/*/pyproject.toml`` (real dependency declarations, not
  user-facing advice). Non-``.py`` files are also naturally skipped by the
  ``*.py`` glob, but the README/pyproject exclusion is called out explicitly
  so a reader doesn't assume docs are covered when they are not.

No network calls (no PyPI queries) — this only reads local files with
``ast``/regex.

**The discriminator, stated plainly:** a string literal is flagged when it
contains BOTH

1. an install verb — ``pip install`` or ``uv pip install``; and
2. a package that cannot be installed — ``gaia-agent-<name>`` (nothing is
   published) or the exact broken extra ``amd-gaia[agents]``.

Both halves are required, and this is a *content* test, not a *context*
one — it deliberately does NOT care whether the literal sits in a
``raise``, a ``print``, an f-string, a logger call, or a return value. An
install hint can hide in any of them: ``gaia init``'s completion banner
emits guidance through ``self._print(...)``, with existing precedent at
``src/gaia/installer/init_command.py:1301,1304``
(``"Run: pip install lemonade-sdk"``). Scoping this guard to ``raise``
arguments would leave every ``print``-based hint uncovered — including the
one this issue's own fix adds to that banner.

Requiring the install verb is what makes an allowlist unnecessary. Every
legitimate ``gaia-agent-<name>`` reference in the tree — console-script
usage docstrings, argparse ``prog=``, health-check JSON payloads, the
``--copy-metadata`` pyinstaller flag, pyproject-name assertions, plain
comments — names the distribution without telling anyone to install it, so
none contain an install verb and all fall out with nothing to maintain.

Note the second half is ``amd-gaia[agents]`` exactly, NOT a broad
``amd-gaia[`` prefix. ``amd-gaia[ui]``, ``[api]``, ``[dev]``, ``[publish]``
and friends are real, working, still-declared extras; ~15 shipped hints
recommend them correctly. Flagging those would repeat precisely the mistake
this issue is about — mislabelling a functioning install path as a broken
one (the same reason ``cli.py``'s ``pip install -e ".[blender]"`` branch is
deliberately left alone).

The `amd-gaia[agents]` bracket-extra syntax is additionally checked on its
own, with no install-verb requirement: it is dead syntax with no legitimate
use anywhere in shipped source after this fix, so that check is a broad
whole-file substring scan across the same in-scope files.

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

_BRACKET_EXTRA = "amd-gaia[agents]"

# Half 2 of the discriminator: a package that cannot be installed. Note the
# exact `[agents]` — a bare `amd-gaia[` prefix would flag the working
# [ui]/[api]/[dev]/[publish] extras (see the module docstring).
_UNINSTALLABLE_RE = re.compile(r"gaia-agent-[a-z0-9_-]+|amd-gaia\[agents\]")

# Half 1 of the discriminator: an install verb.
_INSTALL_VERB_RE = re.compile(r"\b(?:uv\s+)?pip\s+install\b")

# Grows only once a hub agent wheel is actually published to PyPI (#1179,
# #1513). Empty today.
_PUBLISHED_AGENT_WHEELS: frozenset[str] = frozenset()


def _in_scope_files() -> list[Path]:
    """Every ``*.py`` under ``src/gaia/`` and each ``hub/agents/python/<id>/``,
    excluding ``tests/`` subtrees.

    Deliberately excludes docs/**, hub agent READMEs, and pyproject.toml —
    see the module docstring.
    """
    files = list(_SRC_GAIA.rglob("*.py"))
    if _HUB_AGENTS_PYTHON.is_dir():
        for agent_dir in sorted(_HUB_AGENTS_PYTHON.iterdir()):
            if agent_dir.is_dir():
                files.extend(agent_dir.rglob("*.py"))
    return sorted(f for f in files if "tests" not in f.parts)


def _iter_string_literals(tree: ast.AST):
    """Yield ``(lineno, text)`` for every string literal in *tree*.

    Covers plain ``str`` constants (adjacent literals are already merged
    into one node by the parser, so a hint split across several source
    lines is seen whole) and f-strings, for which only the constant
    segments are concatenated — a ``{variable}``'s runtime value is not
    knowable statically and never supplies the literal ``pip install`` /
    ``gaia-agent-`` characters this scanner matches on.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                value.value
                for value in node.values
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
            if text:
                yield node.lineno, text


def _scan_file_for_bad_install_hints(path: Path) -> list[str]:
    """Return one ``"path:line: reason"`` per offending literal in *path*.

    A literal offends when it carries an install verb AND names something
    that cannot be installed — see the module docstring for why both halves
    are required and why context (raise vs print vs return) is irrelevant.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []

    offenders: list[str] = []
    seen: set = set()
    for lineno, literal in _iter_string_literals(tree):
        if not _INSTALL_VERB_RE.search(literal):
            continue
        bad = sorted(
            {
                match
                for match in _UNINSTALLABLE_RE.findall(literal)
                if match not in _PUBLISHED_AGENT_WHEELS
            }
        )
        if not bad or (lineno, tuple(bad)) in seen:
            continue
        seen.add((lineno, tuple(bad)))
        offenders.append(f"{path}:{lineno}: {', '.join(bad)}")
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


def test_no_shipped_string_advises_installing_an_unpublished_package():
    offenders: list[str] = []
    for path in _in_scope_files():
        offenders.extend(_scan_file_for_bad_install_hints(path))
    assert not offenders, (
        "install hints naming an unpublished gaia-agent-* package or the "
        "broken amd-gaia[agents] extra (issue #2240):\n  "
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


def test_scan_flags_a_pip_install_of_an_unpublished_package(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n" '    raise RuntimeError("pip install gaia-agent-foo")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(
        f
    ), "a hint naming gaia-agent-foo must be flagged"


def test_scan_flags_a_hint_inside_a_print_not_only_a_raise(tmp_path):
    """The discriminator is content, not context — `gaia init`'s banner emits
    guidance via print(), and that path must be covered too (#2240)."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n" '    print("Install it with: pip install gaia-agent-foo")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(
        f
    ), "a print()-based hint must be flagged just like a raise()"


def test_scan_ignores_a_comment_mentioning_the_package_name(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "# comment mentioning gaia-agent-foo\n" "def f():\n" "    return 1\n",
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(f) == []


def test_scan_ignores_the_package_name_without_an_install_verb(tmp_path):
    """The install verb is what separates advice from mere reference — this
    is why no allowlist is needed for prog=/docstrings/health payloads."""
    f = tmp_path / "mod.py"
    f.write_text(
        'SERVICE_NAME = "gaia-agent-foo"\n'
        'PROG = "gaia-agent-foo"\n'
        '"""Smoke tests for the standalone gaia-agent-foo package."""\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(f) == []


def test_scan_ignores_an_install_verb_for_a_working_extra(tmp_path):
    """amd-gaia[ui]/[api]/[dev]/[publish] are real, declared, working extras.
    Flagging them would mislabel a functioning install path as broken — the
    same mistake #2240 exists to fix."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n"
        '    raise RuntimeError("Install it with: uv pip install \\"amd-gaia[ui]\\"")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(f) == []


def test_scan_flags_the_broken_agents_extra(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n" "    raise ValueError('pip install \"amd-gaia[agents]\"')\n",
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(
        f
    ), "a hint naming amd-gaia[agents] must be flagged"


def test_scan_ignores_an_fstring_whose_interpolated_value_is_a_real_variable(tmp_path):
    """Mirrors cli.py's `wheel` variable case — the literal text around the
    interpolation contains no gaia-agent- substring, only the runtime value
    does, which this static scanner correctly can't (and shouldn't) see."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f(wheel):\n"
        '    raise RuntimeError(f"pip install {wheel} for all agents")\n',
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(f) == []


def test_scan_flags_an_fstring_whose_literal_segments_carry_the_bad_advice(tmp_path):
    """Mirrors cli.py:821 — the interpolated `{wheel}` is opaque, but the
    literal text around it still names the broken extra."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f(wheel):\n"
        '    raise RuntimeError(\n'
        '        f"install `uv pip install {wheel}` (or "\n'
        '        f\'`uv pip install "amd-gaia[agents]"`)\'\n'
        "    )\n",
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(
        f
    ), "literal f-string segments naming the broken extra must be flagged"


def test_scan_flags_a_hint_split_across_adjacent_source_lines(tmp_path):
    """The real call sites wrap the hint across several adjacent string
    literals; the parser merges them, so the verb and the package are seen
    together even though neither line carries both."""
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n"
        "    raise RuntimeError(\n"
        '        "The chat agent is not installed. Install it with "\n'
        '        "`pip install gaia-agent-chat`, then retry."\n'
        "    )\n",
        encoding="utf-8",
    )
    assert _scan_file_for_bad_install_hints(
        f
    ), "a hint split across adjacent literals must still be flagged"


def test_bracket_extra_anywhere_flags_non_hint_usage(tmp_path):
    """The broader bracket-extra scan (unlike the discriminator) needs no
    install verb — it catches a stray docstring or comment too."""
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
