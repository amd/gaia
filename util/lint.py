# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Cross-platform linting script for GAIA project.
Runs the same checks as util/lint.ps1 but works on Linux/macOS/Windows.
Used by CI and can be run locally for consistency.
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CheckResult:
    """Result of a linting check."""

    name: str
    passed: bool
    is_warning: bool  # Non-blocking check
    issues: int
    output: str


# Configuration
SRC_DIR = "src/gaia"
TEST_DIR = "tests"
INSTALLER_DIR = "installer"
PYLINT_CONFIG = ".pylintrc"
DISABLED_CHECKS = "C0103,C0301,W0246,W0221,E1102,R0401,E0401,W0718"
EXCLUDE_DIRS = (
    ".git,__pycache__,venv,.venv,.mypy_cache,.tox,.eggs,_build,buck-out,node_modules"
)


def run_command(cmd: list[str], check: bool = False) -> tuple[int, str]:
    """Run a command and return exit code and combined output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
        )
        output = result.stdout + result.stderr
        return result.returncode, output
    except subprocess.CalledProcessError as e:
        return e.returncode, e.stdout + e.stderr
    except FileNotFoundError:
        return 1, f"Command not found: {cmd[0]}"


def check_black(fix: bool = False) -> CheckResult:
    """Check code formatting with Black."""
    print("\n[1/7] Checking code formatting with Black...")
    print("-" * 40)

    if fix:
        cmd = [
            sys.executable,
            "-m",
            "black",
            INSTALLER_DIR,
            SRC_DIR,
            TEST_DIR,
            "--config",
            "pyproject.toml",
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "black",
            "--check",
            "--diff",
            INSTALLER_DIR,
            SRC_DIR,
            TEST_DIR,
            "--config",
            "pyproject.toml",
        ]

    print(f"[CMD] {' '.join(cmd)}")
    exit_code, output = run_command(cmd)

    if exit_code != 0:
        # Count files that would be reformatted
        issues = output.count("would reformat") or 1
        print(f"\n[!] Code formatting issues found.")
        if not fix:
            print("Fix with: python util/lint.py --black --fix")
        return CheckResult("Code Formatting (Black)", False, False, issues, output)

    print("[OK] Code formatting looks good!")
    return CheckResult("Code Formatting (Black)", True, False, 0, output)


def check_isort(fix: bool = False) -> CheckResult:
    """Check import sorting with isort."""
    print("\n[2/7] Checking import sorting with isort...")
    print("-" * 40)

    if fix:
        cmd = [sys.executable, "-m", "isort", INSTALLER_DIR, SRC_DIR, TEST_DIR]
    else:
        cmd = [
            sys.executable,
            "-m",
            "isort",
            "--check-only",
            "--diff",
            INSTALLER_DIR,
            SRC_DIR,
            TEST_DIR,
        ]

    print(f"[CMD] {' '.join(cmd)}")
    exit_code, output = run_command(cmd)

    if exit_code != 0:
        issues = output.count("would reformat") + output.count("ERROR") or 1
        print(f"\n[!] Import sorting issues found.")
        if not fix:
            print("Fix with: python util/lint.py --isort --fix")
        return CheckResult("Import Sorting (isort)", False, False, issues, output)

    print("[OK] Import sorting looks good!")
    return CheckResult("Import Sorting (isort)", True, False, 0, output)


def check_pylint() -> CheckResult:
    """Run Pylint (errors only)."""
    print("\n[3/7] Running Pylint (errors only)...")
    print("-" * 40)

    cmd = [
        sys.executable,
        "-m",
        "pylint",
        SRC_DIR,
        "--rcfile",
        PYLINT_CONFIG,
        "--disable",
        DISABLED_CHECKS,
    ]

    print(f"[CMD] {' '.join(cmd)}")
    exit_code, output = run_command(cmd)

    if exit_code != 0:
        # Count error lines
        import re

        issues = len(re.findall(r":\d+:\d+: [EF]\d+", output)) or 1
        print(f"\n[!] Pylint found critical errors:")
        print(output)
        return CheckResult("Critical Errors (Pylint)", False, False, issues, output)

    print("[OK] No critical Pylint errors!")
    return CheckResult("Critical Errors (Pylint)", True, False, 0, output)


def check_flake8() -> CheckResult:
    """Run Flake8."""
    print("\n[4/7] Running Flake8...")
    print("-" * 40)

    cmd = [
        sys.executable,
        "-m",
        "flake8",
        INSTALLER_DIR,
        SRC_DIR,
        TEST_DIR,
        f"--exclude={EXCLUDE_DIRS}",
        "--count",
        "--statistics",
        "--max-line-length=88",
        "--extend-ignore=E203,W503,E501,F541,W291,W293,E402,F841,E722",
    ]

    print(f"[CMD] {' '.join(cmd)}")
    exit_code, output = run_command(cmd)

    if exit_code != 0:
        # Count violation lines
        import re

        issues = len(re.findall(r"\.py:\d+:\d+: [A-Z]\d+", output)) or 1
        print(f"\n[!] Flake8 found style issues:")
        for line in output.strip().split("\n"):
            if line.strip():
                print(f"   {line}")
        return CheckResult("Style Compliance (Flake8)", False, False, issues, output)

    print("[OK] Flake8 checks passed!")
    return CheckResult("Style Compliance (Flake8)", True, False, 0, output)


def check_mypy() -> CheckResult:
    """Run MyPy type checking (warning only)."""
    print("\n[5/7] Running MyPy type checking (warning only)...")
    print("-" * 40)

    cmd = [
        sys.executable,
        "-m",
        "mypy",
        SRC_DIR,
        "--ignore-missing-imports",
    ]

    print(f"[CMD] {' '.join(cmd)}")
    exit_code, output = run_command(cmd)

    if exit_code != 0:
        # Count error lines
        import re

        issues = len(re.findall(r"\.py:\d+: error:", output)) or 1
        print(f"\n[WARNING] MyPy found type issues (non-blocking):")
        lines = output.strip().split("\n")[:20]
        for line in lines:
            print(line)
        if len(output.strip().split("\n")) > 20:
            print("... (output truncated, showing first 20 lines)")
        return CheckResult("Type Checking (MyPy)", False, True, issues, output)

    print("[OK] Type checking passed!")
    return CheckResult("Type Checking (MyPy)", True, True, 0, output)


def check_bandit() -> CheckResult:
    """Run Bandit security check (warning only)."""
    print("\n[6/7] Running security check with Bandit (warning only)...")
    print("-" * 40)

    cmd = [
        sys.executable,
        "-m",
        "bandit",
        "-r",
        SRC_DIR,
        "-ll",
        "--exclude",
        EXCLUDE_DIRS,
    ]

    print(f"[CMD] {' '.join(cmd)}")
    exit_code, output = run_command(cmd)

    if exit_code != 0:
        # Count issue lines
        issues = output.count(">> Issue:") or 1
        print(f"\n[WARNING] Bandit found security issues (non-blocking):")
        lines = output.strip().split("\n")[:30]
        for line in lines:
            print(line)
        if len(output.strip().split("\n")) > 30:
            print("... (output truncated, showing first 30 lines)")
        print("\nNote: Many are false positives for ML applications.")
        return CheckResult("Security Check (Bandit)", False, True, issues, output)

    print("[OK] No security issues found!")
    return CheckResult("Security Check (Bandit)", True, True, 0, output)


def check_imports() -> CheckResult:
    """Test critical imports."""
    print("\n[7/7] Testing critical imports...")
    print("-" * 40)

    imports = [
        ("gaia.cli", "CLI module"),
        ("gaia.chat.sdk", "Chat SDK"),
        ("gaia.llm.llm_client", "LLM client"),
        ("gaia.agents.base.agent", "Base agent"),
    ]

    failed = False
    issues = 0

    for module, desc in imports:
        cmd = [sys.executable, "-c", f"import {module}; print('OK: {desc} imports')"]
        print(f"[CMD] {' '.join(cmd)}")
        exit_code, output = run_command(cmd)
        print(output.strip())
        if exit_code != 0:
            print(f"[!] Failed to import {module}")
            failed = True
            issues += 1

    if failed:
        return CheckResult("Import Validation", False, False, issues, "")

    print("[OK] All imports working!")
    return CheckResult("Import Validation", True, False, 0, "")


def count_python_files() -> tuple[int, int]:
    """Count Python files and lines of code."""
    total_files = 0
    total_lines = 0

    for dir_name in [SRC_DIR, TEST_DIR, INSTALLER_DIR]:
        dir_path = Path(dir_name)
        if dir_path.exists():
            for py_file in dir_path.rglob("*.py"):
                total_files += 1
                try:
                    total_lines += len(py_file.read_text(encoding="utf-8").splitlines())
                except Exception:
                    pass

    return total_files, total_lines


def print_summary(results: list[CheckResult]) -> int:
    """Print summary table and return exit code."""
    print("\n")
    print("=" * 64)
    print("                    LINT SUMMARY REPORT                        ")
    print("=" * 64)

    # Print statistics
    total_files, total_lines = count_python_files()
    print()
    print("[STATS] Project Statistics:")
    print(f"   - Python Files: {total_files}")
    print(f"   - Lines of Code: {total_lines:,}")
    print(f"   - Directories: {SRC_DIR}, {TEST_DIR}, {INSTALLER_DIR}")

    # Build results
    pass_count = 0
    fail_count = 0
    warn_count = 0

    print()
    print("[RESULTS] Quality Check Results:")
    print()
    print("+" + "-" * 32 + "+" + "-" * 12 + "+" + "-" * 11 + "+")
    print("| Check                          | Status     | Issues    |")
    print("+" + "-" * 32 + "+" + "-" * 12 + "+" + "-" * 11 + "+")

    for result in results:
        if result.passed:
            status = "[OK] PASS"
            issue_str = "-"
            pass_count += 1
        elif result.is_warning:
            status = "[!] WARN"
            issue_str = f"{result.issues} warns" if result.issues != 1 else "1 warning"
            warn_count += 1
        else:
            status = "[X] FAIL"
            issue_str = f"{result.issues} errors" if result.issues != 1 else "1 error"
            fail_count += 1

        print(f"| {result.name:<30} | {status:<10} | {issue_str:<9} |")

    print("+" + "-" * 32 + "+" + "-" * 12 + "+" + "-" * 11 + "+")

    # Print statistics
    total_checks = len(results)
    print()
    print("[SUMMARY] Statistics:")
    print(f"   - Total Checks Run: {total_checks}")
    print(
        f"   - Passed: {pass_count} ({round(pass_count/total_checks*100, 1) if total_checks else 0}%)"
    )
    print(
        f"   - Failed: {fail_count} ({round(fail_count/total_checks*100, 1) if total_checks else 0}%)"
    )
    print(
        f"   - Warnings: {warn_count} ({round(warn_count/total_checks*100, 1) if total_checks else 0}%)"
    )

    # Print final verdict
    print()
    print("=" * 60)
    if fail_count == 0:
        print("[SUCCESS] ALL QUALITY CHECKS PASSED!")
        if warn_count > 0:
            print(f"[WARNING] {warn_count} warning(s) found (non-blocking)")
        print("=" * 60)
        print()
        print("[OK] Your code meets quality standards!")
        print("[OK] Ready for PR submission")
        return 0
    else:
        print("[FAILED] QUALITY CHECKS FAILED")
        print("=" * 60)
        print()
        print("[ERROR] Issues Found:")
        print(f"   - {fail_count} critical error(s) - must fix before PR")
        if warn_count > 0:
            print(f"   - {warn_count} warning(s) - non-blocking")
        print()
        print("[TIP] Review the error messages above and fix the issues.")
        print("[TIP] Use --fix flag to auto-fix formatting issues:")
        print("   python util/lint.py --black --fix")
        print("   python util/lint.py --isort --fix")
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run code quality checks for GAIA project"
    )
    parser.add_argument("--black", action="store_true", help="Run Black formatter")
    parser.add_argument("--isort", action="store_true", help="Run isort")
    parser.add_argument("--pylint", action="store_true", help="Run Pylint")
    parser.add_argument("--flake8", action="store_true", help="Run Flake8")
    parser.add_argument("--mypy", action="store_true", help="Run MyPy")
    parser.add_argument("--bandit", action="store_true", help="Run Bandit")
    parser.add_argument("--imports", action="store_true", help="Test imports")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    parser.add_argument(
        "--fix", action="store_true", help="Auto-fix issues where possible"
    )
    args = parser.parse_args()

    # If no specific checks are requested, run all
    run_all = args.all or not any(
        [
            args.black,
            args.isort,
            args.pylint,
            args.flake8,
            args.mypy,
            args.bandit,
            args.imports,
        ]
    )

    print("=" * 40)
    print("Running Code Quality Checks")
    print("=" * 40)

    results: list[CheckResult] = []

    if args.black or run_all:
        results.append(check_black(args.fix))

    if args.isort or run_all:
        results.append(check_isort(args.fix))

    if args.pylint or run_all:
        results.append(check_pylint())

    if args.flake8 or run_all:
        results.append(check_flake8())

    if args.mypy or run_all:
        results.append(check_mypy())

    if args.imports or run_all:
        results.append(check_imports())

    if args.bandit or run_all:
        results.append(check_bandit())

    exit_code = print_summary(results)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
