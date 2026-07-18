# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Developer workflow for the ``gaia agent`` command group.

This module owns the *authoring* side of the Agent Hub developer loop —
scaffolding a new agent package, bumping its version, and running the quality
gates that publishing requires:

* ``gaia agent init <name> --language python|cpp`` — scaffold a package that
  mirrors ``hub/agents/summarize/python/`` (the canonical reference layout).
* ``gaia agent version <patch|minor|major>`` — bump the SemVer in
  ``gaia-agent.yaml`` (and keep ``pyproject.toml`` / ``__init__.py`` in sync).
* ``gaia agent test`` — quality gates in two modes:
  * ``--lint`` (default, CI-safe, no LLM): manifest valid + complete, package
    structure sound, Python sources parse, ``black``/``isort`` clean.
  * ``--live`` (requires Lemonade Server): the agent answers each declared
    ``conversation_starter`` without crashing.

The export/import subcommands stay in ``gaia.cli`` (legacy bundle workflow);
this module is wired in with two small hooks (``register_subparsers`` and
``handle``) to keep ``cli.py`` churn minimal.

Per ``CLAUDE.md`` (No Silent Fallbacks): every gate either passes or raises an
:class:`AgentWorkflowError` naming *what* failed, *what* to do, and *where* to
look. Nothing degrades silently.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.logger import get_logger
from gaia.version import __version__ as GAIA_VERSION

log = get_logger(__name__)

# Default model for a freshly scaffolded agent — matches the framework default
# (Gemma-4-E4B-it-GGUF, see DEFAULT_MODEL_NAME in lemonade_client). Kept as a
# literal so the template stays a pure string with no heavy import.
DEFAULT_MODEL = "Gemma-4-E4B-it-GGUF"

# Mirrors gaia.hub.manifest._ID_RE so a bad name fails at init time with the
# same vocabulary the manifest validator uses later.
_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,50}[a-z0-9])?$")

_CORE_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class AgentWorkflowError(Exception):
    """Raised when an ``init`` / ``version`` / ``test`` step cannot proceed.

    The message always names *what* failed, *what* to do, and *where* to look,
    per the project's fail-loudly rule.
    """


# ---------------------------------------------------------------------------
# CLI wiring (the two hooks cli.py calls)
# ---------------------------------------------------------------------------


def register_subparsers(agent_subparsers) -> None:
    """Add ``init`` / ``version`` / ``test`` to the existing ``agent`` group.

    Args:
        agent_subparsers: the subparsers object created by
            ``agent_parser.add_subparsers(dest="agent_action", ...)`` in
            ``gaia.cli.build_parser``.
    """
    init_p = agent_subparsers.add_parser(
        "init",
        help="Scaffold a new agent package (gaia-agent.yaml + skeleton)",
    )
    init_p.add_argument(
        "name",
        help="Agent id/name (lowercase, hyphens allowed), e.g. 'my-agent'",
    )
    init_p.add_argument(
        "--language",
        choices=["python", "cpp"],
        default="python",
        help="Implementation language for the scaffold (default: python)",
    )
    init_p.add_argument(
        "--output",
        "-o",
        default=".",
        help="Parent directory to create the package in (default: current dir)",
    )
    init_p.add_argument(
        "--layout",
        choices=["flat", "hub"],
        default="flat",
        help=(
            "Package layout: 'flat' creates <output>/<id>/ (default, for "
            "standalone packages); 'hub' creates <output>/<id>/<language>/ to "
            "match the hub tree (e.g. -o hub/agents/ --layout hub)"
        ),
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing package directory if it exists",
    )

    version_p = agent_subparsers.add_parser(
        "version",
        help="Bump the version in gaia-agent.yaml (patch/minor/major)",
    )
    version_p.add_argument(
        "bump",
        choices=["patch", "minor", "major"],
        help="Which SemVer component to increment",
    )
    version_p.add_argument(
        "--path",
        default=".",
        help="Agent package directory (default: current dir)",
    )

    test_p = agent_subparsers.add_parser(
        "test",
        help="Run quality gates: --lint (default, no LLM) or --live (Lemonade)",
    )
    mode = test_p.add_mutually_exclusive_group()
    mode.add_argument(
        "--lint",
        action="store_true",
        help="Static quality gates: manifest, structure, syntax, black+isort "
        "(default, CI-safe, no LLM required)",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Runtime gate: agent answers its conversation_starters (needs "
        "Lemonade Server)",
    )
    test_p.add_argument(
        "--path",
        default=".",
        help="Agent package directory (default: current dir)",
    )
    test_p.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Per-prompt timeout in seconds for --live mode (default: 60)",
    )

    pack_p = agent_subparsers.add_parser(
        "pack",
        help="Build a distributable wheel from an agent package (python -m build)",
    )
    pack_p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Agent package directory (default: current dir)",
    )
    pack_p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory for the wheel (default: <package>/dist)",
    )

    publish_p = agent_subparsers.add_parser(
        "publish",
        help="Build + publish the wheel to R2 (Hub) and PyPI (dual-publish)",
    )
    publish_p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Agent package directory (default: current dir)",
    )
    publish_p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory for the built wheel (default: <package>/dist)",
    )
    publish_p.add_argument(
        "--hub-url",
        default=None,
        help="R2 Worker origin (default: GAIA_HUB_URL or https://hub.amd-gaia.ai)",
    )
    publish_p.add_argument(
        "--skip-r2",
        action="store_true",
        help="Publish to PyPI only (skip the R2 Hub upload)",
    )
    publish_p.add_argument(
        "--skip-pypi",
        action="store_true",
        help="Publish to R2 only (skip the PyPI upload)",
    )

    configure_p = agent_subparsers.add_parser(
        "configure",
        help="Set per-agent config (model preference, settings) in ~/.gaia/agents/<id>",
    )
    configure_p.add_argument("id", help="Agent id to configure, e.g. 'chat'")
    configure_p.add_argument(
        "--model",
        default=None,
        help="Preferred model for this agent (stored as the 'model' setting)",
    )
    configure_p.add_argument(
        "--set",
        dest="settings",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set an arbitrary setting (repeatable), e.g. --set temperature=0.2",
    )
    configure_p.add_argument(
        "--replace",
        action="store_true",
        help="Replace the whole config instead of merging into the existing one",
    )
    configure_p.add_argument(
        "--show",
        action="store_true",
        help="Print the current config and exit (no changes)",
    )

    health_p = agent_subparsers.add_parser(
        "health",
        help="Health check: does an installed agent load + its entry point resolve?",
    )
    health_p.add_argument("id", help="Agent id to health-check, e.g. 'chat'")

    status_p = agent_subparsers.add_parser(
        "status",
        help="Show installed version, health, and config for one or all agents",
    )
    status_p.add_argument(
        "id",
        nargs="?",
        default=None,
        help="Agent id (omit to show every discovered agent)",
    )

    run_p = agent_subparsers.add_parser(
        "run",
        help="Run a registered agent (custom or built-in) by id",
    )
    run_p.add_argument(
        "id", help="Agent id to run, e.g. 'jarvis' (custom) or 'chat' (built-in)"
    )
    run_p.add_argument(
        "--query",
        "-q",
        type=str,
        default=None,
        help="Single query to execute (defaults to interactive mode if not provided)",
    )
    run_p.add_argument("--debug", action="store_true", help="Enable debug output")
    run_p.add_argument(
        "--model",
        default=None,
        help="Model ID to use (default: auto-selected by the agent)",
    )
    run_p.add_argument(
        "--stream",
        action="store_true",
        help="Enable real-time streaming of LLM responses",
    )
    run_p.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum conversation steps (default: the agent's own default)",
    )
    run_p.add_argument(
        "--allowed-paths",
        nargs="+",
        default=None,
        help="Allowed directory paths for file operations",
    )
    run_p.add_argument(
        "--list-tools",
        action="store_true",
        help="List the agent's registered tools and exit",
    )

    login_p = agent_subparsers.add_parser(
        "login",
        help="Store publisher tokens (R2 Hub and/or PyPI) in the OS keyring",
    )
    login_p.add_argument(
        "--hub-token",
        default=None,
        help="R2 Worker publish token (prompted securely if omitted with --hub)",
    )
    login_p.add_argument(
        "--pypi-token",
        default=None,
        help="PyPI API token (prompted securely if omitted with --pypi)",
    )
    login_p.add_argument(
        "--hub",
        action="store_true",
        help="Prompt for the R2 Hub token (use instead of --hub-token)",
    )
    login_p.add_argument(
        "--pypi",
        action="store_true",
        help="Prompt for the PyPI token (use instead of --pypi-token)",
    )


