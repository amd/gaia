# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Uninstall Command

Implements ``gaia uninstall`` with tiered cleanup. Designed to be the single
implementation called by every desktop installer platform (NSIS, DMG, DEB,
AppImage) via a subprocess call.

Tiers:
    * Default (no flags)  — print a friendly help message, touch nothing.
      Tier 1 (removing the Electron app binaries / shortcuts / registry
      entries) is always the responsibility of the platform's native
      uninstaller; this command only owns the shared Python-side state.
    * ``--venv``          — Tier 2: remove ``~/.gaia/venv/``.
    * ``--purge``         — Tier 3: venv + chat data + documents + electron
      config + install logs / state files. Always keeps ``~/.gaia/`` itself
      so other tools that store data there (MCP config, etc.) are preserved.

Opt-in extras (only valid alongside ``--purge``):
    * ``--purge-lemonade`` — best-effort Lemonade Server removal.
    * ``--purge-models``   — remove ``~/.cache/lemonade/models/``.
    * ``--purge-hf-cache`` — remove the HuggingFace hub cache (restores the
      legacy ``gaia uninstall --models`` cleanup capability).

Flags:
    * ``--dry-run`` — print paths that would be removed, touch nothing.
    * ``--yes``     — skip the interactive confirmation prompt. Required for
      CI / scripted use. Auto-detected when ``stdin`` is not a TTY so that
      silent NSIS uninstall and Debian ``postrm`` flows work without the flag.

Exit codes:
    * 0 — success, dry-run, or no-op
    * 1 — user aborted at the confirmation prompt
    * 2 — filesystem error (permission denied, unreadable path, ...)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_FS_ERROR = 2


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _gaia_home(home: Optional[Path] = None) -> Path:
    """Return ``~/.gaia`` using ``pathlib`` for cross-platform correctness."""
    base = home if home is not None else Path.home()
    return base / ".gaia"


def _lemonade_models_dir(home: Optional[Path] = None) -> Path:
    """Return the Lemonade models cache directory.

    On Windows the cache lives under ``%LOCALAPPDATA%\\lemonade\\models``
    when that env var is set; otherwise we fall back to the
    POSIX-style ``~/.cache/lemonade/models`` which also works on macOS and
    Linux.
    """
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "lemonade" / "models"
    base = home if home is not None else Path.home()
    return base / ".cache" / "lemonade" / "models"


def _huggingface_cache_dir(home: Optional[Path] = None) -> Path:
    """Return the HuggingFace hub cache directory.

    Many GAIA models (sentence-transformers, embedding models, etc.) are
    downloaded into the standard HuggingFace cache rather than Lemonade's
    cache. The location follows the HF convention:

      * ``HF_HOME/hub`` if ``HF_HOME`` is set
      * Otherwise ``~/.cache/huggingface/hub`` on POSIX and macOS
      * Otherwise ``%LOCALAPPDATA%\\huggingface\\hub`` on Windows when set

    Restoring the cleanup capability that the legacy
    ``gaia uninstall --models`` flag provided before Phase D's refactor.
    """
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home) / "hub"
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "huggingface" / "hub"
    base = home if home is not None else Path.home()
    return base / ".cache" / "huggingface" / "hub"


def _venv_paths(home: Optional[Path] = None) -> List[Path]:
    """Paths that belong to Tier 2 (``--venv``)."""
    gaia = _gaia_home(home)
    return [gaia / "venv"]


def _purge_paths(home: Optional[Path] = None) -> List[Path]:
    """Paths that belong to Tier 3 (``--purge``).

    ``--purge`` implies ``--venv``. We keep ``~/.gaia/`` itself so other
    tooling that lives alongside us (e.g. MCP config) is preserved.
    """
    gaia = _gaia_home(home)
    return [
        gaia / "venv",
        gaia / "chat",
        gaia / "documents",
        gaia / "electron-config.json",
        gaia / "gaia.log",
        gaia / "electron-install-state.json",
        gaia / "electron-install.log",
    ]


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


@dataclass
class UninstallPlan:
    """What an invocation of ``gaia uninstall`` intends to do.

    ``tiered_paths`` is a list of ``(tier_label, path)`` tuples so dry-run
    output can show *why* a path was selected.
    """

    tiered_paths: List[tuple] = field(default_factory=list)
    purge_lemonade: bool = False
    purge_models_path: Optional[Path] = None
    purge_hf_cache_path: Optional[Path] = None

    def unique_paths(self) -> List[Path]:
        """Return deduplicated paths preserving the order they were added."""
        seen = set()
        out: List[Path] = []
        for _, path in self.tiered_paths:
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
        return out

    def is_empty(self) -> bool:
        return (
            not self.tiered_paths
            and not self.purge_lemonade
            and self.purge_models_path is None
            and self.purge_hf_cache_path is None
        )


