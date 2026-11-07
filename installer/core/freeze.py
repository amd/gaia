# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Freeze the GAIA core (``gaia.cli:main``) into the one-dir ``gaia`` executable
the desktop app ships, so a user's PC never installs a Python package.

It holds what ``pip install amd-gaia[ui]`` installs today: the CLI, the Agent UI
backend (FastAPI, RAG indexing, connectors), the daemon and ``gaia init``. It
leaves out the flagship agent, which runs as its own hub binary, so ``gaia eval
agent``, which runs that agent in-process, works only from source.

Usage (from a venv with ``.[ui]`` and pyinstaller installed)::

    python installer/core/freeze.py

Output: ``installer/core/dist/gaia/gaia[.exe]`` plus its ``_internal`` folder.

Design notes:
- One-dir, not one-file: the CLI and the daemon start often, and a one-file
  build unpacks itself to a temp folder, scanned by antivirus, on every start.
- Every ``gaia`` submodule is collected. The CLI imports most commands inside
  functions and the connectors, tool mixins and providers by name, so the
  analyzer alone would ship a build that fails on the first command it missed.
- GAIA relaunches itself as ``sys.executable -m <module>``, which
  ``gaia.frozen.dispatch`` handles in the frozen app; those modules are
  collected like the rest.
- The ML stack (torch, transformers, ...) is excluded, as in the agent's freeze:
  every model runs in Lemonade, over HTTP.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SRC = REPO_ROOT / "src"
ENTRY = HERE / "entry.py"
NAME = "gaia"
AGENT_PACKAGING = REPO_ROOT / "hub" / "agents" / "gaia" / "python" / "packaging"

sys.path.insert(0, str(SRC))
from gaia.utils import tiktoken_bundle  # noqa: E402

#: Third-party licence notices for the libraries the executable bundles.
NOTICES = [
    (REPO_ROOT / "THIRD_PARTY_NOTICES.md", "."),
    (REPO_ROOT / "licenses", "licenses"),
]
#: Package data a source checkout reads beside the modules. ``version.py`` too:
#: the daemon records its path, and other clients read the version from it.
PACKAGE_DATA = [
    (SRC / "gaia" / "version.py", "gaia"),
    (SRC / "gaia" / "img", "gaia/img"),
    (SRC / "gaia" / "mcp" / "mcp.json", "gaia/mcp"),
    (SRC / "gaia" / "mcp" / "n8n.json", "gaia/mcp"),
]

#: Never in the frozen core: the hub agents, which run as their own binaries
#: through the daemon. The chat paths relay to them when they can't be imported.
NOT_SHIPPED = ["gaia_agent", "gaia_agent_email"]

COLLECT_SUBMODULES = ["uvicorn", "keyring", "pydantic"]
COLLECT_ALL = ["faiss"]
COPY_METADATA = ["amd-gaia", "keyring"]
HIDDEN_IMPORTS = [tiktoken_bundle.PLUGIN_MODULE]

# Same as the agent's freeze: GPL code GAIA must not ship, and the ML stack no
# GAIA path runs in-process.
GPL_EXCLUDES = [
    "PyInstaller",
    "altgraph",
    "pefile",
    "macholib",
    "readline",
    "_gdbm",
    "_dbm",
    "dbm.gnu",
    "dbm.ndbm",
]
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
    *GPL_EXCLUDES,
    *NOT_SHIPPED,
]


def is_excluded(name: str) -> bool:
    """True when *name* is, or sits under, a module in :data:`EXCLUDES`."""
    return any(name == ex or name.startswith(ex + ".") for ex in EXCLUDES)


def _verify_freeze_env() -> None:
    """Fail the build when something the executable must carry is not installed.

    PyInstaller turns a missing collect target into a warning and ships an
    executable without it, and a frozen app has no interpreter to add it later.
    """
    missing = [
        mod
        for mod in COLLECT_SUBMODULES + COLLECT_ALL + ["fastapi", "pypdf", "pypdfium2"]
        if importlib.util.find_spec(mod) is None
    ]
    if missing:
        raise SystemExit(
            "freeze: refusing to build -- these modules must be in the executable "
            f"but are not installed: {', '.join(missing)}.\n"
            'Install the core with its UI extras (uv pip install -e ".[ui]" '
            "pyinstaller) into the freeze environment and re-run."
        )
    origin = Path(importlib.util.find_spec("gaia").origin).resolve()
    if SRC.resolve() not in origin.parents:
        raise SystemExit(
            f"freeze: `gaia` imports from {origin.parent}, not {SRC / 'gaia'}.\n"
            "The executable would carry another checkout's code. Install this "
            'checkout (uv pip install -e ".[ui]") into the freeze environment.'
        )


def gaia_hidden_imports() -> list[str]:
    """Every ``gaia`` module except :data:`NOT_SHIPPED`, from the source tree."""
    names = []
    for root, dirs, files in os.walk(SRC / "gaia"):
        # Only packages: the webui and example apps also hold node tooling.
        dirs[:] = sorted(d for d in dirs if (Path(root) / d / "__init__.py").is_file())
        package = ".".join(Path(root).relative_to(SRC).parts)
        for file in sorted(files):
            if not file.endswith(".py"):
                continue
            stem = file[: -len(".py")]
            name = package if stem == "__init__" else f"{package}.{stem}"
            if not is_excluded(name):
                names.append(name)
    return names


def build(clean: bool = True) -> Path:
    import PyInstaller.__main__

    _verify_freeze_env()
    work = HERE / "build"
    dist = HERE / "dist"
    if clean:
        for d in (work, dist):
            if d.exists():
                shutil.rmtree(d)
    add_data = [*NOTICES, *PACKAGE_DATA]
    for source, _ in add_data:
        if not source.exists():
            raise SystemExit(f"freeze: required bundle data is missing: {source}")
    tiktoken_cache = tiktoken_bundle.stage_cache(work / tiktoken_bundle.BUNDLE_DIR)
    add_data.append((tiktoken_cache, tiktoken_bundle.BUNDLE_DIR))

    args = [
        str(ENTRY),
        "--name",
        NAME,
        "--console",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(HERE),
        "--paths",
        str(SRC),
    ]
    for mod in COLLECT_SUBMODULES:
        args += ["--collect-submodules", mod]
    for mod in COLLECT_ALL:
        args += ["--collect-all", mod]
    for mod in sorted(set(HIDDEN_IMPORTS + gaia_hidden_imports())):
        args += ["--hidden-import", mod]
    for distribution in COPY_METADATA:
        args += ["--copy-metadata", distribution]
    for source, dest in add_data:
        args += ["--add-data", f"{source}{os.pathsep}{dest}"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    if sys.platform.startswith("linux"):
        args += ["--runtime-hook", str(AGENT_PACKAGING / "rthook_linux_ca.py")]

    started = time.time()
    PyInstaller.__main__.run(args)
    exe = dist / NAME / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.is_file():
        raise SystemExit(f"freeze: PyInstaller finished but {exe} is missing.")
    size = sum(p.stat().st_size for p in (dist / NAME).rglob("*") if p.is_file())
    print(f"\nBuilt {exe} in {time.time() - started:.0f}s, {size / 1e6:.0f} MB on disk")
    return exe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.parse_args(argv)
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