def handle(args) -> bool:
    """Dispatch ``init`` / ``version`` / ``test`` actions.

    Returns:
        ``True`` if this module handled the action (caller should return),
        ``False`` if the action belongs to another handler (export/import).
    """
    action = getattr(args, "agent_action", None)
    dispatch = {
        "init": cmd_init,
        "version": cmd_version,
        "test": cmd_test,
        "pack": cmd_pack,
        "publish": cmd_publish,
        "login": cmd_login,
        "configure": cmd_configure,
        "health": cmd_health,
        "status": cmd_status,
        "run": cmd_run,
    }
    fn = dispatch.get(action)
    if fn is None:
        return False
    try:
        fn(args)
    except AgentWorkflowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return True


# ---------------------------------------------------------------------------
# Name derivation
# ---------------------------------------------------------------------------


class _Names:
    """Derived identifiers for a scaffolded agent."""

    def __init__(self, raw_name: str):
        slug = raw_name.strip().lower().replace(" ", "-").replace("_", "-")
        slug = re.sub(r"-+", "-", slug).strip("-")
        if not slug or not _ID_RE.match(slug):
            raise AgentWorkflowError(
                f"agent name {raw_name!r} is not a valid id. Use 1-52 lowercase "
                f"alphanumeric characters and internal hyphens (start and end "
                f"with a letter or digit), e.g. 'my-agent'."
            )
        self.id = slug
        self.module_slug = slug.replace("-", "_")
        self.package = f"gaia_agent_{self.module_slug}"
        self.dist_name = f"gaia-agent-{slug}"
        parts = [p for p in slug.split("-") if p]
        pascal = "".join(p.capitalize() for p in parts)
        if not pascal.endswith("Agent"):
            pascal += "Agent"
        if not pascal.isidentifier():
            raise AgentWorkflowError(
                f"agent name {raw_name!r} produces an invalid Python class name "
                f"{pascal!r}. Choose a name that starts with a letter, e.g. "
                f"'my-agent' instead of '3d-agent'."
            )
        self.class_name = pascal
        self.display_name = " ".join(p.capitalize() for p in parts)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args) -> None:
    """Scaffold a new agent package directory."""
    names = _Names(args.name)
    parent = Path(args.output).expanduser().resolve()
    pkg_dir = parent / names.id
    if args.layout == "hub":
        pkg_dir = pkg_dir / args.language

    if pkg_dir.exists():
        if not args.force:
            raise AgentWorkflowError(
                f"directory already exists: {pkg_dir}. Re-run with --force to "
                f"overwrite, or choose a different name/--output."
            )
    pkg_dir.parent.mkdir(parents=True, exist_ok=True)

    if args.language == "python":
        _scaffold_python(pkg_dir, names)
    else:
        _scaffold_cpp(pkg_dir, names)

    print(f"Scaffolded {args.language} agent '{names.id}' at {pkg_dir}")
    print("Next steps:")
    print(f"  cd {pkg_dir}")
    print("  gaia agent test --lint     # validate the scaffold")
    if args.language == "python":
        print("  pip install -e .           # register via the gaia.agent entry point")