def build_plan(
    *,
    venv: bool,
    purge: bool,
    purge_lemonade: bool,
    purge_models: bool,
    purge_hf_cache: bool = False,
    home: Optional[Path] = None,
) -> UninstallPlan:
    """Build an :class:`UninstallPlan` from the parsed flag set.

    Assumes validation has already happened (``--purge-lemonade`` /
    ``--purge-models`` / ``--purge-hf-cache`` must come with ``--purge``).
    ``--purge`` wins over ``--venv`` if both are passed.
    """
    plan = UninstallPlan()

    if purge:
        for path in _purge_paths(home):
            plan.tiered_paths.append(("--purge", path))
    elif venv:
        for path in _venv_paths(home):
            plan.tiered_paths.append(("--venv", path))

    if purge_lemonade:
        plan.purge_lemonade = True

    if purge_models:
        plan.purge_models_path = _lemonade_models_dir(home)

    if purge_hf_cache:
        plan.purge_hf_cache_path = _huggingface_cache_dir(home)

    return plan


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print(msg: str = "", *, printer: Callable[[str], None] = print) -> None:
    printer(msg)


def _print_no_flags_help(printer: Callable[[str], None] = print) -> None:
    """Friendly message shown when ``gaia uninstall`` runs with no flags."""
    lines = [
        "",
        "gaia uninstall — tiered cleanup of the GAIA Python install.",
        "",
        "This command intentionally does nothing without a flag. The Electron",
        "app uninstaller removes the app binaries and shortcuts (Tier 1); this",
        "CLI only owns the shared Python state under ~/.gaia/ and the",
        "Lemonade cache.",
        "",
        "Choose a tier:",
        "  gaia uninstall --venv            Remove ~/.gaia/venv/ (Tier 2)",
        "  gaia uninstall --purge           Remove venv + chat data + documents +",
        "                                   electron config + install logs (Tier 3)",
        "",
        "Optional extras (must be combined with --purge):",
        "  --purge-lemonade                 Also uninstall Lemonade Server",
        "  --purge-models                   Also remove Lemonade's models cache",
        "  --purge-hf-cache                 Also remove the HuggingFace hub cache",
        "",
        "Safety:",
        "  --dry-run                        Show what would be removed, touch nothing",
        "  --yes, -y                        Skip the confirmation prompt",
        "",
        "Tip: run `gaia uninstall --dry-run --purge` first to preview everything.",
        "",
    ]
    for line in lines:
        _print(line, printer=printer)


def _print_plan(
    plan: UninstallPlan,
    *,
    dry_run: bool,
    printer: Callable[[str], None] = print,
) -> None:
    header = "[dry-run] Would remove:" if dry_run else "The following will be removed:"
    _print(header, printer=printer)
    if plan.tiered_paths:
        for label, path in plan.tiered_paths:
            _print(f"  ({label}) {path}", printer=printer)
    if plan.purge_lemonade:
        _print("  (--purge-lemonade) Lemonade Server (best-effort)", printer=printer)
    if plan.purge_models_path is not None:
        _print(
            f"  (--purge-models) {plan.purge_models_path}",
            printer=printer,
        )
    if plan.purge_hf_cache_path is not None:
        _print(
            f"  (--purge-hf-cache) {plan.purge_hf_cache_path}",
            printer=printer,
        )
    _print(printer=printer)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def _should_skip_prompt(yes: bool) -> bool:
    """Return True if we should not show an interactive prompt.

    Explicit ``--yes`` always skips. Non-TTY stdin also skips so that silent
    uninstallers (NSIS, Debian ``postrm``, CI) work without the flag.
    """
    if yes:
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        # E.g. stdin closed; treat as non-interactive.
        return True


