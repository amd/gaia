# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every module under ``src/gaia`` must import.

A module that cannot be imported is dead code that still looks alive: nothing
calls it, so no other test notices, and a reader has no way to tell. The one
acceptable failure is a missing *optional* extra — a module that needs
``[mcp]`` cannot import in a job that installed only ``[api]``. Those are
allowlisted by the package that is missing, not by module, so an allowlisted
module still fails here if it breaks for any other reason.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
SETUP_PY = REPO_ROOT / "setup.py"

# Missing top-level import name -> the setup.py distribution that provides it.
# Each must be an optional extra, never a base dependency.
OPTIONAL_IMPORTS = {
    "mcp": "mcp",
    "reportlab": "reportlab",
}

# Runs in a child interpreter so module-level side effects never touch the
# pytest process. Package modules import by dotted name; a .py file outside a
# package (no __init__.py chain) is loaded by path, so a stray file with
# relative imports fails here instead of hiding.
_CHILD = r"""
import importlib
import importlib.util
import json
import sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(src))
skip = {"node_modules", "__pycache__"}
failures = {}
for path in sorted((src / "gaia").rglob("*.py")):
    rel = path.relative_to(src)
    if skip.intersection(rel.parts):
        continue
    parents = [src.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts))]
    try:
        if all((p / "__init__.py").is_file() for p in parents):
            name = ".".join(rel.with_suffix("").parts)
            importlib.import_module(name.removesuffix(".__init__"))
        else:
            spec = importlib.util.spec_from_file_location(f"_loose_{path.stem}", path)
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
    except BaseException as exc:  # report every failure, including SystemExit
        failures[rel.as_posix()] = {
            "type": type(exc).__name__,
            "missing": getattr(exc, "name", None),
            "message": str(exc),
        }
out.write_text(json.dumps(failures), encoding="utf-8")
"""


def _is_optional_extra_missing(failure: dict) -> bool:
    if failure["type"] != "ModuleNotFoundError" or not failure["missing"]:
        return False
    top_level = failure["missing"].split(".")[0]
    return top_level != "gaia" and top_level in OPTIONAL_IMPORTS


def _import_failures(tmp_path: Path) -> dict:
    out = tmp_path / "failures.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), *filter(None, [env.get("PYTHONPATH")])]
    )
    env["GAIA_HOME"] = str(tmp_path / "gaia_home")
    env["GAIA_DAEMON_HOME"] = str(tmp_path / "daemon_home")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(SRC_DIR), str(out)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    assert (
        proc.returncode == 0 and out.is_file()
    ), f"import sweep crashed (exit {proc.returncode}):\n{proc.stderr[-4000:]}"
    return json.loads(out.read_text(encoding="utf-8"))


def test_every_gaia_module_imports(tmp_path):
    failures = _import_failures(tmp_path)
    broken = {
        module: failure
        for module, failure in failures.items()
        if not _is_optional_extra_missing(failure)
    }
    assert not broken, (
        "These modules under src/gaia cannot be imported. Fix the import, or "
        "delete the module if nothing uses it. If it only needs an optional "
        "extra, add the missing package to OPTIONAL_IMPORTS in this file:\n"
        + "\n".join(
            f"  {module}: {f['type']}: {f['message']}"
            for module, f in sorted(broken.items())
        )
    )


def test_allowlisted_imports_are_optional_extras():
    content = SETUP_PY.read_text(encoding="utf-8")
    base = re.search(r"install_requires\s*=\s*\[(.*?)\]", content, re.DOTALL)
    assert base, "Could not find install_requires=[] in setup.py"
    for import_name, dist in OPTIONAL_IMPORTS.items():
        requirement = re.compile(rf'"{re.escape(dist)}(?=[<>=!~;\[ "])', re.I)
        assert requirement.search(content), (
            f"OPTIONAL_IMPORTS allows '{import_name}' to be missing, but "
            f"'{dist}' is not declared in any setup.py extra"
        )
        assert not requirement.search(base.group(1)), (
            f"'{dist}' is a base dependency, so a missing '{import_name}' is a "
            "broken install, not an optional extra; drop it from OPTIONAL_IMPORTS"
        )