def _scaffold_python(pkg_dir: Path, names: _Names) -> None:
    """Write the Python package layout (mirrors hub/agents/summarize/python)."""
    from gaia.agents.builder.template import (
        TEMPLATE_INSTRUCTIONS,
        TEMPLATE_STARTERS,
        generate_agent_source,
    )

    description = f"{names.display_name} — a GAIA agent (edit this description)"
    code_dir = pkg_dir / names.package
    tests_dir = pkg_dir / "tests"
    code_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Dev scaffold intentionally seeds the playful demo persona — a complete,
    # runnable example the developer rewrites. (The conversational UI Builder
    # instead authors a purpose-matched persona; see agents/builder/agent.py.)
    agent_source = generate_agent_source(
        agent_id=names.id,
        agent_name=names.display_name,
        description=description,
        class_name=names.class_name,
        starters=list(TEMPLATE_STARTERS),
        system_prompt=TEMPLATE_INSTRUCTIONS,
    )

    # Normalise generated Python with the same tools (and defaults) the --lint
    # gate enforces, so a freshly scaffolded package passes 'gaia agent test
    # --lint' as-is. Done in-process (not via subprocess) so it works wherever
    # black/isort are importable.
    (pkg_dir / "gaia-agent.yaml").write_text(
        _render_manifest_yaml(names, description), encoding="utf-8"
    )
    (pkg_dir / "pyproject.toml").write_text(
        _render_pyproject(names, description), encoding="utf-8"
    )
    (pkg_dir / "README.md").write_text(
        _render_readme(names, description), encoding="utf-8"
    )
    (pkg_dir / "CHANGELOG.md").write_text(_render_changelog(names), encoding="utf-8")
    (code_dir / "__init__.py").write_text(
        _format_python_source(_render_init_py(names, description)), encoding="utf-8"
    )
    (code_dir / "agent.py").write_text(
        _format_python_source(agent_source), encoding="utf-8"
    )
    (tests_dir / "test_agent.py").write_text(
        _format_python_source(_render_test_py(names)), encoding="utf-8"
    )


def _format_python_source(source: str) -> str:
    """Return *source* formatted with isort (black profile) then black.

    Best-effort: the scaffold templates are already black/isort-clean, so
    formatting is a cosmetic nicety, not a correctness requirement. When black
    and isort are not installed (they live in the ``[dev]`` extra, absent from
    a bare ``amd-gaia`` install), return the source unchanged rather than
    failing — ``gaia agent init`` must work without the dev toolchain.
    """
    try:
        import black
        import isort
    except ImportError:
        return source
    sorted_src = isort.code(source, profile="black")
    try:
        return black.format_str(sorted_src, mode=black.Mode())
    except Exception:  # pylint: disable=broad-exception-caught
        # black raises only on a syntax error in the generated source; the
        # templates are valid, so fall back to the isort-sorted text.
        return sorted_src


def _scaffold_cpp(pkg_dir: Path, names: _Names) -> None:
    """Write a minimal native (C++) agent scaffold."""
    src_dir = pkg_dir / "src"
    tests_dir = pkg_dir / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    (pkg_dir / "gaia-agent.yaml").write_text(
        _render_manifest_yaml_cpp(names), encoding="utf-8"
    )
    (pkg_dir / "CMakeLists.txt").write_text(_render_cmakelists(names), encoding="utf-8")
    (src_dir / "agent.cpp").write_text(_render_agent_cpp(names), encoding="utf-8")
    (tests_dir / "test_agent.cpp").write_text(_render_test_cpp(names), encoding="utf-8")
    (pkg_dir / "README.md").write_text(
        _render_readme(names, f"{names.display_name} — a native GAIA agent"),
        encoding="utf-8",
    )
    (pkg_dir / "CHANGELOG.md").write_text(_render_changelog(names), encoding="utf-8")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def cmd_version(args) -> None:
    """Bump the SemVer version in gaia-agent.yaml (and keep siblings in sync)."""
    pkg_dir = Path(args.path).expanduser().resolve()
    manifest_path = pkg_dir / "gaia-agent.yaml" if pkg_dir.is_dir() else pkg_dir
    if not manifest_path.exists():
        raise AgentWorkflowError(
            f"gaia-agent.yaml not found at {manifest_path}. Run this from an "
            f"agent package directory, or pass --path <dir>."
        )

    old = _read_yaml_scalar(manifest_path, "version")
    if old is None:
        raise AgentWorkflowError(
            f"no 'version:' field in {manifest_path}. A manifest must declare a "
            f"SemVer version, e.g. 'version: 0.1.0'."
        )
    new = _bump_semver(old, args.bump)

    _replace_yaml_scalar(manifest_path, "version", new)
    print(f"{manifest_path.name}: {old} -> {new}")

    # Keep the package's other version declarations in sync so they don't drift.
    base = manifest_path.parent
    pyproject = base / "pyproject.toml"
    if pyproject.exists() and _replace_pyproject_version(pyproject, new):
        print(f"pyproject.toml: -> {new}")

    names_id = _read_yaml_scalar(manifest_path, "id")
    if names_id:
        init_py = base / f"gaia_agent_{names_id.replace('-', '_')}" / "__init__.py"
        if init_py.exists() and _replace_dunder_version(init_py, new):
            print(f"{init_py.name}: -> {new}")


def _bump_semver(version: str, part: str) -> str:
    m = _CORE_SEMVER_RE.match(version.strip())
    if not m:
        raise AgentWorkflowError(
            f"version {version!r} is not valid SemVer. Use MAJOR.MINOR.PATCH "
            f"(e.g. '0.1.0') before bumping. See https://semver.org."
        )
    major, minor, patch = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:  # patch
        patch += 1
    return f"{major}.{minor}.{patch}"


# ---------------------------------------------------------------------------
# test (quality gates)
# ---------------------------------------------------------------------------


def cmd_test(args) -> None:
    """Run quality gates against an agent package."""
    pkg_dir = Path(args.path).expanduser().resolve()
    if not pkg_dir.is_dir():
        raise AgentWorkflowError(
            f"package directory not found: {pkg_dir}. Pass --path <dir> or run "
            f"from inside an agent package."
        )

    if args.live:
        _run_live_gates(pkg_dir, timeout=args.timeout)
    else:
        _run_lint_gates(pkg_dir)


