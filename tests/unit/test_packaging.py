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
    "node_modules",
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


def _parse_setup_install_requires():
    """Extract the base install_requires package names (lowercased) from setup.py."""
    content = SETUP_PY.read_text()
    match = re.search(r"install_requires\s*=\s*\[(.*?)\]", content, re.DOTALL)
    assert match, "Could not find install_requires=[] in setup.py"
    raw = match.group(1)
    names = set()
    for spec in re.findall(r'"([^"]+)"', raw):
        # Strip version specifiers / markers; keep just the distribution name.
        name = re.split(r"[<>=!~; ]", spec, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


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


class TestBaseDependencies:
    """Base console_scripts must work on a plain `pip install amd-gaia`."""

    def test_gaia_mcp_module_imports_are_base_deps(self):
        """`gaia-mcp` is a base console_script whose module imports
        ``python_multipart`` at load time. The dist must be in base
        install_requires (not only extras), or a plain `pip install amd-gaia`
        ships a broken gaia-mcp. Regression for the v0.20.0 post-publish smoke
        failure (ModuleNotFoundError: python_multipart)."""
        bridge = (SRC_DIR / "gaia" / "mcp" / "mcp_bridge.py").read_text(
            encoding="utf-8"
        )
        base = _parse_setup_install_requires()
        # Only enforce while the import is top-level; a future lazy import would
        # legitimately move the dep into an extra.
        top_level_multipart = re.search(
            r"^(from|import) python_multipart", bridge, re.MULTILINE
        )
        if top_level_multipart:
            assert "python-multipart" in base, (
                "gaia.mcp.mcp_bridge imports python_multipart at module top level "
                "and gaia-mcp is a base console_script, but 'python-multipart' is "
                "not in setup.py install_requires (only extras). A plain "
                "`pip install amd-gaia` would ship a broken gaia-mcp entry point."
            )


def _parse_extras_require_block():
    """Extract the raw text inside extras_require={...} from setup.py."""
    content = SETUP_PY.read_text()
    match = re.search(
        r"extras_require\s*=\s*\{(.*?)\n    \},\n    classifiers=", content, re.DOTALL
    )
    assert match, "Could not find extras_require={} in setup.py"
    return match.group(1)


class TestAgentWheelExtras:
    """Regression guard for #2240.

    The gaia-agent-* wheels (chat, email, code, ...) aren't published to
    PyPI yet (publish_agents.yml's publish job is gated off). An 'agents'
    extra -- or any 'agent-<id>' extra -- naming one of those packages
    makes that *whole* amd-gaia release unsatisfiable, so pip/uv silently
    backtrack past it to the newest older release that doesn't declare the
    extra. That's what turned `pip install "amd-gaia[agents]"` into a
    silent downgrade from 0.22.0 to 0.20.0. Don't re-add these extras until
    the wheels are actually live on PyPI.
    """

    def test_no_agents_extra_declared(self):
        extras_keys = re.findall(
            r'"([a-zA-Z0-9_-]+)"\s*:', _parse_extras_require_block()
        )
        broken = {k for k in extras_keys if k == "agents" or k.startswith("agent-")}
        assert not broken, (
            f"setup.py declares extras {sorted(broken)} -- this is exactly "
            "the shape that caused #2240's silent amd-gaia downgrade. Don't "
            "re-add until the gaia-agent-* wheels are live on PyPI."
        )

    def test_no_extras_reference_unpublished_agent_wheels(self):
        # Drop comment lines first -- e.g. the "install from source" hint
        # left in place of the removed extras legitimately mentions
        # gaia-agent-* as a string, not as a live dependency spec.
        code_lines = (
            line
            for line in _parse_extras_require_block().splitlines()
            if not line.strip().startswith("#")
        )
        extras_block = "\n".join(code_lines)
        assert "gaia-agent-" not in extras_block, (
            "An extras_require value references a gaia-agent-* wheel; those "
            "aren't published to PyPI yet (#2240), so any extra naming one "
            "makes amd-gaia unsatisfiable at that version and pip/uv "
            "silently backtrack-downgrade to an older release instead."
        )
