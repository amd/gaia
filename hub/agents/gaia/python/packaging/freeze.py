# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Reproducible PyInstaller freeze for the GAIA flagship agent REST sidecar.

Freezes ``packaging/server.py`` into a self-contained executable that boots the
``/v1/gaia/*`` REST surface with NO Python interpreter on the target machine.

Usage (from a venv with the deps + pyinstaller installed)::

    python hub/agents/gaia/python/packaging/freeze.py            # one-dir (default)
    python hub/agents/gaia/python/packaging/freeze.py --onefile  # one-file

Output:
    one-dir:  hub/agents/gaia/python/packaging/dist/gaia-agent/gaia-agent[.exe]
    one-file: hub/agents/gaia/python/packaging/dist/gaia-agent[.exe]

Design notes / gotchas baked in (mirrors the email sidecar's freeze):
- ``uvicorn`` loads its loops/protocols/lifespan impls by string import, so its
  submodules are invisible to static analysis -> ``--collect-submodules uvicorn``.
- ``keyring`` resolves OS backends through entry points -> collect its submodules
  AND copy its metadata so the entry-point lookup succeeds in the frozen app.
- ``GaiaAgent`` subclasses ``ChatAgent`` and registers tools lazily from the
  ``full`` profile's tool groups (RAG, filesystem, scratchpad, browser,
  screenshot), so collect both agent packages wholesale.
- ``gaia.connectors`` discovers providers dynamically; collect it explicitly.
- ``gaia-agent.yaml``, the agent's own ``gaia_agent/skills`` and the starter
  pack (``hub/skills``) are DATA, invisible to the import analyzer ->
  ``--add-data``. All of it lands under ``gaia_agent/`` and resolves relative to
  ``__file__`` at runtime (see ``gaia_agent/agent.py``).
- A bundled skill's ``tools.py`` and ``scripts/`` are imported or run from that
  data at runtime, so nothing reaches their imports statically. Each one is
  added as a hidden import, and the build fails if one is not installed.
- We deliberately do NOT ``--collect-submodules gaia``: the whole core package
  pulls every agent + RAG + torch and explodes the binary. Static analysis from
  the sidecar entry pulls only the reachable core modules.
- The agent's import graph reaches ``gaia.chat.sdk``, whose static graph reaches
  the ML stack (torch, transformers, ...). All inference is Lemonade over HTTP
  and never runs in-process, so the ML stack is EXCLUDED to keep the binary lean.
  ``faiss`` stays collected: the memory/RAG working-context index needs it.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "server.py"
NAME = "gaia-agent"
# Repo root: packaging/ -> python/ -> gaia/ -> agents/ -> hub/ -> <root>
REPO_ROOT = HERE.parents[4]
PKG_ROOT = REPO_ROOT / "hub" / "agents" / "gaia" / "python"
# Editable installs are invisible to PyInstaller's static analyzer, so point it
# at the source roots directly.
PATHEX = [
    PKG_ROOT,
    REPO_ROOT / "hub" / "agents" / "chat" / "python",
    REPO_ROOT / "src",
]

MANIFEST_SRC = PKG_ROOT / "gaia-agent.yaml"
SKILLS_SRC = PKG_ROOT / "gaia_agent" / "skills"
#: The first-party starter pack. A checkout discovers it in place; the frozen
#: binary has no hub/ tree, so it is staged beside the agent's own skills.
HUB_SKILLS_SRC = REPO_ROOT / "hub" / "skills"
#: Where both land in the binary — the root ``gaia_agent.agent._SKILLS_DIR`` reads.
SKILLS_DEST = "gaia_agent/skills"

# Modules PyInstaller must pull in wholesale, and the distributions whose
# metadata must travel with them. Declared once so the preflight guard below
# and the CLI args can never disagree about what has to be in the binary.
COLLECT_SUBMODULES = [
    # uvicorn: string-imported loops/protocols/lifespan.
    "uvicorn",
    # keyring: OS backend resolution via entry points.
    "keyring",
    # Both agent packages register tools lazily inside functions.
    "gaia_agent",
    "gaia_agent_chat",
    # connector provider discovery is dynamic.
    "gaia.connectors",
    # pydantic v2 ships a compiled core; collect data to be safe.
    "pydantic",
]
# FAISS backs the memory / RAG working-context index. faiss-cpu ships compiled
# libs + swig submodules the static analyzer misses.
# sherpa-onnx must be BUNDLED, not installed at runtime. The lazy
# pip-install path cannot work inside a frozen app: sys.executable is the
# .exe itself, not an interpreter, and a frozen app does not import from the
# system's site-packages either. Without this the shipped binary silently has
# no speaker identification while a source checkout has it — verified on a
# 46-minute meeting that produced zero speaker spans from the frozen build.
# collect-all because the wheel carries native .dll/.pyd payloads that static
# analysis cannot see. ~29 MB, Apache-2.0 (its vendored onnxruntime is MIT).
COLLECT_ALL = ["faiss", "sherpa_onnx"]
# Package data the analyzer can't see. pymupdf 2.x imports its layout module on
# import, which loads ONNX model configs from pymupdf/layout/resources at runtime;
# without them every ``import pymupdf`` in a run_python script fails.
COLLECT_DATA = ["pymupdf"]
# Imported behind an ``except ImportError`` guard, so PyInstaller bundles them
# only when they are installed and the binary otherwise boots without them.
# anthropic: ``--use-claude`` and ``/model``, which the TUI forwards to the binary.
REQUIRED_IMPORTS = ["anthropic"]
# importlib.metadata version probes + entry-point agent discovery.
COPY_METADATA = ["keyring", "amd-gaia", "gaia-agent-gaia", "gaia-agent-chat"]

# What a ``run_python`` script may import. The frozen binary is the only Python
# a user of the release has, and nothing in GAIA imports most of these, so the
# import analyzer would leave them out. Office output (.xlsx, .docx, .pptx,
# .pdf), tables and charts are what users' document and analysis tasks ask for.
SCRIPT_LIBRARIES = [
    "openpyxl",
    "xlsxwriter",
    "docx",
    "pptx",
    "reportlab",
    "pypdf",
    "pymupdf",
    "numpy",
    "pandas",
    "matplotlib",
    "PIL",
]
# A script may import any stdlib module, not only the ones GAIA's graph reaches.
# These are GUI toolkits, dev tooling, and CPython's own test suite.
STDLIB_EXCLUDED = {
    "test",
    "idlelib",
    "tkinter",
    "turtle",
    "turtledemo",
    "lib2to3",
    "ensurepip",
    "venv",
    "pydoc_data",
    "antigravity",
    "this",
}
# Script-library submodules that pull in a GUI toolkit or a test suite.
_UNWANTED_SUBMODULE = re.compile(
    r"\.(?:tests?|conftest)(?:\.|$)"
    r"|^matplotlib\.backends\.(?:.*(?:tk|qt|gtk|wx|cairo|macosx|webagg|nbagg)"
    r"|qt_editor|web_backend)",
    re.IGNORECASE,
)

# Heavy ML stack reached only through the lazily-imported chat/SDK graph. All
# inference goes to Lemonade over HTTP, so excluding these keeps the binary at
# ~170 MB instead of ~2 GB. pandas and matplotlib are NOT here: they are
# SCRIPT_LIBRARIES. tkinter is: no GAIA module uses it, and a bundled Tk would
# make matplotlib open windows from a headless agent.
EXCLUDES = [
    "torch",
    "transformers",
    "sentence_transformers",
    "tokenizers",
    "safetensors",
    "torchvision",
    "torchaudio",
    "scipy",
    "sympy",
    "tkinter",
]


def _verify_collect_targets() -> None:
    """Fail the build if a collect target is not importable in this env.

    PyInstaller downgrades an uncollectable ``--collect-all`` target to a
    WARNING and keeps going, so a missing dependency does not fail the freeze --
    it ships a binary silently missing that capability. ``faiss`` is the live
    example: the release workflow installs only ``.[api]``, which carries no RAG
    deps, so the binary booted and passed the smoke test with vector search and
    document Q&A absent. A frozen binary has no interpreter for the
    "pip install" remedy those code paths advise, so the gap is unrecoverable on
    the user's machine -- it has to fail here instead.
    """
    import importlib.util

    missing = [
        mod
        for mod in COLLECT_SUBMODULES
        + COLLECT_ALL
        + COLLECT_DATA
        + REQUIRED_IMPORTS
        + SCRIPT_LIBRARIES
        if importlib.util.find_spec(mod) is None
    ]
    if missing:
        raise SystemExit(
            "freeze: refusing to build -- these modules must be in the binary "
            f"but are not installed: {', '.join(missing)}.\n"
            "PyInstaller would produce a binary missing that capability "
            "without failing. Install them into the freeze environment the way "
            'the "Install deps + PyInstaller" step of '
            ".github/workflows/release_agent_gaia.yml does, and re-run."
        )


def _wanted_submodule(name: str) -> bool:
    return not _UNWANTED_SUBMODULE.search(name)


def _stdlib_top_level() -> list[str]:
    """Public stdlib modules this interpreter has, minus :data:`STDLIB_EXCLUDED`."""
    import importlib.util

    return sorted(
        name
        for name in sys.stdlib_module_names
        if not name.startswith("_")
        and name not in STDLIB_EXCLUDED
        and importlib.util.find_spec(name) is not None
    )


def script_hidden_imports() -> list[str]:
    """Every module a ``run_python`` script may import, as hidden imports.

    Whole packages, not just their ``__init__``: ``import matplotlib`` does not
    import ``matplotlib.pyplot``, and ``import xml`` does not import
    ``xml.etree``, so a top-level hidden import would still miss them.
    """
    import importlib.util

    from PyInstaller.utils.hooks import collect_submodules

    names: set[str] = set()
    for name in _stdlib_top_level() + SCRIPT_LIBRARIES:
        names.add(name)
        if importlib.util.find_spec(name).submodule_search_locations is not None:
            names.update(collect_submodules(name, filter=_wanted_submodule))
    return sorted(n for n in names if _wanted_submodule(n))


def _skill_dirs(root: Path) -> list[Path]:
    """Skill folders directly under *root* (those holding a ``SKILL.md``)."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def bundled_skill_dirs() -> list[Path]:
    """Every skill the binary ships: the agent's own, then the starter pack."""
    return _skill_dirs(SKILLS_SRC) + _skill_dirs(HUB_SKILLS_SRC)


def _skill_code_files(skill_dir: Path) -> list[Path]:
    """The Python a skill runs: ``tools.py`` and anything under ``scripts/``."""
    files = [skill_dir / "tools.py"] if (skill_dir / "tools.py").is_file() else []
    scripts = skill_dir / "scripts"
    if scripts.is_dir():
        files += sorted(scripts.rglob("*.py"))
    return files


def _is_local_module(name: str, skill_dir: Path, source: Path) -> bool:
    """True when *name* is a sibling module the skill ships itself.

    The loader puts the skill folder on ``sys.path`` for ``tools.py``, and a
    script runs with its own folder first on the path.
    """
    top = name.split(".")[0]
    return any(
        (folder / f"{top}.py").is_file() or (folder / top).is_dir()
        for folder in {skill_dir, source.parent}
    )


def _find_spec(name: str):
    """``importlib.util.find_spec``, with a missing parent package read as absent."""
    import importlib.util

    try:
        return importlib.util.find_spec(name)
    except (ModuleNotFoundError, ValueError):
        return None


def skill_code_hidden_imports(skill_dirs: list[Path] | None = None) -> list[str]:
    """Modules the bundled skills' code imports, as hidden imports.

    PyInstaller analyzes only the entry point's graph, and ``gaia`` is not
    collected wholesale, so a ``from gaia.web.client import WebClient`` in a
    skill's ``tools.py`` would ship as an ImportError on load. Fails the build
    when a module is not installed rather than shipping that.
    """
    names: set[str] = set()
    missing: list[str] = []
    for skill_dir in bundled_skill_dirs() if skill_dirs is None else skill_dirs:
        for source in _skill_code_files(skill_dir):
            tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found = [alias.name for alias in node.names]
                    submodules: list[str] = []
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    found = [node.module] if node.module else []
                    # `from pkg import mod` names a submodule as often as an
                    # attribute; only the analyzer-invisible module matters.
                    submodules = [f"{node.module}.{a.name}" for a in node.names]
                else:
                    continue
                for name in found:
                    if _is_local_module(name, skill_dir, source):
                        continue
                    if _find_spec(name) is None:
                        missing.append(f"{name} ({source.relative_to(REPO_ROOT)})")
                        continue
                    names.add(name)
                    names.update(m for m in submodules if _find_spec(m) is not None)
    if missing:
        raise SystemExit(
            "freeze: refusing to build -- bundled skill code imports modules "
            f"that are not installed: {', '.join(sorted(missing))}.\n"
            "The skill would load in the binary and fail on its first call. "
            "Install them into the freeze environment, or drop the import."
        )
    return sorted(names)


def _resolve_add_data() -> list[tuple[Path, str]]:
    """``(source, destination)`` pairs for data the import analyzer cannot see.

    The manifest is MANDATORY -- without it the frozen agent has no declared
    skill sets and ``SKILL_MANIFEST`` resolves to ``None``, which silently
    changes behaviour. The bundled skills directory is OPTIONAL for v0.1.0: it
    ships empty (no skill set is declared by default), and git cannot track an
    empty directory, so a fresh CI clone may not have it at all.
    """
    if not MANIFEST_SRC.exists():
        raise SystemExit(
            f"freeze: required bundle data is missing: {MANIFEST_SRC}\n"
            "The frozen sidecar would start with no agent manifest, so no skill "
            "sets could be declared and the hub metadata would be absent. "
            "Restore hub/agents/gaia/python/gaia-agent.yaml and re-run."
        )
    add_data: list[tuple[Path, str]] = [(MANIFEST_SRC, "gaia_agent")]

    skill_files = (
        sorted(p for p in SKILLS_SRC.rglob("*") if p.is_file() and p.name != ".gitkeep")
        if SKILLS_SRC.is_dir()
        else []
    )
    if skill_files:
        print(
            f"freeze: bundling {len(skill_files)} skill file(s) from {SKILLS_SRC}",
            flush=True,
        )
        add_data.append((SKILLS_SRC, SKILLS_DEST))
    else:
        reason = "absent" if not SKILLS_SRC.is_dir() else "empty"
        print("=" * 78, flush=True)
        print(
            f"freeze: NO SKILLS BUNDLED -- {SKILLS_SRC} is {reason}.\n"
            "        The frozen sidecar will ship with an empty skill library. "
            "This is\n"
            "        EXPECTED for v0.1.0 (gaia-agent.yaml declares no skill_sets), "
            "but if\n"
            "        you added skills and see this line, they are NOT in the "
            "binary.",
            flush=True,
        )
        print("=" * 78, flush=True)

    if not HUB_SKILLS_SRC.is_dir():
        raise SystemExit(
            f"freeze: required bundle data is missing: {HUB_SKILLS_SRC}\n"
            "The starter pack would be absent from the binary while a checkout "
            "has it. Restore hub/skills and re-run."
        )
    packaged = {p.name for p in _skill_dirs(SKILLS_SRC)}
    starter = _skill_dirs(HUB_SKILLS_SRC)
    clashes = sorted(p.name for p in starter if p.name in packaged)
    if clashes:
        raise SystemExit(
            f"freeze: {', '.join(clashes)} exist(s) in both {SKILLS_SRC} and "
            f"{HUB_SKILLS_SRC}; both land in {SKILLS_DEST}, so one would "
            "silently overwrite the other. Rename or remove one copy."
        )
    print(
        f"freeze: bundling {len(starter)} starter-pack skill(s) from {HUB_SKILLS_SRC}",
        flush=True,
    )
    for skill_dir in starter:
        add_data.append((skill_dir, f"{SKILLS_DEST}/{skill_dir.name}"))
    return add_data


def build(onefile: bool = False, clean: bool = True) -> Path:
    import PyInstaller.__main__

    _verify_collect_targets()
    add_data = _resolve_add_data()

    work = HERE / "build"
    dist = HERE / "dist"
    if clean:
        for d in (work, dist):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    args = [
        str(ENTRY),
        "--name",
        NAME,
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(HERE),
    ]
    for path in PATHEX:
        args += ["--paths", str(path)]
    for mod in COLLECT_SUBMODULES:
        args += ["--collect-submodules", mod]
    for mod in COLLECT_ALL:
        args += ["--collect-all", mod]
    for mod in COLLECT_DATA:
        args += ["--collect-data", mod]
    for mod in sorted(set(script_hidden_imports()) | set(skill_code_hidden_imports())):
        args += ["--hidden-import", mod]
    for distribution in COPY_METADATA:
        args += ["--copy-metadata", distribution]
    for source, dest in add_data:
        args += ["--add-data", f"{source}{os.pathsep}{dest}"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args.append("--onefile" if onefile else "--onedir")

    t0 = time.time()
    PyInstaller.__main__.run(args)
    elapsed = time.time() - t0

    suffix = ".exe" if sys.platform == "win32" else ""
    exe = dist / (NAME + suffix) if onefile else dist / NAME / (NAME + suffix)
    print(f"\nBuild finished in {elapsed:.1f}s")
    print(f"Executable: {exe}")
    if exe.exists():
        if onefile:
            size = exe.stat().st_size
        else:
            size = sum(
                p.stat().st_size for p in (dist / NAME).rglob("*") if p.is_file()
            )
        print(
            f"Size: {size / 1e6:.1f} MB ({'one-file exe' if onefile else 'one-dir total'})"
        )
    else:
        print("WARNING: expected executable not found.")
    return exe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Freeze the GAIA agent REST sidecar.")
    parser.add_argument(
        "--onefile", action="store_true", help="Build a single-file executable."
    )
    args = parser.parse_args(argv)
    exe = build(onefile=args.onefile)
    return 0 if exe.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