def _run_lint_gates(pkg_dir: Path) -> None:
    """Static, CI-safe gates — no LLM, no network."""
    from gaia.hub import manifest as hub_manifest

    print(f"Running --lint quality gates on {pkg_dir}")
    failures: List[str] = []

    # Gate 1: manifest valid + complete.
    try:
        parsed = hub_manifest.parse(pkg_dir)
        _ok("gaia-agent.yaml valid and complete")
    except hub_manifest.ManifestError as exc:
        _bad("gaia-agent.yaml validation")
        failures.append(str(exc))
        # Without a valid manifest the remaining language-specific gates are
        # meaningless — fail loudly now.
        raise AgentWorkflowError("lint failed:\n  - " + "\n  - ".join(failures))

    # Gate: documentation present. A WARNING, not a failure — an agent can
    # publish without these, but its hub page would have an empty Overview /
    # Changelog (both are rendered from these files), so nudge the author.
    for doc in ("README.md", "CHANGELOG.md"):
        if (pkg_dir / doc).exists():
            _ok(f"{doc} present")
        else:
            _warn(
                f"{doc} not found — the hub agent page renders it as documentation; "
                f"add one so your listing isn't empty."
            )

    if parsed.language == "python":
        _lint_python(pkg_dir, parsed, failures)
    else:
        _lint_cpp(pkg_dir, parsed, failures)

    if failures:
        raise AgentWorkflowError("lint failed:\n  - " + "\n  - ".join(failures))
    print("\nAll lint gates passed.")


def _lint_python(pkg_dir: Path, parsed, failures: List[str]) -> None:
    # Gate 2: package structure (package dir + __init__.py, entry point).
    if parsed.python is None or not parsed.python.entry_module:
        _bad("python.entry_module declared")
        failures.append(
            "gaia-agent.yaml: python.entry_module is missing. Declare it, e.g. "
            "'python:\\n  entry_module: gaia_agent_<id>'."
        )
        return

    code_dir = pkg_dir / parsed.python.entry_module
    init_py = code_dir / "__init__.py"
    if not init_py.exists():
        _bad("package structure")
        failures.append(
            f"package module not found: {code_dir}/__init__.py. The "
            f"entry_module '{parsed.python.entry_module}' must be a package "
            f"directory with an __init__.py."
        )
    else:
        _ok("package structure (__init__.py present)")

    # Gate 3: entry point present in pyproject.toml.
    pyproject = pkg_dir / "pyproject.toml"
    if not pyproject.exists():
        _bad("pyproject.toml present")
        failures.append(
            f"pyproject.toml not found at {pyproject}. It declares the "
            f"'gaia.agent' entry point that registers the agent."
        )
    else:
        text = pyproject.read_text(encoding="utf-8")
        if 'entry-points."gaia.agent"' not in text and (
            "entry-points.gaia.agent" not in text
        ):
            _bad("gaia.agent entry point")
            failures.append(
                'pyproject.toml: missing [project.entry-points."gaia.agent"] '
                "section. Add it so the registry can discover the agent."
            )
        else:
            _ok("gaia.agent entry point declared")

    # Gate 4: Python sources parse (no syntax errors).
    py_files = sorted(code_dir.rglob("*.py")) if code_dir.exists() else []
    syntax_ok = True
    for py in py_files:
        try:
            ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            syntax_ok = False
            _bad(f"syntax: {py.name}")
            failures.append(f"syntax error in {py}: {exc}")
    if py_files and syntax_ok:
        _ok(f"Python sources parse ({len(py_files)} file(s))")

    # Gate 5: imports resolve at the package surface.
    if init_py.exists():
        ok, err = _import_check(pkg_dir, parsed.python.entry_module)
        if ok:
            _ok("package imports resolve")
        else:
            _bad("package imports")
            failures.append(err)

    # Gate 6: black + isort clean.
    fmt_files = list(py_files)
    tests_dir = pkg_dir / "tests"
    if tests_dir.exists():
        fmt_files.extend(sorted(tests_dir.rglob("*.py")))
    if fmt_files:
        _lint_formatters(fmt_files, failures)


def _lint_cpp(pkg_dir: Path, parsed, failures: List[str]) -> None:
    # Structural gates for native agents. Compile verification needs a
    # toolchain; CI's build_agents workflow runs the actual build.
    cmake = pkg_dir / "CMakeLists.txt"
    if cmake.exists():
        _ok("CMakeLists.txt present")
    else:
        _bad("CMakeLists.txt present")
        failures.append(
            f"CMakeLists.txt not found at {cmake}. A C++ agent needs a build "
            f"definition."
        )

    sources = list(pkg_dir.rglob("*.cpp")) + list(pkg_dir.rglob("*.cc"))
    if sources:
        _ok(f"C++ sources present ({len(sources)} file(s))")
    else:
        _bad("C++ sources present")
        failures.append(
            f"no .cpp/.cc sources found under {pkg_dir}. A C++ agent needs at "
            f"least one source file."
        )

    if parsed.cpp is None or not parsed.cpp.binaries:
        # parse() already enforces this for cpp, but guard explicitly.
        failures.append(
            "gaia-agent.yaml: cpp.binaries must map at least one platform to a "
            "binary path."
        )
    print(
        "  note: compile verification is not run by --lint (needs a C++ "
        "toolchain); the CI build_agents workflow compiles native agents."
    )


