# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Reproducible PyInstaller freeze for the GAIA Email Triage REST sidecar
(packaging spike, milestone #49 / email-agent-packaging Phase 2).

Freezes ``server.py`` into a self-contained executable that boots the email
REST surface with NO Python interpreter on the target machine.

Usage (from an activated venv with the deps + pyinstaller installed)::

    python hub/agents/python/email/packaging/freeze.py            # one-dir (default)
    python hub/agents/python/email/packaging/freeze.py --onefile  # one-file

Output:
    one-dir:  hub/agents/python/email/packaging/dist/email-agent/email-agent[.exe]
    one-file: hub/agents/python/email/packaging/dist/email-agent[.exe]

Design notes / gotchas baked in (see README.md for the why):
- ``uvicorn`` loads its loops/protocols/lifespan impls by string import, so its
  submodules are invisible to static analysis -> ``--collect-submodules uvicorn``.
- ``keyring`` resolves OS backends through entry points -> collect its submodules
  AND copy its metadata so the entry-point lookup succeeds in the frozen app.
- The email router lazily imports its tool modules inside functions
  (``classify_email_llm`` / ``summarize_email_llm`` etc.); collect the whole
  ``gaia_agent_email`` package so the triage path is present in the freeze.
- ``gaia.connectors`` discovers providers dynamically; collect it explicitly.
- We deliberately do NOT ``--collect-submodules gaia`` (the whole core package
  pulls every agent + RAG + torch and explodes the binary). Static analysis from
  the email router pulls only the reachable core modules.
- The triage path lazily imports ``gaia.chat.sdk`` (AgentSDK) inside
  ``_build_llm_chat``, whose static import graph reaches the ML stack (torch,
  transformers, …). Real triage talks to the local Lemonade Server over HTTP and
  never runs torch in-process, so we EXCLUDE the ML stack to keep the binary lean
  (~90 MB vs ~2 GB). If a future build genuinely needs these, drop them from
  ``EXCLUDES``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "server.py"
NAME = "email-agent"
# Repo root: packaging/ -> email/ -> python/ -> agents/ -> hub/ -> <root>
REPO_ROOT = HERE.parents[4]
# Editable installs are invisible to PyInstaller's static analyzer, so point it
# at the source roots directly: the email package and the core ``src`` tree.
PATHEX = [REPO_ROOT / "hub" / "agents" / "python" / "email", REPO_ROOT / "src"]

# Heavy ML stack reached only via the lazily-imported triage import graph. Real
# triage uses Lemonade over HTTP (no in-process torch), so these are excluded to
# keep the binary lean (torch alone is ~2 GB).
#
# NOTE (#1666 follow-up): the stateful agent surface (/v1/email/agent/*) hosts the
# full EmailTriageAgent, whose memory subsystem uses FAISS for the working-context
# index (embeddings still go over Lemonade HTTP, so torch/transformers stay
# excluded). ``faiss``/``faiss_cpu`` are therefore NOT excluded and are collected
# in full below. numpy is a memory.py module-level import the analyzer already
# follows.
EXCLUDES = [
    "torch",
    "transformers",
    "sentence_transformers",
    "sympy",
    "tokenizers",
    "scipy",
    "pandas",
    "matplotlib",
    "safetensors",
    "torchvision",
    "torchaudio",
]


def build(onefile: bool = False, clean: bool = True) -> Path:
    import PyInstaller.__main__

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
        # Editable-install source roots (analyzer can't see editable installs).
        "--paths",
        str(PATHEX[0]),
        "--paths",
        str(PATHEX[1]),
        # uvicorn: string-imported loops/protocols/lifespan.
        "--collect-submodules",
        "uvicorn",
        # keyring: OS backend resolution via entry points.
        "--collect-submodules",
        "keyring",
        "--copy-metadata",
        "keyring",
        # email agent: lazily-imported tool modules on the triage path.
        "--collect-submodules",
        "gaia_agent_email",
        "--copy-metadata",
        "gaia-agent-email",
        # connector provider discovery is dynamic.
        "--collect-submodules",
        "gaia.connectors",
        # FAISS backs the stateful agent's memory index (#1666). faiss-cpu ships
        # compiled libs + swig submodules the static analyzer misses, so collect
        # it wholesale. Lazily imported inside gaia.agents.base.memory.
        "--collect-all",
        "faiss",
        # core metadata (importlib.metadata version probes).
        "--copy-metadata",
        "amd-gaia",
        # pydantic v2 ships a compiled core; collect data to be safe.
        "--collect-submodules",
        "pydantic",
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    if onefile:
        args.append("--onefile")
    else:
        args.append("--onedir")

    t0 = time.time()
    PyInstaller.__main__.run(args)
    elapsed = time.time() - t0

    exe = (
        dist / (NAME + (".exe" if sys.platform == "win32" else ""))
        if onefile
        else dist / NAME / (NAME + (".exe" if sys.platform == "win32" else ""))
    )
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
    parser = argparse.ArgumentParser(description="Freeze the email REST sidecar.")
    parser.add_argument(
        "--onefile", action="store_true", help="Build a single-file executable."
    )
    args = parser.parse_args(argv)
    exe = build(onefile=args.onefile)
    return 0 if exe.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