def _confirm(
    prompt: str = "Continue? [y/N]: ",
    *,
    input_fn: Callable[[str], str] = input,
) -> bool:
    try:
        answer = input_fn(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# Removal primitives
# ---------------------------------------------------------------------------


def _remove_path(path: Path, *, printer: Callable[[str], None] = print) -> bool:
    """Remove ``path`` if it exists. Returns True on success or no-op.

    Any :class:`OSError` (including ``PermissionError``) is caught and
    reported; the caller decides whether to escalate to an error exit code.
    """
    try:
        if not path.exists() and not path.is_symlink():
            _print(f"  [skip] {path} (does not exist)", printer=printer)
            return True
    except OSError as exc:
        _print(f"  [error] could not stat {path}: {exc}", printer=printer)
        return False

    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            _print(f"  [skip] {path} (unsupported file type)", printer=printer)
            return True
    except PermissionError as exc:
        _print(f"  [error] permission denied removing {path}: {exc}", printer=printer)
        return False
    except OSError as exc:
        _print(f"  [error] failed to remove {path}: {exc}", printer=printer)
        return False

    _print(f"  [removed] {path}", printer=printer)
    return True


def _remove_lemonade(printer: Callable[[str], None] = print) -> bool:
    """Best-effort Lemonade Server removal.

    Never fails the command — returns True even if Lemonade could not be
    removed, so the rest of the uninstall flow proceeds.
    """
    _print("  [lemonade] attempting to uninstall Lemonade Server...", printer=printer)

    # Strategy 1: the shared LemonadeInstaller class knows how to uninstall
    # on Windows (msiexec) and Linux (apt / dpkg).
    try:
        from gaia.installer.lemonade_installer import LemonadeInstaller

        installer = LemonadeInstaller()
        info = installer.check_installation()
        if not info.installed:
            _print(
                "  [lemonade] not installed — nothing to do",
                printer=printer,
            )
            return True

        result = installer.uninstall(silent=True)
        if result.success:
            _print(
                "  [lemonade] uninstalled via LemonadeInstaller",
                printer=printer,
            )
            return True
        _print(
            f"  [lemonade] LemonadeInstaller.uninstall failed: {result.error}",
            printer=printer,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _print(
            f"  [lemonade] LemonadeInstaller unavailable: {exc}",
            printer=printer,
        )

    # Strategy 2: pip uninstall, in case Lemonade was installed via pip.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "lemonade-sdk"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0:
            _print(
                "  [lemonade] removed via `pip uninstall lemonade-sdk`",
                printer=printer,
            )
            return True
        _print(
            "  [lemonade] pip uninstall did not remove lemonade-sdk "
            f"(exit {result.returncode}); giving up cleanly",
            printer=printer,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _print(
            f"  [lemonade] pip uninstall unavailable: {exc}",
            printer=printer,
        )

    _print(
        "  [lemonade] could not auto-remove Lemonade Server. If you want it "
        "gone, uninstall it manually via your OS package manager.",
        printer=printer,
    )
    return True  # best-effort — never fail the command


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_plan(
    plan: UninstallPlan,
    *,
    printer: Callable[[str], None] = print,
) -> int:
    """Execute a plan and return the exit code."""
    all_ok = True

    for path in plan.unique_paths():
        if not _remove_path(path, printer=printer):
            all_ok = False

    if plan.purge_models_path is not None:
        if not _remove_path(plan.purge_models_path, printer=printer):
            all_ok = False

    if plan.purge_hf_cache_path is not None:
        if not _remove_path(plan.purge_hf_cache_path, printer=printer):
            all_ok = False

    if plan.purge_lemonade:
        _remove_lemonade(printer=printer)

    _print(printer=printer)
    if all_ok:
        _print("gaia uninstall: done.", printer=printer)
        return EXIT_OK

    _print(
        "gaia uninstall: completed with errors (see messages above).",
        printer=printer,
    )
    return EXIT_FS_ERROR


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run(
    args: argparse.Namespace,
    *,
    home: Optional[Path] = None,
    printer: Callable[[str], None] = print,
    input_fn: Callable[[str], str] = input,
) -> int:
    """Run ``gaia uninstall`` from a parsed argparse namespace.

    Kept as a thin wrapper so tests can feed it a crafted namespace plus
    fake I/O functions without touching the real shell.
    """
    venv: bool = bool(getattr(args, "venv", False))
    purge: bool = bool(getattr(args, "purge", False))
    purge_lemonade: bool = bool(getattr(args, "purge_lemonade", False))
    purge_models: bool = bool(getattr(args, "purge_models", False))
    purge_hf_cache: bool = bool(getattr(args, "purge_hf_cache", False))
    dry_run: bool = bool(getattr(args, "dry_run", False))
    yes: bool = bool(getattr(args, "yes", False))

    # Validate: extras require --purge.
    if purge_lemonade and not purge:
        _print(
            "error: --purge-lemonade requires --purge.",
            printer=printer,
        )
        _print(
            "       Re-run as: gaia uninstall --purge --purge-lemonade",
            printer=printer,
        )
        return EXIT_FS_ERROR

    if purge_models and not purge:
        _print(
            "error: --purge-models requires --purge.",
            printer=printer,
        )
        _print(
            "       Re-run as: gaia uninstall --purge --purge-models",
            printer=printer,
        )
        return EXIT_FS_ERROR

    if purge_hf_cache and not purge:
        _print(
            "error: --purge-hf-cache requires --purge.",
            printer=printer,
        )
        _print(
            "       Re-run as: gaia uninstall --purge --purge-hf-cache",
            printer=printer,
        )
        return EXIT_FS_ERROR

    # No flags at all → friendly help (always exit 0, never crash even when
    # stdin is closed).
    if not (
        venv or purge or purge_lemonade or purge_models or purge_hf_cache or dry_run
    ):
        _print_no_flags_help(printer=printer)
        return EXIT_OK

    plan = build_plan(
        venv=venv,
        purge=purge,
        purge_lemonade=purge_lemonade,
        purge_models=purge_models,
        purge_hf_cache=purge_hf_cache,
        home=home,
    )

    if plan.is_empty() and dry_run:
        _print("[dry-run] Nothing to do — no tier or extras selected.", printer=printer)
        return EXIT_OK

    if plan.is_empty():
        _print_no_flags_help(printer=printer)
        return EXIT_OK

    _print_plan(plan, dry_run=dry_run, printer=printer)

    if dry_run:
        _print("[dry-run] No files were modified.", printer=printer)
        return EXIT_OK

    if not _should_skip_prompt(yes):
        if not _confirm(input_fn=input_fn):
            _print("Aborted. Nothing was removed.", printer=printer)
            return EXIT_ABORTED

    return execute_plan(plan, printer=printer)


def register_subparser(
    subparsers: "argparse._SubParsersAction",
    parent_parser: Optional[argparse.ArgumentParser] = None,
) -> argparse.ArgumentParser:
    """Attach the ``uninstall`` subcommand to ``subparsers``.

    Called from ``gaia.cli`` so the CLI registration stays close to every
    other subcommand while the implementation lives in this module.
    """
    parents = [parent_parser] if parent_parser is not None else []
    parser = subparsers.add_parser(
        "uninstall",
        help="Uninstall GAIA components (tiered cleanup of ~/.gaia and caches)",
        description=(
            "Tiered cleanup of the GAIA Python install. "
            "Default (no flags) prints a help message and exits. "
            "Use --dry-run first to preview what would be removed."
        ),
        parents=parents,
    )
    parser.add_argument(
        "--venv",
        action="store_true",
        help="Tier 2: remove ~/.gaia/venv/",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help=(
            "Tier 3: remove venv + chat + documents + electron config + "
            "install logs. Implies --venv."
        ),
    )
    parser.add_argument(
        "--purge-lemonade",
        action="store_true",
        dest="purge_lemonade",
        help="Also uninstall Lemonade Server (requires --purge).",
    )
    parser.add_argument(
        "--purge-models",
        action="store_true",
        dest="purge_models",
        help="Also remove the Lemonade models cache (requires --purge).",
    )
    parser.add_argument(
        "--purge-hf-cache",
        action="store_true",
        dest="purge_hf_cache",
        help=(
            "Also remove the HuggingFace hub cache "
            "(~/.cache/huggingface/hub or $HF_HOME/hub). Requires --purge. "
            "Restores the cleanup behavior of the legacy "
            "`gaia uninstall --models` flag from before v0.17.2."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would be removed without touching the filesystem.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=(
            "Skip the interactive confirmation prompt. Also auto-skipped "
            "when stdin is not a TTY."
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone entry point, useful for ``python -m`` style invocation."""
    parser = argparse.ArgumentParser(
        prog="gaia uninstall",
        description="Tiered cleanup of the GAIA Python install.",
    )
    parser.add_argument("--venv", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--purge-lemonade", action="store_true", dest="purge_lemonade")
    parser.add_argument("--purge-models", action="store_true", dest="purge_models")
    parser.add_argument("--purge-hf-cache", action="store_true", dest="purge_hf_cache")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    parser.add_argument("--yes", "-y", action="store_true")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