def _lint_formatters(py_files: List[Path], failures: List[str]) -> None:
    """Check black + isort cleanliness in-process (defaults, no subprocess).

    Best-effort: black/isort live in the ``[dev]`` extra. When they are not
    installed, skip the formatting check with a note instead of failing — the
    other --lint gates (manifest, structure, syntax, imports) still run.
    """
    try:
        import black
        import isort
    except ImportError:
        print(
            "  note: skipped black/isort check (not installed; "
            "install with 'uv pip install \"amd-gaia[dev]\"')."
        )
        return
    mode = black.Mode()
    black_bad: List[str] = []
    isort_bad: List[str] = []
    for py in py_files:
        src = py.read_text(encoding="utf-8")
        if isort.code(src, profile="black") != src:
            isort_bad.append(py.name)
        try:
            if black.format_str(src, mode=mode) != src:
                black_bad.append(py.name)
        except Exception:  # pylint: disable=broad-exception-caught
            # Syntax errors are already reported by the parse gate; flag here too.
            black_bad.append(py.name)

    if black_bad:
        _bad("black formatting")
        failures.append(
            "black would reformat: "
            + ", ".join(black_bad)
            + ". Run 'python util/lint.py --black --fix' (or 'black <path>')."
        )
    else:
        _ok("black formatting clean")

    if isort_bad:
        _bad("isort import order")
        failures.append(
            "isort would reorder imports in: "
            + ", ".join(isort_bad)
            + ". Run 'python util/lint.py --isort --fix' (or 'isort <path>')."
        )
    else:
        _ok("isort import order clean")


def _run_live_gates(pkg_dir: Path, timeout: int) -> None:
    """Runtime gate: the agent answers its conversation_starters."""
    from gaia.hub import manifest as hub_manifest

    parsed = hub_manifest.parse(pkg_dir)
    if parsed.language != "python":
        raise AgentWorkflowError(
            f"--live is only supported for python agents (this is "
            f"{parsed.language!r}). Use --lint, or run the agent's native test "
            f"harness."
        )
    if parsed.python is None or not parsed.python.entry_module:
        raise AgentWorkflowError(
            "gaia-agent.yaml: python.entry_module is required for --live."
        )

    starters = parsed.conversation_starters or _starters_from_init(
        pkg_dir, parsed.python.entry_module
    )
    if not starters:
        raise AgentWorkflowError(
            "no conversation_starters declared in gaia-agent.yaml (and none "
            "found in the package's build_registration()). --live needs at "
            "least one prompt to exercise the agent."
        )

    agent = _instantiate_agent(pkg_dir, parsed)

    print(
        f"Running --live quality gates ({len(starters)} prompt(s), "
        f"{timeout}s timeout each)"
    )
    for i, prompt in enumerate(starters, 1):
        print(f"  [{i}/{len(starters)}] {prompt!r}")
        result, error, timed_out = _query_with_timeout(agent, prompt, timeout)
        if timed_out:
            raise AgentWorkflowError(
                f"agent did not respond to prompt {prompt!r} within {timeout}s. "
                f"Increase --timeout, or check that Lemonade Server is running "
                f"and the model is loaded."
            )
        if error is not None:
            raise AgentWorkflowError(
                f"agent crashed on prompt {prompt!r}: {error}. Fix the agent "
                f"and re-run 'gaia agent test --live'."
            ) from error
        if not _nonempty_response(result):
            raise AgentWorkflowError(
                f"agent returned an empty response for prompt {prompt!r}. "
                f"Check the agent's logic and model availability."
            )
        _ok(f"responded to prompt {i}")
    print("\nAll live gates passed.")


