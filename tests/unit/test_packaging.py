# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Packaging integrity tests.

Ensures that setup.py packages list, __init__.py files, and entry points
are consistent. Catches missing packages that only break on non-editable installs.
"""

import re
from pathlib import Path

# Project root and source directory
PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
SETUP_PY = PROJECT_ROOT / "setup.py"

# Directories that are intentionally NOT packages (no __init__.py needed)
EXCLUDED_DIRS = {
    "tests",
    "examples",
    "__pycache__",
}


def _parse_setup_packages():
    """Extract packages list from setup.py."""
    content = SETUP_PY.read_text()
    # Match the packages=[...] block
    match = re.search(r"packages\s*=\s*\[(.*?)\]", content, re.DOTALL)
    assert match, "Could not find packages=[] in setup.py"
    raw = match.group(1)
    # Extract all quoted strings
    return set(re.findall(r'"([^"]+)"', raw))


def _parse_setup_entry_points():
    """Extract console_scripts entry points from setup.py."""
    content = SETUP_PY.read_text()
    match = re.search(r'"console_scripts"\s*:\s*\[(.*?)\]', content, re.DOTALL)
    if not match:
        return []
    raw = match.group(1)
    entries = re.findall(r'"(.+?)"', raw)
    result = []
    for entry in entries:
        # Format: "name = module:func"
        parts = entry.split("=", 1)
        if len(parts) == 2:
            name = parts[0].strip()
            module_func = parts[1].strip()
            if ":" in module_func:
                module, func = module_func.rsplit(":", 1)
                result.append((name, module.strip(), func.strip()))
    return result


def _find_filesystem_packages():
    """Find all directories under src/gaia/ that contain __init__.py."""
    packages = set()
    gaia_src = SRC_DIR / "gaia"
    for init_file in gaia_src.rglob("__init__.py"):
        pkg_dir = init_file.parent
        # Convert path to dotted package name
        rel = pkg_dir.relative_to(SRC_DIR)
        pkg_name = ".".join(rel.parts)
        # Skip excluded directories
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        packages.add(pkg_name)
    return packages


class TestPackagingIntegrity:
    """Verify setup.py packages list matches the filesystem."""

    def test_all_filesystem_packages_in_setup_py(self):
        """Every directory with __init__.py must be declared in setup.py packages."""
        setup_packages = _parse_setup_packages()
        fs_packages = _find_filesystem_packages()

        missing = fs_packages - setup_packages
        assert not missing, (
            f"Packages exist on disk but are missing from setup.py packages=[]:\n"
            f"  {sorted(missing)}\n"
            f"These will not be installed in non-editable (pip install .) builds."
        )

    def test_all_setup_packages_exist_on_disk(self):
        """Every package in setup.py must have a directory with __init__.py or .py files."""
        setup_packages = _parse_setup_packages()

        missing = []
        for pkg in sorted(setup_packages):
            pkg_path = SRC_DIR / pkg.replace(".", "/")
            has_init = (pkg_path / "__init__.py").exists()
            has_py_files = any(pkg_path.glob("*.py")) if pkg_path.is_dir() else False
            if not has_init and not has_py_files:
                missing.append(pkg)

        assert not missing, (
            f"Packages declared in setup.py but missing on disk:\n"
            f"  {missing}\n"
            f"Either create the package or remove it from setup.py."
        )

    def test_all_setup_packages_have_init_py(self):
        """Every package in setup.py should have an __init__.py for non-editable installs."""
        setup_packages = _parse_setup_packages()

        missing_init = []
        for pkg in sorted(setup_packages):
            pkg_path = SRC_DIR / pkg.replace(".", "/")
            if pkg_path.is_dir() and not (pkg_path / "__init__.py").exists():
                missing_init.append(pkg)

        assert not missing_init, (
            f"Packages in setup.py missing __init__.py (will fail on non-editable install):\n"
            f"  {missing_init}\n"
            f"Create __init__.py files for these packages."
        )


class TestEntryPoints:
    """Verify that all console_scripts entry points resolve to real files."""

    def test_entry_point_modules_exist_on_disk(self):
        """Every entry point module file must exist on disk."""
        entry_points = _parse_setup_entry_points()
        assert entry_points, "No entry points found in setup.py"

        failures = []
        for name, module, func in entry_points:
            # Convert dotted module path to file path
            module_path = SRC_DIR / module.replace(".", "/")
            py_file = module_path.with_suffix(".py")
            pkg_init = module_path / "__init__.py"

            if not py_file.exists() and not pkg_init.exists():
                failures.append(
                    f"  {name}: module '{module}' not found "
                    f"(checked {py_file} and {pkg_init})"
                )

        assert not failures, f"Entry point modules missing on disk:\n" + "\n".join(
            failures
        )

    def test_entry_point_functions_exist_in_source(self):
        """Every entry point function must be defined in its module source."""
        entry_points = _parse_setup_entry_points()

        failures = []
        for name, module, func in entry_points:
            module_path = SRC_DIR / module.replace(".", "/")
            py_file = module_path.with_suffix(".py")

            if py_file.exists():
                source = py_file.read_text(encoding="utf-8")
                # Check for "def func_name" in the source
                if not re.search(rf"^def {func}\s*\(", source, re.MULTILINE):
                    failures.append(
                        f"  {name}: function '{func}' not found in {py_file}"
                    )

        assert (
            not failures
        ), f"Entry point functions not found in source:\n" + "\n".join(failures)

    def test_entry_point_parent_packages_declared(self):
        """Entry point module's parent package must be in setup.py packages."""
        entry_points = _parse_setup_entry_points()
        setup_packages = _parse_setup_packages()

        failures = []
        for name, module, func in entry_points:
            # Get the parent package (e.g., "gaia.mcp" from "gaia.mcp.mcp_bridge")
            parts = module.rsplit(".", 1)
            if len(parts) == 2:
                parent_pkg = parts[0]
                if parent_pkg not in setup_packages:
                    failures.append(
                        f"  {name}: parent package '{parent_pkg}' "
                        f"not in setup.py packages"
                    )

        assert (
            not failures
        ), f"Entry point parent packages missing from setup.py:\n" + "\n".join(failures)
