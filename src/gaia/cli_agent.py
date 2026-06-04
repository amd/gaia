# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Developer workflow for the ``gaia agent`` command group.

This module owns the *authoring* side of the Agent Hub developer loop —
scaffolding a new agent package, bumping its version, and running the quality
gates that publishing requires:

* ``gaia agent init <name> --language python|cpp`` — scaffold a package that
  mirrors ``hub/agents/python/summarize/`` (the canonical reference layout).
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
from typing import List, Optional, Tuple

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

    if pkg_dir.exists():
        if not args.force:
            raise AgentWorkflowError(
                f"directory already exists: {pkg_dir}. Re-run with --force to "
                f"overwrite, or choose a different name/--output."
            )
    parent.mkdir(parents=True, exist_ok=True)

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
    """Write the Python package layout (mirrors hub/agents/python/summarize)."""
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

    agent_source = generate_agent_source(
        agent_id=names.id,
        agent_name=names.display_name,
        description=description,
        class_name=names.class_name,
        starters=list(TEMPLATE_STARTERS),
        system_prompt=TEMPLATE_INSTRUCTIONS,
    )

    (pkg_dir / "gaia-agent.yaml").write_text(
        _render_manifest_yaml(names, description), encoding="utf-8"
    )
    (pkg_dir / "pyproject.toml").write_text(
        _render_pyproject(names, description), encoding="utf-8"
    )
    (pkg_dir / "README.md").write_text(
        _render_readme(names, description), encoding="utf-8"
    )
    (code_dir / "__init__.py").write_text(
        _render_init_py(names, description), encoding="utf-8"
    )
    (code_dir / "agent.py").write_text(agent_source, encoding="utf-8")
    (tests_dir / "test_agent.py").write_text(_render_test_py(names), encoding="utf-8")

    # Normalise generated sources with the same tools the --lint gate enforces,
    # so a freshly scaffolded package passes 'gaia agent test --lint' as-is.
    _format_python_sources([str(code_dir), str(tests_dir)])


def _format_python_sources(targets: List[str]) -> None:
    """Run black + isort over generated sources; fail loudly if unavailable."""
    isort_rc, isort_out = _run_tool(["isort", *targets])
    if isort_rc != 0:
        raise AgentWorkflowError(
            "could not format scaffold with isort. The 'gaia agent' developer "
            "workflow needs the formatting tools — install them with "
            f"'pip install \"amd-gaia[dev]\"'.\n{isort_out.strip()}"
        )
    black_rc, black_out = _run_tool(["black", "--quiet", *targets])
    if black_rc != 0:
        raise AgentWorkflowError(
            "could not format scaffold with black. The 'gaia agent' developer "
            "workflow needs the formatting tools — install them with "
            f"'pip install \"amd-gaia[dev]\"'.\n{black_out.strip()}"
        )


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
    targets = [str(p) for p in (code_dir,) if p.exists()]
    tests_dir = pkg_dir / "tests"
    if tests_dir.exists():
        targets.append(str(tests_dir))
    if targets:
        _lint_formatters(targets, failures)


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


def _lint_formatters(targets: List[str], failures: List[str]) -> None:
    """Run black --check and isort --check-only on the given targets."""
    black_rc, black_out = _run_tool(["black", "--check", "--quiet", *targets])
    if black_rc == 0:
        _ok("black formatting clean")
    else:
        _bad("black formatting")
        failures.append(
            "black would reformat files. Run "
            "'python util/lint.py --black --fix' (or 'black <path>') to fix.\n"
            + black_out.strip()
        )

    isort_rc, isort_out = _run_tool(["isort", "--check-only", *targets])
    if isort_rc == 0:
        _ok("isort import order clean")
    else:
        _bad("isort import order")
        failures.append(
            "isort would reorder imports. Run "
            "'python util/lint.py --isort --fix' (or 'isort <path>') to fix.\n"
            + isort_out.strip()
        )


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
    rc, out = _run_tool([sys.executable, "-c", code], raw=True)
    if rc == 0:
        return True, ""
    return False, (
        f"importing package {module_name!r} failed:\n{out.strip()}\n"
        f"Fix the import error (or 'pip install -e .' to resolve dependencies)."
    )


def _run_tool(cmd: List[str], raw: bool = False) -> Tuple[int, str]:
    """Run a subprocess; return (returncode, combined output)."""
    full = cmd if raw else [sys.executable, "-m", *cmd]
    try:
        proc = subprocess.run(
            full,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return 1, f"could not run {full[0]}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _ok(label: str) -> None:
    print(f"  [PASS] {label}")


def _bad(label: str) -> None:
    print(f"  [FAIL] {label}")


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