def _query_with_timeout(agent, prompt: str, timeout: int):
    """Run ``agent.process_query`` in a worker thread bounded by *timeout*.

    Returns ``(result, error, timed_out)``. A Python thread cannot be force
    killed, so on timeout we abandon the worker (daemon) and fail loudly rather
    than hang the developer's terminal.
    """
    import threading

    box = {}

    def _worker():
        try:
            box["result"] = agent.process_query(prompt)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            box["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, None, True
    return box.get("result"), box.get("error"), False


# ---------------------------------------------------------------------------
# pack / publish / login (distribution)
# ---------------------------------------------------------------------------


def cmd_pack(args) -> None:
    """Build a distributable wheel from an agent package."""
    from gaia.hub import packager

    try:
        result = packager.pack(args.path, output_dir=args.output)
    except packager.PackagerError as exc:
        raise AgentWorkflowError(str(exc)) from exc

    print(f"Built wheel for agent '{result.agent_id}' v{result.version}")
    print(f"  path:   {result.wheel_path}")
    print(f"  size:   {result.size_bytes} bytes")
    print(f"  sha256: {result.sha256}")
    print("\nNext: 'gaia agent publish' to upload to the Hub (R2) and PyPI.")


def cmd_publish(args) -> None:
    """Build the wheel, then dual-publish it to R2 (Hub) and PyPI."""
    from gaia.hub import packager, publisher

    pkg_dir = Path(args.path).expanduser().resolve()
    manifest_path = pkg_dir / "gaia-agent.yaml"

    try:
        pack_result = packager.pack(args.path, output_dir=args.output)
    except packager.PackagerError as exc:
        raise AgentWorkflowError(str(exc)) from exc

    print(f"Built {pack_result.wheel_path.name} (sha256 {pack_result.sha256[:12]}…)")
    print("Publishing…")
    try:
        result = publisher.publish(
            pack_result,
            manifest_path,
            hub_url=args.hub_url,
            skip_r2=args.skip_r2,
            skip_pypi=args.skip_pypi,
        )
    except publisher.PublisherError as exc:
        raise AgentWorkflowError(str(exc)) from exc

    for target in (result.r2, result.pypi):
        status = "skipped" if target.skipped else "PASS"
        print(f"  [{status}] {target.target}: {target.detail}")
    print(
        f"\nPublished agent '{result.agent_id}' v{result.version} "
        f"(R2 for the Hub UI, PyPI for 'pip install {pack_result.dist_name}')."
    )


def cmd_login(args) -> None:
    """Store R2 Hub and/or PyPI publisher tokens in the OS keyring."""
    import getpass

    from gaia.hub import publisher

    stored = []

    hub_token = args.hub_token
    if hub_token is None and args.hub:
        hub_token = getpass.getpass("R2 Hub publish token: ")
    if hub_token:
        try:
            publisher.store_token("hub", hub_token)
        except publisher.PublisherError as exc:
            raise AgentWorkflowError(str(exc)) from exc
        stored.append("hub")

    pypi_token = args.pypi_token
    if pypi_token is None and args.pypi:
        pypi_token = getpass.getpass("PyPI API token: ")
    if pypi_token:
        try:
            publisher.store_token("pypi", pypi_token)
        except publisher.PublisherError as exc:
            raise AgentWorkflowError(str(exc)) from exc
        stored.append("pypi")

    if not stored:
        raise AgentWorkflowError(
            "no token provided. Pass --hub-token/--pypi-token (or --hub/--pypi "
            "to be prompted). Tokens are stored in your OS keyring and read at "
            "publish time."
        )
    print(f"Stored token(s) in the OS keyring: {', '.join(stored)}.")


# ---------------------------------------------------------------------------
# configure / health / status (lifecycle — issue #465)
# ---------------------------------------------------------------------------


def _coerce_setting(raw: str):
    """Parse a ``KEY=VALUE`` pair, JSON-decoding the value when possible.

    ``--set temperature=0.2`` stores a float; ``--set verbose=true`` a bool;
    ``--set model=Gemma-4-E4B-it-GGUF`` a string. Anything that is not valid
    JSON is kept as the raw string.
    """
    import json

    if "=" not in raw:
        raise AgentWorkflowError(
            f"--set expects KEY=VALUE, got {raw!r}. Example: --set temperature=0.2."
        )
    key, _, value = raw.partition("=")
    key = key.strip()
    if not key:
        raise AgentWorkflowError(f"--set has an empty key in {raw!r}.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    return key, parsed


def cmd_configure(args) -> None:
    """Set per-agent config under ~/.gaia/agents/<id>/config.json."""
    from gaia.hub import lifecycle

    if args.show:
        try:
            current = lifecycle.read_config(args.id)
        except lifecycle.LifecycleError as exc:
            raise AgentWorkflowError(str(exc)) from exc
        if not current:
            print(f"No config set for '{args.id}'.")
        else:
            import json

            print(json.dumps(current, indent=2, sort_keys=True))
        return

    settings = {}
    if args.model:
        settings[lifecycle.CONFIG_MODEL_KEY] = args.model
    for raw in args.settings:
        key, value = _coerce_setting(raw)
        settings[key] = value

    if not settings:
        raise AgentWorkflowError(
            "nothing to configure. Pass --model <name> and/or --set KEY=VALUE, "
            "or --show to view the current config."
        )

    try:
        merged = lifecycle.configure(args.id, settings, merge=not args.replace)
    except lifecycle.LifecycleError as exc:
        raise AgentWorkflowError(str(exc)) from exc

    import json

    print(f"Updated config for '{args.id}':")
    print(json.dumps(merged, indent=2, sort_keys=True))


def cmd_health(args) -> None:
    """Run a health check against an installed agent."""
    from gaia.hub import lifecycle

    registry = _build_registry()
    result = lifecycle.health_check(args.id, registry=registry)
    print(f"{args.id}: {result.state}")
    if result.detail:
        print(f"  {result.detail}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    # error/not_installed are non-zero exits so scripts can gate on it.
    if result.state in (lifecycle.HEALTH_ERROR, lifecycle.HEALTH_NOT_INSTALLED):
        sys.exit(1)


def cmd_status(args) -> None:
    """Show installed version + health + config for one or all agents."""
    from gaia.hub import lifecycle

    registry = _build_registry()
    if args.id:
        statuses = {args.id: lifecycle.status(args.id, registry=registry)}
    else:
        statuses = lifecycle.status_all(registry=registry)

    if not statuses:
        print("No agents discovered.")
        return

    width = max(len(aid) for aid in statuses)
    print(f"{'AGENT'.ljust(width)}  VERSION    HEALTH         SOURCE")
    for aid, st in statuses.items():
        version = st.installed_version or "-"
        print(
            f"{aid.ljust(width)}  {version.ljust(9)}  "
            f"{st.health.ljust(13)}  {st.source or '-'}"
        )


def _build_registry():
    """Build and populate an AgentRegistry for health/status commands."""
    from gaia.agents.registry import AgentRegistry

    registry = AgentRegistry()
    registry.discover()
    return registry


def resolve_and_create_agent(
    agent_id: str,
    agent_config_kwargs: Dict[str, Any],
    *,
    not_found_message: Optional[Callable[[], str]] = None,
) -> Any:
    """Discover, resolve, and construct *agent_id* through the registry.

    The single discover → get → create_agent path shared by ``gaia browse``/
    ``gaia analyze`` (``gaia.cli``) and ``gaia agent run`` (this module) — one
    mechanism, not a parallel copy per caller (#2242).

    Args:
        agent_id: Registry id to resolve — a built-in id (e.g. ``web``), a
            hub-installed id, or a custom agent's ``AGENT_ID``.
        agent_config_kwargs: Forwarded to the resolved registration's factory.
        not_found_message: Lazily-evaluated message to raise when *agent_id*
            isn't registered. Evaluated only on the not-found path so a
            caller-supplied hint (e.g. one that reads installed package
            metadata to build a pip-install pointer) never runs on the happy
            path. When omitted, a generic message lists every registered id.

    Raises:
        RuntimeError: *agent_id* is not registered (either the caller's hint
            or the generic "unknown id" message), or it is registered but its
            factory raised while constructing it.
    """
    registry = _build_registry()

    if registry.get(agent_id) is None:
        if not_found_message is not None:
            raise RuntimeError(not_found_message())
        available = sorted(reg.id for reg in registry.list())
        raise RuntimeError(
            f"Unknown agent ID: '{agent_id}'. Registered agents: "
            f"{', '.join(available) if available else '(none discovered)'}"
        )

    try:
        return registry.create_agent(agent_id, **agent_config_kwargs)
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(
            f"Agent '{agent_id}' is registered but failed to start: {exc}"
        ) from exc


def cmd_run(args) -> None:
    """Run any registered agent (custom or built-in) by id.

    Shares the discover/get/construct path used by ``gaia browse``/``gaia
    analyze`` via :func:`resolve_and_create_agent`, so a user's own
    ``~/.gaia/agents/<id>/agent.py`` runs through the exact same mechanism as
    a built-in — there is no second, custom-only construction path (#2242).
    """
    agent_config_kwargs = dict(
        model_id=args.model,
        # None → global default (default_max_steps / env) in Agent.
        max_steps=args.max_steps,
        streaming=args.stream,
        show_stats=False,
        silent_mode=not (args.debug or args.list_tools),
        debug=args.debug,
        allowed_paths=args.allowed_paths,
    )

    try:
        agent = resolve_and_create_agent(args.id, agent_config_kwargs)
    except RuntimeError as exc:
        raise AgentWorkflowError(str(exc)) from exc

    try:
        if args.list_tools:
            agent.list_tools(verbose=True)
            return

        if args.query:
            result = agent.process_query(args.query, trace=False)
            if result.get("status") != "success":
                sys.exit(1)
            return

        print(f"Starting {agent.__class__.__name__}. Type /quit to exit.")
        while True:
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"/quit", "/exit"}:
                return
            agent.process_query(user_input, trace=False)
    finally:
        if hasattr(agent, "close"):
            agent.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _instantiate_agent(pkg_dir: Path, parsed):
    """Import and construct the agent class from a scaffolded package."""
    import importlib

    _ensure_on_path(pkg_dir)
    module_name = parsed.python.entry_module
    try:
        pkg = importlib.import_module(module_name)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise AgentWorkflowError(
            f"could not import package {module_name!r}: {exc}. Run "
            f"'pip install -e .' in the package, or check the entry_module name."
        ) from exc

    class_name = parsed.python.entry_class
    if not class_name:
        reg = getattr(pkg, "build_registration", None)
        if reg is None:
            raise AgentWorkflowError(
                f"gaia-agent.yaml has no python.entry_class and {module_name} "
                f"exposes no build_registration(). Declare one so --live can "
                f"construct the agent."
            )
        try:
            return reg().factory()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise AgentWorkflowError(
                f"build_registration().factory() failed: {exc}."
            ) from exc

    try:
        cls = getattr(pkg, class_name)
    except AttributeError as exc:
        raise AgentWorkflowError(
            f"entry_class {class_name!r} not found in {module_name}: {exc}."
        ) from exc
    try:
        return cls()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise AgentWorkflowError(f"could not construct {class_name}(): {exc}.") from exc


def _starters_from_init(pkg_dir: Path, module_name: str) -> List[str]:
    # Best-effort: if the manifest omits conversation_starters we try the
    # package's build_registration(). Any failure here just yields [], and the
    # caller raises a loud "no conversation_starters" error — no silent success.
    import importlib

    try:
        _ensure_on_path(pkg_dir)
        pkg = importlib.import_module(module_name)
        reg = getattr(pkg, "build_registration", None)
        if reg is None:
            return []
        return list(reg().conversation_starters or [])
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def _nonempty_response(result) -> bool:
    if result is None:
        return False
    if isinstance(result, str):
        return bool(result.strip())
    if isinstance(result, dict):
        for key in ("result", "response", "text", "answer", "final_answer"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                return True
        # A dict with any truthy content counts as a response.
        return any(bool(v) for v in result.values())
    return True


def _ensure_on_path(pkg_dir: Path) -> None:
    p = str(pkg_dir)
    if p not in sys.path:
        sys.path.insert(0, p)


def _import_check(pkg_dir: Path, module_name: str) -> Tuple[bool, str]:
    """Import the package top module (cheap/lazy) to confirm imports resolve."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import importlib; importlib.import_module(%r)" % (str(pkg_dir), module_name)
    )
    rc, out = _run_subprocess([sys.executable, "-c", code])
    if rc == 0:
        return True, ""
    return False, (
        f"importing package {module_name!r} failed:\n{out.strip()}\n"
        f"Fix the import error (or 'pip install -e .' to resolve dependencies)."
    )


def _run_subprocess(cmd: List[str]) -> Tuple[int, str]:
    """Run a subprocess; return (returncode, combined stdout+stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return 1, f"could not run {cmd[0]}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _ok(label: str) -> None:
    print(f"  [PASS] {label}")


def _bad(label: str) -> None:
    print(f"  [FAIL] {label}")


def _warn(label: str) -> None:
    print(f"  [WARN] {label}")


# ---------------------------------------------------------------------------
# YAML scalar read/replace (line-oriented, preserves formatting)
# ---------------------------------------------------------------------------


def _read_yaml_scalar(path: Path, key: str) -> Optional[str]:
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+?)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def _replace_yaml_scalar(path: Path, key: str, value: str) -> None:
    pattern = re.compile(rf"^({re.escape(key)}:\s*).+?(\s*)$")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        m = pattern.match(stripped)
        if m:
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f"{m.group(1)}{value}{newline}"
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise AgentWorkflowError(f"no '{key}:' line found in {path} to update.")


def _replace_pyproject_version(path: Path, value: str) -> bool:
    pattern = re.compile(r'^(version\s*=\s*)"[^"]*"(\s*)$')
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        m = pattern.match(stripped)
        if m:
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f'{m.group(1)}"{value}"{newline}'
            path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


def _replace_dunder_version(path: Path, value: str) -> bool:
    pattern = re.compile(r'^(__version__\s*=\s*)"[^"]*"(\s*)$')
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        m = pattern.match(stripped)
        if m:
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f'{m.group(1)}"{value}"{newline}'
            path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_STARTERS_YAML = (
    "  - Hello! What can you do?\n"
    "  - Tell me a fun fact.\n"
    "  - What's your favourite topic?"
)


def _render_manifest_yaml(names: _Names, description: str) -> str:
    return f"""\
id: {names.id}
name: {names.display_name}
version: 0.1.0
description: "{description}"
author: Your Name
license: MIT

category: general
tags: [{names.id}]
icon: sparkles
tools_count: 0

language: python
min_gaia_version: "{GAIA_VERSION}"
models: [{DEFAULT_MODEL}]

conversation_starters:
{_STARTERS_YAML}

python:
  entry_module: {names.package}
  entry_class: {names.class_name}
  dependencies:
    - "amd-gaia>={GAIA_VERSION}"

requirements:
  min_memory_gb: 8
  platforms: [win-x64, linux-x64, darwin-arm64]

interfaces:
  tui: false
  cli: true
  pipe: true
  api_server: true
  mcp_server: true
"""


def _render_manifest_yaml_cpp(names: _Names) -> str:
    return f"""\
id: {names.id}
name: {names.display_name}
version: 0.1.0
description: "{names.display_name} — a native GAIA agent (edit this description)"
author: Your Name
license: MIT

category: general
tags: [{names.id}]
icon: sparkles
tools_count: 0

language: cpp
min_gaia_version: "{GAIA_VERSION}"

cpp:
  static_linked: true
  binaries:
    win-x64: build/{names.id}.exe
    linux-x64: build/{names.id}
    darwin-arm64: build/{names.id}

requirements:
  min_memory_gb: 4
  platforms: [win-x64, linux-x64, darwin-arm64]

interfaces:
  tui: false
  cli: true
  pipe: true
  api_server: false
  mcp_server: false
"""


def _render_pyproject(names: _Names, description: str) -> str:
    return f"""\
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{names.dist_name}"
version = "0.1.0"
description = "{description}"
authors = [{{ name = "Your Name" }}]
license = {{ text = "MIT" }}
readme = "README.md"
requires-python = ">=3.10"
dependencies = ["amd-gaia>={GAIA_VERSION}"]

[project.entry-points."gaia.agent"]
{names.id} = "{names.package}:build_registration"

[project.optional-dependencies]
test = ["pytest"]

[tool.setuptools.packages.find]
include = ["{names.package}*"]
"""


def _render_readme(names: _Names, description: str) -> str:
    return f"""\
# {names.dist_name}

{description}

## Install

```bash
pip install -e .   # editable, for development
```

Installing registers the `{names.id}` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Develop / test

```bash
gaia agent test --lint    # static quality gates (no LLM)
gaia agent test --live    # runtime gates (requires Lemonade Server)
pip install -e ".[test]"
pytest tests/ -x
```

## Versioning

```bash
gaia agent version patch  # 0.1.0 -> 0.1.1
gaia agent version minor  # 0.1.0 -> 0.2.0
gaia agent version major  # 0.1.0 -> 1.0.0
```

Add a matching `CHANGELOG.md` entry with each release — the hub agent page
renders it as the agent's Changelog section.
"""


def _render_changelog(names: _Names) -> str:
    return f"""\
# Changelog

All notable changes to `{names.dist_name}` are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/).
This file is published with the agent and rendered on its hub page.

## 0.1.0

- Initial release of the {names.display_name} agent.
"""


def _render_init_py(names: _Names, description: str) -> str:
    return f'''\
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA {names.display_name} agent — standalone hub package.

Installs the ``{names.id}`` agent into the GAIA registry via the ``gaia.agent``
entry-point group (see ``pyproject.toml``). The framework's
``AgentRegistry._discover_installed_agents`` calls :func:`build_registration`
at discovery time; the agent module itself is imported lazily inside the
factory so discovery stays cheap.
"""

__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import {names.package}`` (e.g. at registry
    # discovery) does not pull in the heavy agent module + its SDK deps.
    if name == "{names.class_name}":
        from {names.package}.agent import {names.class_name}

        return {names.class_name}
    raise AttributeError(f"module {{__name__!r}} has no attribute {{name!r}}")


def build_registration():
    """Return the :class:`AgentRegistration` for the {names.id} agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from {names.package}.agent import {names.class_name}

        return class_factory({names.class_name})(**kwargs)

    return AgentRegistration(
        id="{names.id}",
        name="{names.display_name}",
        description="{description}",
        source="installed",
        conversation_starters=[
            "Hello! What can you do?",
            "Tell me a fun fact.",
            "What's your favourite topic?",
        ],
        factory=factory,
        agent_dir=None,
        models=["{DEFAULT_MODEL}"],
        namespaced_agent_id="installed:{names.id}",
        category="general",
        tags=["{names.id}"],
        icon="sparkles",
        tools_count=0,
    )
'''


def _render_test_py(names: _Names) -> str:
    return f'''\
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Smoke tests for the {names.display_name} agent package (no LLM required)."""

from pathlib import Path

import yaml

from {names.package} import build_registration


def test_build_registration_matches_manifest():
    """The registration metadata agrees with gaia-agent.yaml."""
    reg = build_registration()
    manifest_path = Path(__file__).resolve().parent.parent / "gaia-agent.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert reg.id == data["id"]
    assert reg.name == data["name"]
    assert reg.factory is not None
    assert reg.conversation_starters


def test_agent_class_importable():
    """The agent class is importable via the package's lazy re-export."""
    import {names.package}

    cls = getattr({names.package}, "{names.class_name}")
    assert cls.__name__ == "{names.class_name}"
'''


def _render_cmakelists(names: _Names) -> str:
    return f"""\
cmake_minimum_required(VERSION 3.16)
project({names.module_slug} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable({names.id} src/agent.cpp)

enable_testing()
add_executable({names.module_slug}_tests tests/test_agent.cpp)
add_test(NAME {names.module_slug}_tests COMMAND {names.module_slug}_tests)
"""


def _render_agent_cpp(names: _Names) -> str:
    return f"""\
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// {names.display_name} — a native GAIA agent skeleton.
// Reads a prompt from stdin (pipe interface) and writes a response to stdout.

#include <iostream>
#include <string>

int main() {{
    std::string line;
    while (std::getline(std::cin, line)) {{
        // TODO: replace with your agent's logic.
        std::cout << "{names.display_name} received: " << line << std::endl;
    }}
    return 0;
}}
"""


def _render_test_cpp(names: _Names) -> str:
    return f"""\
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Minimal self-contained test for {names.display_name}.

#include <cstdlib>

int main() {{
    // TODO: add real assertions for your agent's behaviour.
    return EXIT_SUCCESS;
}}
"""
