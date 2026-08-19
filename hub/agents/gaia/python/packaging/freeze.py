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
- ``gaia-agent.yaml`` and the bundled skills directory are DATA, invisible to the
  import analyzer -> ``--add-data``. Both resolve relative to ``__file__`` at
  runtime (see ``gaia_agent/agent.py``).
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
import os
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

# Heavy ML stack reached only through the lazily-imported chat/SDK graph. All
# inference goes to Lemonade over HTTP, so excluding these keeps the binary at
# ~100 MB instead of ~2 GB. numpy stays (memory.py imports it at module level).
#
# ``pandas`` is excluded, same as the email sidecar: the scratchpad tool group
# is SQLite-backed (``gaia.scratchpad.service`` -> ``DatabaseMixin``) and no
# module under ``src/gaia/`` or either agent package imports pandas. Verified by
# grep before excluding -- the only repo hits are string literals in unrelated
# agents' keyword lists.
EXCLUDES = [
    "torch",
    "transformers",
    "sentence_transformers",
    "tokenizers",
    "safetensors",
    "torchvision",
    "torchaudio",
    "scipy",
    "matplotlib",
    "sympy",
    "pandas",
]


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
        add_data.append((SKILLS_SRC, "gaia_agent/skills"))
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
    return add_data


def build(onefile: bool = False, clean: bool = True) -> Path:
    import PyInstaller.__main__

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
    args += [
        # uvicorn: string-imported loops/protocols/lifespan.
        "--collect-submodules",
        "uvicorn",
        # keyring: OS backend resolution via entry points.
        "--collect-submodules",
        "keyring",
        "--copy-metadata",
        "keyring",
        # Both agent packages register tools lazily inside functions.
        "--collect-submodules",
        "gaia_agent",
        "--collect-submodules",
        "gaia_agent_chat",
        # connector provider discovery is dynamic.
        "--collect-submodules",
        "gaia.connectors",
        # FAISS backs the memory / RAG working-context index. faiss-cpu ships
        # compiled libs + swig submodules the static analyzer misses.
        "--collect-all",
        "faiss",
        # pydantic v2 ships a compiled core; collect data to be safe.
        "--collect-submodules",
        "pydantic",
        # importlib.metadata version probes + entry-point agent discovery.
        "--copy-metadata",
        "amd-gaia",
        "--copy-metadata",
        "gaia-agent-gaia",
        "--copy-metadata",
        "gaia-agent-chat",
    ]
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
