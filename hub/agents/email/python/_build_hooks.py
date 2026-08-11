# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build-time staging of ``gaia-agent.yaml`` into the installed package.

``gaia-agent.yaml`` lives at the package **root** — it is the hub artifact the
Worker reads on publish, and the version stamper, capability matrix, and R2
publisher all address it there. But the agent reads it at *runtime* to resolve its
declared skill sets (#2466), and in an installed wheel there is no directory above
``gaia_agent_email/`` to read it from: ``package-data`` globs cannot reach outside
the package they belong to.

So the wheel build copies it in. ``build_py`` is the standard hook for exactly
this, and it runs for every install path — ``pip install .``,
``pip install git+…#subdirectory=…``, ``python -m build``, and an editable
install — so no separate packaging step has to remember.

Wired via ``[tool.setuptools.cmdclass]`` in ``pyproject.toml``. There is
deliberately no second committed copy of the manifest: one authored file, staged
at build time, so the two can never drift.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools.command.build_py import build_py as _build_py

_PACKAGE = "gaia_agent_email"
_MANIFEST = "gaia-agent.yaml"


class build_py(_build_py):  # noqa: N801 — setuptools requires this exact name
    """``build_py`` that stages the agent manifest inside the package."""

    def run(self) -> None:
        super().run()
        self._stage_agent_manifest()

    def _stage_agent_manifest(self) -> None:
        source = Path(__file__).resolve().parent / _MANIFEST
        if not source.is_file():
            # Fail the build rather than ship a package whose agent cannot be
            # constructed — the runtime raises ManifestError on a missing
            # manifest, which would turn an install into an unusable agent.
            raise FileNotFoundError(
                f"{_MANIFEST} not found next to pyproject.toml ({source}). It is "
                "required: the built package stages it inside "
                f"{_PACKAGE}/ so the agent can resolve its declared skill sets "
                "at runtime."
            )

        target_dir = Path(self.build_lib) / _PACKAGE
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target_dir / _MANIFEST)
        self.announce(f"staged {_MANIFEST} into {_PACKAGE}/", level=2)
